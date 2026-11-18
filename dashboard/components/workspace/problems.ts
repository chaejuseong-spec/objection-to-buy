// 문제 목록: 논리 문제(배심원이 판정한 이의)와 숫자 문제(코드가 확인한 불일치)를 한 줄로 모은다.
// 통과한 것(일치, 유지)은 목록에 넣지 않는다. 계산은 파이썬이 이미 끝냈고 여기서는 모으고 정렬만 한다.
import type { NumberCheck, Objection, Result } from "@/lib/types";

export const OK_NUMBER = "일치";
export const UNVERIFIED_NUMBER = new Set(["검증 불가", "확인 불가"]);
const RANK: Record<string, number> = { 수정: 3, 불일치: 3, "확인 필요": 2, "판정 보류": 1, 유지: 0 };

export type Problem =
  | { kind: "logic"; id: string; verdict: string; page: number | null; o: Objection; claimId: string | null }
  | { kind: "number"; id: string; verdict: string; page: number | null; x: NumberCheck; claimIds: string[] };

/** 모든 이의 (주장에 묶인 것 + 묶이지 않은 것) */
export function allObjections(result: Result): (Objection & { claim_id: string | null })[] {
  const linked = result.claims.flatMap((c) => c.objections.map((o) => ({ ...o, claim_id: c.id })));
  const other = (result.other_objections ?? []).map((o) => ({ ...o, claim_id: null }));
  return [...linked, ...other];
}

export function findObjection(result: Result, id: string | null) {
  return id ? allObjections(result).find((o) => o.id === id) : undefined;
}

/** 숫자 검증 id -> 그 숫자를 근거로 쓴 주장 id들 (파이썬 rules.code_findings) */
function claimsOfCheck(result: Result): Map<string, string[]> {
  const m = new Map<string, string[]>();
  Object.entries(result.code_findings ?? {}).forEach(([cid, fs]) => fs.forEach((f) => m.set(f.check_id, [...(m.get(f.check_id) ?? []), cid])));
  return m;
}

/** 문제 목록 (중요도 순, 같으면 쪽 순). 유지된 이의와 일치한 숫자는 빠진다 */
export function problems(result: Result): Problem[] {
  const byCheck = claimsOfCheck(result);
  const out: Problem[] = [];
  allObjections(result).forEach((o) => {
    if (o.verdict === "유지") return;
    out.push({ kind: "logic", id: o.id, verdict: o.verdict, page: o.page ?? null, o, claimId: o.claim_id });
  });
  result.numbers.results.forEach((x) => {
    if (x.status !== "불일치") return;
    out.push({ kind: "number", id: x.check_id, verdict: "불일치", page: x.pages[0] ?? null, x, claimIds: byCheck.get(x.check_id) ?? [] });
  });
  return out.sort((a, b) => importance(result, b) - importance(result, a) || (a.page ?? 999) - (b.page ?? 999));
}

/** 중요도: 투자의견 핵심 주장에 걸렸는가 > 판정의 무게 > 배심원 '타당' 표 수.
 *  투자 판단(목표주가·밸류에이션)에 직접 닿는 문제가 위로 온다 */
export function importance(result: Result, p: Problem): number {
  const core = new Set(result.claims.filter((c) => c.is_core).map((c) => c.id));
  const onCore = p.kind === "logic" ? !!p.claimId && core.has(p.claimId) : p.claimIds.some((id) => core.has(id));
  const votes = p.kind === "logic" ? p.o.tally?.["타당"] ?? 0 : 9;      // 숫자 불일치는 코드가 확인한 것이라 표보다 앞
  return (onCore ? 1000 : 0) + (RANK[p.verdict] ?? 0) * 100 + votes;
}
// 말풍선에 줄 단위로 보인다 (배열의 한 칸이 한 줄)
export const IMPORTANCE_NOTE = [
  "중요도 순서",
  "1. 투자의견 핵심 주장에 걸린 문제",
  "2. 판정이 무거운 문제 (수정·불일치, 그다음 확인 필요)",
  "3. 배심원 '타당' 표가 많은 문제",
].join("\n");

/** 배심원이 받아들이지 않은 이의 (유지) */
export const keptObjections = (result: Result) => allObjections(result).filter((o) => o.verdict === "유지");
/** 공식 데이터나 식으로 확인하지 못한 숫자 */
export const unverifiedNumbers = (result: Result) => result.numbers.results.filter((x) => UNVERIFIED_NUMBER.has(x.status));

/** 숫자 문제의 출처 이름 */
export function numberSource(x: NumberCheck): string {
  return x.kind === "krx" ? "시장 데이터 대조" : x.kind === "ecos" ? "거시 통계 대조" : x.kind === "kosis" ? "산업 통계 대조"
    : x.kind === "dart" || x.source ? "공시 대조" : "다시 계산";
}
