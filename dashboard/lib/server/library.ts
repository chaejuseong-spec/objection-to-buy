// 보관함: 올린 리포트마다 폴더 하나. 서버(Node)에서만 쓴다.
//
// library/<id>/
//   report.pdf     올린 원문
//   meta.json      파일 이름, 올린 시각, 비용 미리보기
//   job.json       분석을 시작한 시각과 프로세스 번호
//   run/           파이썬 에이전트의 결과 (extraction.json … result.json)
//   done.json      끝났을 때 성공 여부와 오류 문구
//   log.txt        파이썬 출력 (문제가 생겼을 때 보는 곳)
//   note.json      사람이 적은 메모
// library/_order.json  사람이 정한 목록 순서 (없으면 올린 시각의 최신순)
// library/_trash/      지운 리포트를 옮겨 두는 곳 (실수로 지웠을 때 되살릴 수 있게)
import "server-only";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, openSync, readFileSync } from "node:fs";
import { cp, mkdir, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import type { Estimate, JobState, LibraryEntry, Stage } from "@/lib/types";

const DASHBOARD = process.cwd();
export const PROJECT = path.resolve(DASHBOARD, "..");
export const LIBRARY = path.join(PROJECT, "library");

/** AI 회사. config.yaml 의 provider 와 상관없이 웹에서는 이 값을 쓴다. 바꾸려면 여기만 고친다 ("claude" 또는 "gemini"). */
export const PROVIDER = "claude";
const PYTHON = process.env.TRIBUNAL_PYTHON || "python";
const DEMO_ID = "demo-soil";

const ID_RE = /^[a-z0-9-]{3,40}$/;
export const isValidId = (id: string) => ID_RE.test(id);
const dir = (id: string) => path.join(LIBRARY, id);
export const entryDir = dir;

export async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(file, "utf-8")) as T;
  } catch {
    return null;
  }
}
export const writeJson = (file: string, obj: unknown) => writeFile(file, JSON.stringify(obj, null, 1), "utf-8");

interface Meta {
  id: string;
  fileName: string;
  uploadedAt: string;
  size: number;
  demo?: boolean;
  estimate?: Estimate | null;
}
/** 어떤 코드와 모델로 분석했는지. 채점 점수를 버전끼리 비교할 때 쓴다. */
export interface RunVersion { commit: string; dirty: boolean; model: string }
export interface Job { startedAt: string; pid: number; mock?: boolean; version?: RunVersion }

/** 지금 코드의 git 기록 번호와 config.yaml 의 모델 이름 (git 이 없으면 '기록 없음') */
export function currentVersion(): RunVersion {
  const git = (args: string[]) => {
    const r = spawnSync("git", args, { cwd: PROJECT, encoding: "utf-8", windowsHide: true });
    return r.status === 0 ? r.stdout.trim() : null;
  };
  const commit = git(["rev-parse", "--short", "HEAD"]) ?? "기록 없음";
  const dirty = !!git(["status", "--porcelain"]);
  let model = "";
  try {
    const cfg = readFileSync(path.join(PROJECT, "config.yaml"), "utf-8");
    model = cfg.match(new RegExp(String.raw`\n\s+${PROVIDER}:[\s\S]*?\n\s+model:\s*([^\s#]+)`))?.[1] ?? "";
  } catch { /* 설정을 못 읽으면 비워 둔다 */ }
  return { commit, dirty, model };
}
interface Done { ok: boolean; error?: string; finished_at?: string }

/** 처음 열 때 기존 대시보드 데이터(데모)를 보관함 첫 항목으로 복사한다. 원본은 그대로 둔다. */
async function seed() {
  if (existsSync(LIBRARY)) return;
  await mkdir(LIBRARY, { recursive: true });
  const result = path.join(DASHBOARD, "data", "result.json");
  const pdf = path.join(DASHBOARD, "public", "report.pdf");
  if (!existsSync(result)) return;
  const d = dir(DEMO_ID);
  await mkdir(path.join(d, "run"), { recursive: true });
  await cp(result, path.join(d, "run", "result.json"));
  if (existsSync(pdf)) await cp(pdf, path.join(d, "report.pdf"));
  try { // 같은 실행의 평가기록지가 남아 있으면 같이 가져온다
    const runId = (JSON.parse(await readFile(result, "utf-8")) as { run?: { id?: string } }).run?.id;
    const md = runId ? path.join(PROJECT, ".tribunal", runId, "평가기록지.md") : "";
    if (md && existsSync(md)) await cp(md, path.join(d, "run", "평가기록지.md"));
  } catch { /* 없으면 내려받기 단추가 안 보인다 */ }
  const size = existsSync(pdf) ? (await stat(pdf)).size : 0;
  await writeJson(path.join(d, "meta.json"), {
    id: DEMO_ID, fileName: "데모 리포트.pdf", uploadedAt: "2026-03-10T00:00:00", size, demo: true, estimate: null,
  } satisfies Meta);
  await writeJson(path.join(d, "done.json"), { ok: true } satisfies Done);
}

