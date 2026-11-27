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
    grade: gradeName(result?.grade.value),
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
  return upgradeResult(await readJson(path.join(dir(id), "run", "result.json")));
}

/* 2026-11-20 전 결과(표: 타당/과장/확인 불가, 등급: 재심리)를 새 이름으로 읽는다. 파일은 고치지 않는다.
   예전 '과장'은 "맞지만 영향이 작다"는 뜻이라 동의 + 중요도 하로 읽는다. */
const OLD_VOTE: Record<string, string> = { 타당: "동의", 과장: "동의", "확인 불가": "판단 불가" };
export const gradeName = (g?: string) => (g === "재심리" ? "재검토 필요" : g);
type Obj = { doubt_type?: string; tally?: Record<string, unknown>; votes?: { vote: string; importance?: string | null }[]; verdict_reason?: string; importance?: string | null };
function upgradeObjection(o: Obj) {
  const t = o.tally;
  if (t && "타당" in t) {
    const ok = Number(t["타당"] ?? 0), small = Number(t["과장"] ?? 0);
    o.tally = { 동의: ok + small, 비동의: 0, "판단 불가": t["확인 불가"], 기권: t["기권"] ?? 0,
                중요도: { 상: 0, 중: 0, 하: small, 미확인: ok } };
  }
  for (const v of o.votes ?? []) {
    if (v.vote === "과장") v.importance = "하";
    v.vote = OLD_VOTE[v.vote] ?? v.vote; v.importance ??= null;
  }
  if (o.doubt_type === "빠진 위험") o.doubt_type = "위험 누락";   // 2026-11-20 이름 변경
  o.verdict_reason ??= "";
  o.importance ??= null;
}
function upgradeResult<T>(r: T): T {
  const x = r as unknown as { grade?: { value: string }; claims?: { objections: Obj[] }[]; other_objections?: Obj[] } | null;
  if (!x) return r;
  if (x.grade) x.grade.value = gradeName(x.grade.value) ?? x.grade.value;
  for (const c of x.claims ?? []) c.objections.forEach(upgradeObjection);
  (x.other_objections ?? []).forEach(upgradeObjection);
  return r;
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

/** 기본 종목정보의 시세를 새로 받는다 (KRX 의 빠진 거래일만, AI 없음, 몇 초). 재무가 7일보다 오래됐으면 통째로 다시 만든다.
 *  파이썬이 결과 파일의 company 칸을 고치고, 그 칸을 JSON 으로 돌려준다 */
export async function refreshCompany(id: string): Promise<unknown> {
  const run = path.join(dir(id), "run");
  if (!existsSync(path.join(run, "result.json"))) throw new UserError("분석 결과가 없습니다.");
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-m", "tribunal.external.company", run, "--refresh"], {
      cwd: PROJECT, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    let out = "";
    child.stdout.setEncoding("utf8");                    // 한글이 조각 사이에 걸려도 깨지지 않게 (2026-11-26)
    child.stdout.on("data", (b: string) => { out += b; });
    child.stderr.resume();
    const timer = setTimeout(() => { child.kill(); reject(new UserError("종목정보를 만드는 데 너무 오래 걸립니다.")); }, 300_000);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) return reject(new UserError("시세를 새로 받지 못했습니다."));
      try { resolve(JSON.parse(out.slice(out.indexOf("{")))); } catch { reject(new UserError("시세 응답을 읽지 못했습니다.")); }
    });
  });
}

/** 파이썬 한 번 돌려 JSON 받기 (종목 검색·주가 차트용) */
function runPyJson(args: string[], timeoutMs: number, fail: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-m", "tribunal.external.company", ...args], {
      cwd: PROJECT, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    let out = "";
    child.stdout.setEncoding("utf8");                    // 한글이 조각 사이에 걸려도 깨지지 않게 (2026-11-26)
    child.stdout.on("data", (b: string) => { out += b; });
    child.stderr.resume();
    const timer = setTimeout(() => { child.kill(); reject(new UserError(`${fail} (시간 초과)`)); }, timeoutMs);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) return reject(new UserError(fail));
      try { resolve(JSON.parse(out.slice(out.indexOf("{")))); } catch { reject(new UserError(fail)); }
    });
  });
}
const TICKER_RE = /^[0-9A-Z]{6}$/;

