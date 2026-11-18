// 채점 파일 저장. 서버(Node)에서만 쓴다.
//
// eval/answers/<PDF 지문>.json   정답지 (같은 PDF를 다시 올려도 같은 정답지)
// eval/scores.jsonl              기록한 점수 (한 줄에 하나, 버전끼리 비교용)
// library/<id>/grading.json      분석 한 번에 대한 채점 표시
// eval/ 은 리포트 내용이 들어가므로 git 에 올리지 않는다.
import "server-only";
import { createHash } from "node:crypto";
import { appendFile, mkdir, readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { EMPTY_GRADING, EMPTY_KEY, type AnswerItem, type AnswerKey, type Grading, type QualityRun, type ScoreRecord, type Score } from "@/lib/grading";
import { entryDir, PROJECT, readJson, writeJson, type Job } from "./library";

const EVAL = path.join(PROJECT, "eval");
const SCORES = path.join(EVAL, "scores.jsonl");
const hashes = new Map<string, { mtime: number; hash: string }>();

/** PDF 내용으로 만든 지문. 파일 이름이 달라도 같은 PDF면 같다. */
async function pdfHash(id: string): Promise<string> {
  const file = path.join(entryDir(id), "report.pdf");
  const { mtimeMs } = await stat(file);
  const hit = hashes.get(id);
  if (hit && hit.mtime === mtimeMs) return hit.hash;
  const hash = createHash("sha256").update(await readFile(file)).digest("hex").slice(0, 16);
  hashes.set(id, { mtime: mtimeMs, hash });
  return hash;
}

const answerPath = (hash: string) => path.join(EVAL, "answers", `${hash}.json`);

async function history(hash: string): Promise<ScoreRecord[]> {
  try {
    const lines = (await readFile(SCORES, "utf-8")).split(/\r?\n/).filter(Boolean);
    return lines.map((l) => JSON.parse(l) as ScoreRecord & { pdf?: string }).filter((r) => r.pdf === hash).reverse();
  } catch {
    return [];
  }
}

const fileHashes = new Map<string, { mtime: number; hash: string }>();
async function fileHash(file: string): Promise<string | null> {
  try {
    const { mtimeMs } = await stat(file);
    const hit = fileHashes.get(file);
    if (hit && hit.mtime === mtimeMs) return hit.hash;
    const hash = createHash("sha256").update(await readFile(file)).digest("hex").slice(0, 16);
    fileHashes.set(file, { mtime: mtimeMs, hash });
    return hash;
  } catch {
    return null;
  }
}

/** 자동 오류 삽입 테스트(python -m tribunal.quality) 결과 가운데 이 PDF로 만든 세트의 것 */
async function qualityRuns(hash: string): Promise<QualityRun[]> {
  const SETS = path.join(EVAL, "quality", "sets"), RUNS = path.join(EVAL, "quality", "runs");
  let sets: string[] = [], files: string[] = [];
  try { sets = await readdir(SETS); } catch { return []; }
  const mine = new Set<string>();
  for (const s of sets) if ((await fileHash(path.join(SETS, s, "clean.pdf"))) === hash) mine.add(s);
  if (!mine.size) return [];
  try { files = (await readdir(RUNS)).filter((f) => f.endsWith(".json")).sort().reverse(); } catch { return []; }
  const out: QualityRun[] = [];
  for (const f of files) {
    const r = await readJson<{ at: string; set: string; model: string; version: { commit: string; dirty: boolean }; metrics: QualityRun["metrics"] }>(path.join(RUNS, f));
    if (r && mine.has(r.set)) out.push({ at: r.at, set: r.set, model: r.model, version: r.version, metrics: r.metrics });
  }
  return out.slice(0, 20);
}

export async function readGrading(id: string) {
  const hash = await pdfHash(id);
  const answer = (await readJson<AnswerKey>(answerPath(hash))) ?? EMPTY_KEY;
  const grading = (await readJson<Grading>(path.join(entryDir(id), "grading.json"))) ?? EMPTY_GRADING;
  return { answer, grading, history: await history(hash), quality: await qualityRuns(hash) };
}

/* 화면에서 온 값을 정리한다 (길이 제한, 정해진 값만) */
const str = (v: unknown, max: number) => (typeof v === "string" ? v.slice(0, max) : "");
const pick = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T => (allowed.includes(v as T) ? (v as T) : fallback);
const ids = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, 50).map((x) => x.slice(0, 40)) : []);

function cleanKey(v: unknown, report: string): AnswerKey {
  const raw = (v as { items?: unknown[] })?.items;
  const items: AnswerItem[] = (Array.isArray(raw) ? raw : []).slice(0, 60).map((x, i) => {
    const o = (x ?? {}) as Record<string, unknown>;
    return {
      id: str(o.id, 20) || `A-${i + 1}`,
      claim: str(o.claim, 40),
      text: str(o.text, 2000),
      kind: pick(o.kind, ["사실 오류", "해석 차이", ""] as const, ""),
      weight: pick(o.weight, ["상", "중", "하"] as const, "중"),
    };
  });
  return { report, items, updatedAt: new Date().toISOString() };
}

function cleanGrading(v: unknown): Grading {
  const o = (v ?? {}) as Record<string, Record<string, unknown>>;
  const map = <T extends string>(m: unknown, allowed: readonly T[]) =>
    Object.fromEntries(Object.entries((m ?? {}) as Record<string, unknown>).slice(0, 200)
      .filter(([, x]) => allowed.includes(x as T)).map(([k, x]) => [k.slice(0, 40), x as T]));
  return {
    found: Object.fromEntries(Object.entries(o.found ?? {}).slice(0, 60).map(([k, x]) => [k.slice(0, 20), ids(x)])),
    objMarks: map(o.objMarks, ["valid", "miss"] as const),
    numberMarks: map(o.numberMarks, ["right", "wrong"] as const),
    updatedAt: new Date().toISOString(),
  };
}

export async function saveGrading(id: string, body: { answer?: unknown; grading?: unknown }, report: string) {
  const hash = await pdfHash(id);
  if (body.answer !== undefined) {
    await mkdir(path.join(EVAL, "answers"), { recursive: true });
    await writeJson(answerPath(hash), cleanKey(body.answer, report));
  }
  if (body.grading !== undefined) await writeJson(path.join(entryDir(id), "grading.json"), cleanGrading(body.grading));
  return readGrading(id);
}

/** 점수 한 줄을 기록한다. 어떤 코드·모델로 분석했는지(job.json)를 같이 남긴다. */
export async function recordScore(id: string, s: Score, report: string, mock: boolean) {
  const hash = await pdfHash(id);
  const job = await readJson<Job>(path.join(entryDir(id), "job.json"));
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const counts = Object.fromEntries(Object.entries(s.counts ?? {}).map(([k, v]) => [k, num(v) ?? 0])) as Score["counts"];
  const rec: ScoreRecord & { pdf: string } = {
    at: new Date().toISOString(), entryId: id, report, pdf: hash, version: job?.version ?? null, mock,
    recall: num(s.recall), weightedRecall: num(s.weightedRecall), missRate: num(s.missRate), numberAcc: num(s.numberAcc), counts,
  };
  await mkdir(EVAL, { recursive: true });
  await appendFile(SCORES, JSON.stringify(rec) + "\n", "utf-8");
  return history(hash);
}