function alive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

const STAGES: { label: string; done: (files: string[]) => boolean }[] = [
  { label: "원문을 읽고 주장과 숫자 뽑기", done: (f) => f.includes("extraction.json") },
  { label: "뽑은 내용을 원문과 대조", done: (f) => f.includes("claims.json") },
  { label: "리스크 검토자가 이의 제기", done: (f) => f.includes("prosecutor.json") },
  { label: "배심원 투표", done: (f) => f.filter((x) => x.startsWith("juror_")).length >= 3 },
  { label: "판사 판결문", done: (f) => f.includes("judge.json") },
  { label: "결과 정리와 원문 표시", done: (f) => f.includes("result.json") },
];

async function stages(id: string): Promise<Stage[]> {
  let files: string[] = [];
  try {
    files = await readdir(path.join(dir(id), "run"));
  } catch { /* 아직 없음 */ }
  return STAGES.map((s) => ({ label: s.label, done: s.done(files) }));
}

export async function readEntry(id: string): Promise<LibraryEntry | null> {
  const d = dir(id);
  const meta = await readJson<Meta>(path.join(d, "meta.json"));
  if (!meta) return null;
  const job = await readJson<Job>(path.join(d, "job.json"));
  const done = await readJson<Done>(path.join(d, "done.json"));
  const result = await readJson<{ meta: Record<string, string>; grade: { value: string }; run: { mock: boolean; demo: boolean } }>(
    path.join(d, "run", "result.json"),
  );

  let state: JobState = "ready";
  let error: string | undefined;
  if (done) {
    state = done.ok && result ? "done" : "failed";
    error = done.ok ? undefined : done.error ?? "분석이 실패했습니다.";
  } else if (job) {
    if (alive(job.pid)) state = "running";
    else {
      state = "failed";
      error = "분석이 중간에 멈췄습니다. 컴퓨터를 껐거나 프로그램이 강제로 닫혔을 수 있습니다. 다시 시도해 주세요.";
    }
  }

  return {
    id,
    fileName: meta.fileName,
    uploadedAt: meta.uploadedAt,
    size: meta.size,
    demo: !!meta.demo || !!result?.run.demo,
    mock: !!result?.run.mock,
    estimate: meta.estimate ?? null,
    state,
    error,
    startedAt: job?.startedAt,
    finishedAt: done?.finished_at,
    stages: state === "running" || state === "failed" ? await stages(id) : [],
    hasRecord: state === "done" && existsSync(path.join(d, "run", "평가기록지.md")),
    company: result?.meta.name,
    ticker: result?.meta.ticker,
    reportDate: result?.meta.report_date,
    title: result?.meta.title,
    grade: result?.grade.value,
    note: (await readJson<{ text: string }>(path.join(d, "note.json")))?.text ?? "",
  };
}

/* ───── 메모와 목록 순서 (사람이 정한 것) ───── */
const ORDER = path.join(LIBRARY, "_order.json");
const TRASH = path.join(LIBRARY, "_trash");

export async function saveNote(id: string, text: string) {
  if (!(await readEntry(id))) throw new UserError("보관함에 없는 리포트입니다.");
  const clean = text.slice(0, 2000);
  await writeJson(path.join(dir(id), "note.json"), { text: clean, updatedAt: new Date().toISOString() });
  return clean;
}

export const hasCustomOrder = () => existsSync(ORDER);

/** 사람이 정한 순서를 저장한다. 비우면(null) 올린 시각의 최신순으로 되돌린다 */
export async function saveOrder(ids: string[] | null) {
  if (ids === null) { await rm(ORDER, { force: true }); return; }
  await writeJson(ORDER, { ids: ids.filter(isValidId), updatedAt: new Date().toISOString() });
}

export async function listEntries(): Promise<LibraryEntry[]> {
  await seed();
  const ids = (await readdir(LIBRARY, { withFileTypes: true })).filter((e) => e.isDirectory() && isValidId(e.name)).map((e) => e.name);
  const entries = (await Promise.all(ids.map(readEntry))).filter((e): e is LibraryEntry => !!e);
  entries.sort((a, b) => b.uploadedAt.localeCompare(a.uploadedAt));         // 기본: 올린 시각의 최신순
  const order = (await readJson<{ ids: string[] }>(ORDER))?.ids;
  if (!order) return entries;
  // 사람이 정한 순서. 순서를 정한 뒤 새로 올린 리포트는 맨 위에 둔다
  const rank = new Map(order.map((id, i) => [id, i]));
  const fresh = entries.filter((e) => !rank.has(e.id));
  const placed = entries.filter((e) => rank.has(e.id)).sort((a, b) => rank.get(a.id)! - rank.get(b.id)!);
  return [...fresh, ...placed];
}