/** 지금 상장된 종목코드: 받아 둔 가장 최근 KRX 일별 시세(유가증권·코스닥)에 있는 종목. 파일이 없으면 빈 집합(거르지 않음) */
async function listedTickers(): Promise<Set<string>> {
  const dir = path.join(PROJECT, ".cache", "krx");
  const out = new Set<string>();
  try {
    const files = await readdir(dir);
    for (const ep of ["stk_bydd_trd", "ksq_bydd_trd"]) {
      const last = files.filter((f) => f.startsWith(`${ep}-`)).sort().at(-1);
      if (!last) continue;
      const rows: { ISU_CD?: string }[] = JSON.parse(await readFile(path.join(dir, last), "utf-8")).rows ?? [];
      rows.forEach((r) => { const c = String(r.ISU_CD ?? ""); out.add(c.length > 6 ? c.slice(3, 9) : c); });
    }
  } catch {
    return new Set();
  }
  return out;
}

/** 종목 검색 (2026-11-23): DART 상장사 목록에서 회사명·종목코드로. 이름이 그 글자로 시작하는 회사가 먼저 */
export async function searchStocks(q: string): Promise<{ ticker: string; name: string }[]> {
  const t = q.trim().replace(/\s+/g, "").toLowerCase();
  if (!t) return [];
  let list: { corp_name: string; stock_code: string }[] = [];
  try {
    list = JSON.parse(await readFile(path.join(PROJECT, ".cache", "dart", "corpcode.json"), "utf-8")).list ?? [];
  } catch {
    throw new UserError("상장사 목록이 아직 없습니다. 리포트를 한 번 분석하면 만들어집니다.");
  }
  const listed = await listedTickers();
  const hits = list.filter((c) => (!listed.size || listed.has(c.stock_code))        // 상장폐지 종목은 뺀다 (2026-11-25 사용자)
    && (c.stock_code.includes(t) || c.corp_name.replace(/\s+/g, "").toLowerCase().includes(t)));
  const rank = (c: { corp_name: string; stock_code: string }) =>
    (c.stock_code === t || c.corp_name.toLowerCase() === t ? 0 : c.corp_name.toLowerCase().startsWith(t) || c.stock_code.startsWith(t) ? 1 : 2) * 100 + c.corp_name.length;
  return hits.sort((a, b) => rank(a) - rank(b)).slice(0, 12).map((c) => ({ ticker: c.stock_code, name: c.corp_name }));
}

/** 금감원 회사 이름 → 지금 상장된 종목코드 (2026-11-26 사용자 B안). 못 찾으면 null (= 비상장으로 표시)
 *  표기 차이: 케이비=KB 등 한글 표기, '○○금융지주회사'=○○지주, 바뀐 이름(디지비→iM) */
const SPELL: [string, string][] = [["케이비", "KB"], ["엔에이치", "NH"], ["비엔케이", "BNK"], ["디지비", "DGB"], ["제이비", "JB"], ["아이비케이", "IBK"],
  ["아이엠", "iM"], ["에스케이", "SK"], ["케이티", "KT"], ["엘지", "LG"], ["지에스", "GS"], ["디비", "DB"], ["케이", "K"]];
