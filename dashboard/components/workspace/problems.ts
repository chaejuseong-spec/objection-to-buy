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

/** 주장을 누르면 열 곳: 그 주장에 걸린 지적 가운데 가장 무거운 것(같으면 동의 표가 많은 것), 없으면 숫자 문제 (2026-11-20 화면을 '지적' 하나로) */
export function claimTarget(result: Result, claimId: string | null): string | null {
  const c = result.claims.find((x) => x.id === claimId);
  if (!c) return null;
  const o = [...c.objections].sort((a, b) => (RANK[b.verdict] ?? 0) - (RANK[a.verdict] ?? 0) || (b.tally?.["동의"] ?? 0) - (a.tally?.["동의"] ?? 0))[0];
  return o?.id ?? c.code?.[0]?.check_id ?? null;
}

/** 원문에 칠할 표시: 문제 있는 곳만, 주장 문장은 칠하지 않는다(지적이 걸린 문장과 숫자만) */
export function shownMarks(result: Result) {
  // 숫자 표시는 지금 결과에 남아 있고 '일치'가 아닌 것만 (다시 검산해 지워지거나 일치로 바뀐 숫자의 남은 표시는 칠하지 않는다)
  const live = new Set(result.numbers.results.filter((x) => x.status !== OK_NUMBER).map((x) => x.check_id));
  return result.marks.filter((m) => !m.pass && m.kind !== "claim" && (m.kind !== "number" || live.has(m.ref)));
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

/** 중요도: 투자의견 핵심 주장에 걸렸는가 > 판정의 무게 > 배심원 '동의' 표 수.
 *  투자 판단(목표주가·밸류에이션)에 직접 닿는 문제가 위로 온다 */
export function importance(result: Result, p: Problem): number {
  const core = new Set(result.claims.filter((c) => c.is_core).map((c) => c.id));
  const onCore = p.kind === "logic" ? !!p.claimId && core.has(p.claimId) : p.claimIds.some((id) => core.has(id));
  const votes = p.kind === "logic" ? p.o.tally?.["동의"] ?? 0 : 9;      // 숫자 불일치는 코드가 확인한 것이라 표보다 앞
  return (onCore ? 1000 : 0) + (RANK[p.verdict] ?? 0) * 100 + votes;
}
// 말풍선에 줄 단위로 보인다 (배열의 한 칸이 한 줄)
export const IMPORTANCE_NOTE = [
  "중요도 순서",
  "1. 투자의견 핵심 주장에 걸린 문제",
  "2. 판정이 무거운 문제 (수정·불일치, 그다음 확인 필요)",
  "3. 배심원 '동의' 표가 많은 문제",
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

/** 리포트에서 옮긴 숫자 하나의 출처·확인 (2026-11-21). 코드가 이미 낸 검증 결과를 모으고, 검증하지 않은 숫자는 성격만 나눈다.
 *  외부 대조(KRX·ECOS·KOSIS·DART)를 다시 계산보다 앞에 보인다 */
const ORIGIN_NAME: Record<string, string> = { krx: "KRX 시세", ecos: "한국은행 ECOS", kosis: "KOSIS 통계", dart: "DART 공시" };
const OFFICIAL = new Set(Object.keys(ORIGIN_NAME));
// 이름에 평가·계산 결과를 뜻하는 말이 있어도 리포트 추정 (파이썬 dart_pool._CALC 와 같은 낱말, 2026-11-21)
const ESTIMATE = /20\d\dE|\dE\b|추정|전망|예상|목표|적정|가정|시나리오|민감도|역산|가치|SOTP|할인|조정|상승여력|괴리/;
const ARITH = /[×÷=+]|[\d)]\s*[−-]\s*\d|\d\s*[×x]\s*\d/;
const ACTUAL = /(19|20)\d\d(?!E)|FY\d{2,4}(?!E)|상반기|분기|월\s*말|말\b/;
export function numberOrigin(result: Result, row: { rid: string; item: string; as_of?: string; basis?: string }):
  { source: string; status: string | null; note: string; ref: string | null } {
  const all = result.numbers.results.filter((x) => x.stated_rid === row.rid || (x.rids ?? []).includes(row.rid));
  // 2026-11-21 사용자 결정: 이 표에서 '일치·불일치'는 그 숫자 자체를 공식 데이터(DART·KRX·ECOS·KOSIS)에서 그대로 찾은 것만.
  // 다시 계산한 것(식 검산, 공시 두 숫자로 나눈 비율, 계산에 쓴 숫자의 대조)은 공적인 확인처럼 보이지 않게 '리포트 추정'으로만 둔다
  const direct = all.filter((x) => OFFICIAL.has(x.kind ?? "") && !x.material && x.matched_by !== "ai_ratio");
  const pick = direct.find((x) => x.status !== "일치") ?? direct[0];
  if (pick) return { source: pick.via ?? ORIGIN_NAME[pick.kind!] ?? "공식 데이터", status: pick.status, note: "", ref: pick.check_id };
  const text = `${row.item} ${row.as_of ?? ""}`;
  if (all.length || ESTIMATE.test(text) || ESTIMATE.test(row.basis ?? "") || ARITH.test(row.basis ?? ""))
    return { source: "리포트 추정", status: null, note: "", ref: null };
  if (ACTUAL.test(text)) return { source: "회사 실적", status: null, note: "공시와 대조 안 함", ref: null };
  return { source: "리포트 표기", status: null, note: "대조 안 함", ref: null };
}