export async function readResult(id: string): Promise<unknown | null> {
  return readJson(path.join(dir(id), "run", "result.json"));
}

export function pdfPath(id: string) {
  return path.join(dir(id), "report.pdf");
}

export function recordPath(id: string) {
  return path.join(dir(id), "run", "평가기록지.md");
}

/** 비용 0: 파이썬으로 쪽수와 호출 수, 토큰 상한만 계산한다. */
function estimate(pdf: string): Promise<Estimate | null> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, ["-m", "tribunal.web", "estimate", pdf, PROVIDER], {
      cwd: PROJECT, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    let out = "";
    child.stdout.on("data", (b) => (out += b.toString("utf-8")));
    child.on("error", () => resolve(null));
    child.on("close", () => {
      try {
        const line = out.trim().split(/\r?\n/).pop() ?? "";
        const j = JSON.parse(line);
        resolve(j.ok ? { calls: j.calls, inputTokens: j.input_tokens_max, outputTokens: j.output_tokens_max, provider: j.provider, model: j.model, pages: j.pages } : { error: j.error });
      } catch {
        resolve(null);
      }
    });
  });
}

const MAX_BYTES = 60 * 1024 * 1024;

export class UserError extends Error {}

export async function addUpload(file: File): Promise<LibraryEntry> {
  await seed();
  if (!file || typeof file.arrayBuffer !== "function") throw new UserError("파일이 오지 않았습니다.");
  if (file.size > MAX_BYTES) throw new UserError("파일이 너무 큽니다. 60MB 이하 PDF만 올릴 수 있습니다.");
  const buf = Buffer.from(await file.arrayBuffer());
  if (buf.subarray(0, 5).toString("latin1") !== "%PDF-") throw new UserError("PDF 파일만 올릴 수 있습니다.");

  const id = `r-${Date.now().toString(36)}`;
  const d = dir(id);
  await mkdir(d, { recursive: true });
  await writeFile(path.join(d, "report.pdf"), buf);
  const est = await estimate(path.join(d, "report.pdf"));
  const fileName = path.basename(file.name || "report.pdf").slice(0, 160);
  await writeJson(path.join(d, "meta.json"), { id, fileName, uploadedAt: new Date().toISOString(), size: buf.length, estimate: est } satisfies Meta);
  return (await readEntry(id))!;
}

export async function startRun(id: string, mock = false): Promise<LibraryEntry> {
  const all = await listEntries();
  const entry = all.find((e) => e.id === id);
  if (!entry) throw new UserError("보관함에 없는 리포트입니다.");
  if (entry.state === "running") return entry;
  const other = all.find((e) => e.state === "running");
  if (other) throw new UserError(`다른 리포트(${other.company ?? other.fileName})를 분석하는 중입니다. 끝난 뒤 시작해 주세요.`);
  if (entry.state === "done" && !entry.demo) throw new UserError("이미 분석이 끝난 리포트입니다.");
  if (entry.demo && !mock) throw new UserError("데모 리포트는 다시 분석하지 않습니다.");

  const d = dir(id);
  // 멈췄던 분석을 다시 시도할 때는 끝난 단계(run/)를 지우지 않는다. 파이썬이 저장된 단계를 건너뛰고 이어서 한다.
  const resume = entry.state === "failed" && !mock;
  if (!resume) await rm(path.join(d, "run"), { recursive: true, force: true });
  await rm(path.join(d, "done.json"), { force: true });
  const log = openSync(path.join(d, "log.txt"), resume ? "a" : "w");
  const args = ["-m", "tribunal.web", "run", d, PROVIDER, ...(mock ? ["--mock"] : [])];
  // detached: 웹사이트 창을 닫아도 분석은 끝까지 간다
  const child = spawn(PYTHON, args, {
    cwd: PROJECT, detached: true, windowsHide: true, stdio: ["ignore", log, log],
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
  });
  if (!child.pid) throw new UserError("분석 프로그램(파이썬)을 시작하지 못했습니다.");
  child.unref();
  await writeJson(path.join(d, "job.json"), { startedAt: new Date().toISOString(), pid: child.pid, mock, version: currentVersion() } satisfies Job);
  return (await readEntry(id))!;
}

export async function removeEntry(id: string) {
  const entry = await readEntry(id);
  if (!entry) return;
  if (entry.state === "running") throw new UserError("분석 중에는 지울 수 없습니다.");
  // 바로 지우지 않고 휴지통 폴더로 옮긴다 (library/_trash/<id>-<시각>). 보관함 목록에는 보이지 않는다
  await mkdir(TRASH, { recursive: true });
  const to = path.join(TRASH, `${id}-${Date.now()}`);
  try {
    await rename(dir(id), to);
  } catch {                                       // 다른 프로그램이 파일을 잡고 있으면 복사한 뒤 지운다
    await cp(dir(id), to, { recursive: true });
    await rm(dir(id), { recursive: true, force: true });
  }
}