const RENAMED: Record<string, string> = { DGB금융지주: "IM금융지주", 한국투자금융지주: "한국금융지주", 중소기업은행: "기업은행" };
function normCo(n: string): string {
  let s = n.replace(/\s|주식회사|\(주\)|㈜/g, "").replace(/회사$/, "").replace(/보험$/, "");   // 삼성생명보험 = 삼성생명
  for (const [a, b] of SPELL) s = s.split(a).join(b);
  s = s.toUpperCase();
  return (RENAMED[s] ?? s).toUpperCase();
}
export async function matchListed(names: string[]): Promise<Record<string, string | null>> {
  let list: { corp_name: string; stock_code: string }[] = [];
  try {
    list = JSON.parse(await readFile(path.join(PROJECT, ".cache", "dart", "corpcode.json"), "utf-8")).list ?? [];
  } catch {
    return Object.fromEntries(names.map((n) => [n, null]));
  }
  const listed = await listedTickers();
  const by = new Map<string, string>();
  for (const c of list) {
    if (!TICKER_RE.test(c.stock_code) || (listed.size && !listed.has(c.stock_code))) continue;
    const k = normCo(c.corp_name);
    if (!by.has(k)) by.set(k, c.stock_code);
    if (k.endsWith("금융지주")) by.set(k.replace(/금융지주$/, "지주"), by.get(k.replace(/금융지주$/, "지주")) ?? c.stock_code);
  }
  return Object.fromEntries(names.map((n) => {
    const k = normCo(n);
    const tries = [k, k.replace(/금융지주$/, "지주"), k.endsWith("금융지주") ? k.replace(/지주$/, "") : "",   // KB금융지주 = KB금융
      k.replace(/해상화재$/, "해상"), k.replace(/화재해상$/, "화재"), k.replace(/재$/, "")];                // 현대해상화재 = 현대해상, 흥국화재해상 = 흥국화재, 코리안리재 = 코리안리
    return [n, tries.map((x) => (x ? by.get(x) : undefined)).find(Boolean) ?? null];
  }));
}

/** 같은 업종의 비상장 정기보고서 제출사 (DART). 업종 목록이 아직 없으면 빈 목록 */
export async function unlistedPeers(induty: string): Promise<{ corp_code: string; name: string }[]> {
  if (!/^[0-9A-Z]{2,6}$/.test(induty)) return [];
  let list: Record<string, { name: string; induty: string }> = {};
  try {
    list = JSON.parse(await readFile(path.join(PROJECT, ".cache", "dart", "industry_unlisted.json"), "utf-8")).list ?? {};
  } catch {
    return [];
  }
  const all = Object.entries(list).map(([corp_code, v]) => ({ corp_code, ...v }));
  for (const n of [5, 4, 3]) {                       // 같은 5자리 업종, 모자라면 앞 4·3자리
    const hit = all.filter((x) => x.induty && induty.length >= n && x.induty.slice(0, n) === induty.slice(0, n));
    if (hit.length >= 3 || n === 3) return hit.sort((a, b) => a.name.localeCompare(b.name, "ko")).slice(0, 40).map(({ corp_code, name }) => ({ corp_code, name }));
  }
  return [];
}

