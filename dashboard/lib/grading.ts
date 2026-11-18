// 채점(성적표): 사람이 쓴 정답지와 에이전트 결과를 맞춰 점수를 낸다. 화면과 서버가 같이 쓴다.
//
// 찾은 비율    = 정답지 문제 중 에이전트가 짚은 것 (중요도 가중: 상 3, 중 2, 하 1)
// 헛짚은 비율  = 채점한 에이전트 이의 중 '헛짚음'으로 표시한 것
// 숫자 정확도  = 채점한 숫자 검증 중 결과가 맞은 것
import type { Result } from "./types";

export type Weight = "상" | "중" | "하";
export type AnswerKind = "사실 오류" | "해석 차이" | "";
export interface AnswerItem { id: string; claim: string; text: string; kind: AnswerKind; weight: Weight }
/** 정답지: 같은 PDF면 다시 분석해도 같은 정답지를 쓴다 */
export interface AnswerKey { report?: string; items: AnswerItem[]; updatedAt?: string }

export type ObjMark = "valid" | "miss";
export type NumMark = "right" | "wrong";
/** 분석 한 번에 대한 채점 표시 */
export interface Grading {
  found: Record<string, string[]>;          // 정답 문제 id -> 그 문제를 짚은 에이전트 이의 id
  objMarks: Record<string, ObjMark>;        // 정답에 없는 이의: 타당한 추가 지적 / 헛짚음
  numberMarks: Record<string, NumMark>;     // 숫자 검증 결과가 맞았나
  updatedAt?: string;
}

export interface Score {
  recall: number | null;
  weightedRecall: number | null;
  missRate: number | null;
  numberAcc: number | null;
  counts: {
    answers: number; found: number; objections: number; hits: number; valid: number; miss: number;
    ungradedObjections: number; numbers: number; right: number; wrong: number; ungradedNumbers: number;
  };
}

export interface ScoreRecord extends Score {
  at: string;
  entryId: string;
  report: string;
  version: { commit: string; dirty: boolean; model: string } | null;
  mock: boolean;
}

/** 자동 오류 삽입 테스트 한 번의 결과 (파이썬 tribunal/quality) */
export interface QualityRun {
  at: string; set: string; model: string; version: { commit: string; dirty: boolean };
  metrics: {
    injected: number; checkable: number; caught: number; detect_rate_checkable: number | null; detect_rate_all: number | null;
    extracted_rate: number | null; missed_why: Record<string, number>; clean_mismatches: number | null; extra_false_alarms: number;
    unverifiable_rate: number | null; seconds: number; gemini_calls: number;
  };
}

export const EMPTY_KEY: AnswerKey = { items: [] };
export const EMPTY_GRADING: Grading = { found: {}, objMarks: {}, numberMarks: {} };
export const WEIGHT: Record<Weight, number> = { 상: 3, 중: 2, 하: 1 };

// 주장 번호는 화면과 같게 원문 쪽 순서 (views.claimsByPage 와 같은 규칙)
export const allObjections = (r: Result) => [...r.claims].sort((a, b) => (a.page ?? 999) - (b.page ?? 999))
  .flatMap((c, i) => c.objections.map((o) => ({ o, claimId: c.id, claimNo: i + 1 })));

const ratio = (a: number, b: number) => (b ? a / b : null);

export function score(result: Result, key: AnswerKey, g: Grading): Score {
  const objs = allObjections(result).map((x) => x.o.id);
  const items = key.items.filter((a) => a.text.trim());
  const foundItems = items.filter((a) => (g.found[a.id] ?? []).some((id) => objs.includes(id)));
  const hitIds = new Set(items.flatMap((a) => g.found[a.id] ?? []));
  const hits = objs.filter((id) => hitIds.has(id)).length;
  const rest = objs.filter((id) => !hitIds.has(id));
  const valid = rest.filter((id) => g.objMarks[id] === "valid").length;
  const miss = rest.filter((id) => g.objMarks[id] === "miss").length;
  const nums = result.numbers.results.map((n) => n.check_id);
  const right = nums.filter((id) => g.numberMarks[id] === "right").length;
  const wrong = nums.filter((id) => g.numberMarks[id] === "wrong").length;
  const wAll = items.reduce((t, a) => t + WEIGHT[a.weight], 0);
  const wFound = foundItems.reduce((t, a) => t + WEIGHT[a.weight], 0);
  return {
    recall: ratio(foundItems.length, items.length),
    weightedRecall: ratio(wFound, wAll),
    missRate: ratio(miss, hits + valid + miss),
    numberAcc: ratio(right, right + wrong),
    counts: {
      answers: items.length, found: foundItems.length, objections: objs.length, hits, valid, miss,
      ungradedObjections: rest.length - valid - miss, numbers: nums.length, right, wrong, ungradedNumbers: nums.length - right - wrong,
    },
  };
}

export const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v * 100)}%`);