/** 검색한 종목의 '내 차트' (종목마다, library/_stocks/<종목코드>/charts.json) */
/** 모든 회사 공통 차트 (2026-11-24, 사용자): 어느 회사를 열든 '저장된 차트'에 나온다. 지표 구성만 저장 */
const COMMON_CHARTS = path.join(LIBRARY, "_common", "charts.json");
export async function readCommonCharts(): Promise<unknown[]> {
  try {
    const v = JSON.parse(await readFile(COMMON_CHARTS, "utf-8"));
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
export async function writeCommonCharts(charts: unknown): Promise<void> {
  if (!Array.isArray(charts) || charts.length > 40) throw new UserError("차트는 40개까지 저장할 수 있습니다.");
  const body = JSON.stringify(charts, null, 1);
  if (body.length > 200_000) throw new UserError("차트 설정이 너무 큽니다.");
  await mkdir(path.dirname(COMMON_CHARTS), { recursive: true });
  const tmp = `${COMMON_CHARTS}.tmp`;
  await writeFile(tmp, body, "utf-8");
  await rename(tmp, COMMON_CHARTS);
}

export async function readStockCharts(ticker: string): Promise<unknown[]> {
  if (!TICKER_RE.test(ticker)) return [];
  try {
    const v = JSON.parse(await readFile(path.join(LIBRARY, "_stocks", ticker, "charts.json"), "utf-8"));
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
export async function writeStockCharts(ticker: string, charts: unknown): Promise<void> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  if (!Array.isArray(charts) || charts.length > 40) throw new UserError("차트는 40개까지 저장할 수 있습니다.");
  const body = JSON.stringify(charts, null, 1);
  if (body.length > 200_000) throw new UserError("차트 설정이 너무 큽니다.");
  const d = path.join(LIBRARY, "_stocks", ticker);
  await mkdir(d, { recursive: true });
  const tmp = path.join(d, "charts.json.tmp");
  await writeFile(tmp, body, "utf-8");
  await rename(tmp, path.join(d, "charts.json"));
}

/** 리포트 없이 검색한 종목의 기본 종목정보 (처음 1~2분, 7일 저장, refresh 면 시세만 새로) */
export async function stockCompany(ticker: string, refresh = false): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  return runPyJson(["--stock", ticker, ...(refresh ? ["--refresh"] : [])], 300_000, "종목정보를 만들지 못했습니다.");
}

/** 별도(회사 단독) 기준 재무제표 (화면에서 '별도'를 누를 때, 7일 저장) */
export async function stockSeparate(ticker: string): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  return runPyJson(["--separate", ticker], 300_000, "별도 기준 재무를 받지 못했습니다.");
}

/** 시장 화면 (2026-11-24): 지수·국채 금리·환율·시장 투자자별 매매대금·섹터 등락 (토스증권). 10분 저장 */
export async function marketData(): Promise<unknown> {
  return runPyJson(["--market"], 180_000, "시장 자료를 받지 못했습니다.");
}

/** 토스 섹터 (2026-11-24): 종목이 속한 섹터들, 등락률, 구성 종목 시가총액 순위. 색인은 7일(처음 2~3분), 화면 자료는 6시간 저장 */
export async function stockSector(ticker: string): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  return runPyJson(["--sector", ticker], 400_000, "섹터 자료를 받지 못했습니다.");
}

/** 경쟁사 표 현재가 (2026-11-26 사용자): 토스 최근 가격. 종목당 1번, 10분 저장 */
export async function lastPrices(tickers: string[]): Promise<unknown> {
  const ok = [...new Set(tickers.filter((t) => TICKER_RE.test(t)))].slice(0, 40);
  if (!ok.length) return { prices: {} };
  return runPyJson(["--last-prices", ok.join(",")], 120_000, "현재가를 받지 못했습니다.");
}

/** 섹터 직접 추가 (2026-11-26 사용자): 토스 섹터 전체 목록 / 종목에 붙인 섹터 저장 (저장 후 그 종목 섹터 자료를 다시 돌려준다) */
export async function sectorList(): Promise<unknown> {
  return runPyJson(["--sector-list"], 400_000, "섹터 목록을 받지 못했습니다.");
}
export async function setStockSectors(ticker: string, ids: string[]): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  const clean = ids.filter((x) => /^[0-9A-Za-z_-]{1,40}$/.test(x)).slice(0, 6);
  return runPyJson(["--sector-extra", ticker, clean.join(",")], 400_000, "섹터를 저장하지 못했습니다.");
}

/** 고른 토스 섹터의 경쟁사 표 (2026-11-25, 사용자 'A': 사업이 여럿인 회사는 섹터 버튼으로 경쟁사 표도 바꾼다). 섹터마다 7일 저장 */
export async function stockPeersSector(ticker: string, sector: string): Promise<unknown> {
  if (!TICKER_RE.test(ticker) || !/^[0-9A-Za-z_-]{1,40}$/.test(sector)) throw new UserError("종목코드나 섹터가 올바르지 않습니다.");
  return runPyJson(["--peers-sector", ticker, sector], 300_000, "그 섹터의 경쟁사 표를 만들지 못했습니다.");
}

/** 수급 (2026-11-24): 토스증권 투자자별·프로그램·공매도·신용·대차 일별 1년, 매수 유의사항. 30분 저장 */
export async function stockFlows(ticker: string): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  return runPyJson(["--flows", ticker], 180_000, "수급 자료를 받지 못했습니다.");
}

/** 주가 차트 자료 (토스증권 수정주가 일봉, 못 받으면 KRX) */
export async function stockHistory(ticker: string): Promise<unknown> {
  if (!TICKER_RE.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  return runPyJson(["--history", ticker, "260"], 300_000, "주가 자료를 받지 못했습니다.");
}

/** 차트 만들기 (2026-11-23): 리포트마다 저장한 차트 카드. 화면 설정(지표 키·회사·함께 보기·기간)만 저장하고 숫자는 저장하지 않는다 */
export async function readCharts(id: string): Promise<unknown[]> {
  try {
    const v = JSON.parse(await readFile(path.join(dir(id), "charts.json"), "utf-8"));
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
export async function writeCharts(id: string, charts: unknown): Promise<void> {
  if (!Array.isArray(charts) || charts.length > 40) throw new UserError("차트는 40개까지 저장할 수 있습니다.");
  const ok = charts.every((x) => x && typeof x === "object" && typeof (x as { id?: unknown }).id === "string"
    && Array.isArray((x as { keys?: unknown }).keys) && Array.isArray((x as { peers?: unknown }).peers));
  if (!ok) throw new UserError("차트 설정이 올바르지 않습니다.");
  const body = JSON.stringify(charts, null, 1);
  if (body.length > 200_000) throw new UserError("차트 설정이 너무 큽니다.");
  if (!existsSync(dir(id))) throw new UserError("리포트를 찾지 못했습니다.");
  const tmp = path.join(dir(id), "charts.json.tmp");
  await writeFile(tmp, body, "utf-8");
  await rename(tmp, path.join(dir(id), "charts.json"));
}

/** 화면의 표를 엑셀 파일로 (사용자 서식 규칙, tribunal/external/excel.py). 숫자는 화면이 보낸 그대로 */
export async function exportXlsx(payload: unknown): Promise<Buffer> {
  const body = JSON.stringify(payload);
  if (body.length > 2_000_000) throw new UserError("내보낼 자료가 너무 큽니다.");
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-m", "tribunal.external.excel"], {
      cwd: PROJECT, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    const out: Buffer[] = [];
    child.stdout.on("data", (b: Buffer) => out.push(b));
    child.stderr.resume();
    const timer = setTimeout(() => { child.kill(); reject(new UserError("엑셀 파일을 만드는 데 너무 오래 걸립니다.")); }, 60_000);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) return reject(new UserError("엑셀 파일을 만들지 못했습니다."));
      resolve(Buffer.concat(out));
    });
    child.stdin.end(body, "utf-8");
  });
}

/** 동종사 하나의 재무 추이 (화면에서 체크할 때). 파이썬이 7일 저장해 두므로 두 번째부터는 빠르다 */
export async function peerSeries(ticker: string, fisis?: string, corp?: string): Promise<unknown> {
  // fisis = "H:0010597" (금감원 업종 코드:회사 코드) 이면 금감원 자료(비상장 포함), corp = DART 고유번호(비상장 정기보고서 제출사), 아니면 상장사
  const m = fisis ? /^([A-Z]):([0-9]{5,10})$/.exec(fisis) : null;
  if (fisis && !m) throw new UserError("회사 코드가 올바르지 않습니다.");
  if (corp && !/^[0-9]{8}$/.test(corp)) throw new UserError("회사 코드가 올바르지 않습니다.");
  if (!m && !corp && !/^[0-9A-Z]{6}$/.test(ticker)) throw new UserError("종목코드가 올바르지 않습니다.");
  const args = m ? ["--fisis-peer", m[1], m[2]] : corp ? ["--peer-corp", corp] : ["--peer", ticker];
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-m", "tribunal.external.company", ...args], {
      cwd: PROJECT, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    let out = "";
    child.stdout.setEncoding("utf8");                    // 한글이 조각 사이에 걸려도 깨지지 않게 (2026-11-26)
    child.stdout.on("data", (b: string) => { out += b; });
    child.stderr.resume();
    const timer = setTimeout(() => { child.kill(); reject(new UserError("동종사 자료를 받는 데 너무 오래 걸립니다.")); }, 300_000);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) return reject(new UserError("동종사 자료를 받지 못했습니다."));
      try { resolve(JSON.parse(out.slice(out.indexOf("{")))); } catch { reject(new UserError("동종사 자료를 읽지 못했습니다.")); }
    });
  });
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
