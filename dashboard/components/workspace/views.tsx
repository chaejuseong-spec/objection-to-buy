"use client";

// 분석이 끝난 리포트의 가운데 화면: 요약, 반대신문 과정, 나의 판단 기록
import type { FormEvent, ReactNode } from "react";
import type { Claim, Objection, Result } from "@/lib/types";
import { ClaimName, ClaimNumber, InfoTip, Mix, Verdict } from "./primitives";
import { IMPORTANCE_NOTE, keptObjections, numberSource, problems, unverifiedNumbers, type Problem } from "./problems";
import { Linked } from "./refs";
import { GRADE_TEXT, shownComputed, TONE, toneOf, toneVars, unitAfter } from "./tone";
import s from "./workspace.module.css";

export const num = (v?: string | null) => {
  const n = Number(String(v ?? "").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(n) && String(v ?? "").trim() !== "" ? n : null;
};
export function upside(meta: Result["meta"]) {
  const t = num(meta.target_price), c = num(meta.current_price);
  if (t === null || c === null || c === 0) return null;
  const v = ((t - c) / c) * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}
/** 주장을 원문 쪽 순서로 (같은 쪽이면 원래 순서). 화면의 '주장 n' 번호도 모두 이 순서를 따른다 */
export const claimsByPage = (claims: Claim[]) => [...claims].sort((a, b) => (a.page ?? 999) - (b.page ?? 999));
export const claimNo = (claims: Claim[], id: string) => claimsByPage(claims).findIndex((c) => c.id === id) + 1;

/** 요약 문장: 문제가 있는 것만 말한다 (유지된 주장, 0개인 항목은 빼고) */
// 주장 목록 설명 말풍선 (배열의 한 칸이 한 줄)
const CLAIMS_NOTE = [
  "원문 쪽 순서로 놓았습니다.",
  "주장을 누르면 원문 위치와 함께",
  "그 주장에 걸린 이의와 배심원 의견이 열립니다.",
].join("\n");

/** '투자 전에 직접 확인할 사항' 항목의 종류: 이의에서 나온 것은 사실 오류/해석 차이, 숫자 검증에서 나온 것은 수치 불일치 */
export function checkKind(it: { why: string; type: string }): string {
  if (!/^(O|J)-/.test(it.why)) return "수치 불일치";
  return it.type === "검증" ? "사실 오류" : "해석 차이";
}
/** 사용자에게 보이는 두 묶음: 논리(해석 차이) / 수치(사실 오류, 수치 불일치) */
export const checkGroup = (it: { why: string; type: string }): "logic" | "number" =>
  checkKind(it) === "해석 차이" ? "logic" : "number";

/** 요약 두 줄: 첫 줄은 '주장' 판정, 둘째 줄은 '문제점' 수 (주장 하나에 문제점이 여럿 붙을 수 있어 둘은 다르다) */
function summaryLines(nClaims: number, nFix: number, nCheck: number, nProblems: number): string[] {
  const parts = [nFix ? `${nFix}개는 수정` : "", nCheck ? `${nCheck}개는 확인` : ""].filter(Boolean);
  const first = parts.length
    ? `전체 주장 ${nClaims}개 중 ${parts.join(", ")}이 필요합니다.`
    : `전체 주장 ${nClaims}개 모두 큰 문제가 없습니다.`;
  return nProblems ? [first, `각 주장에 제시된 근거를 검토한 결과, 총 ${nProblems}건의 문제점이 발견되었습니다.`] : [first];
}

function PageLinks({ pages, onPage }: { pages: number[]; onPage: (p: number) => void }) {
  return (
    <span className={s.pageLinks}>
      {pages.map((p) => <button key={p} type="button" onClick={() => onPage(p)}>{p}쪽</button>)}
    </span>
  );
}

/* ───────── 리포트 요약 ───────── */

export function SummaryView({ result, onOpenClaim, onPage, checklist }: {
  result: Result; onOpenClaim: (id: string) => void; onPage: (p: number) => void;
  checklist?: ReactNode;      // 투자 전에 직접 확인할 사항 (맨 아래)
}) {
  const { meta, grade, claims, summary, logic } = result;
  const gTone = toneOf(grade.value);
  const up = upside(meta);
  const nProblems = problems(result).length;
  return (
    <article className={s.summary}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 className={s.summaryTitle}>{meta.title}</h2>
        <span className={s.muted}>{meta.report_date} 발간, 가격 기준일 {meta.snapshot_date}, 밸류에이션 {meta.valuation_method}</span>
      </div>

      <dl className={s.keyFigures}>
        <div><dt>투자의견</dt><dd>{meta.opinion}</dd></div>
        <div><dt>목표주가</dt><dd>{meta.target_price}<small>{unitAfter(String(meta.target_price ?? ""), "원")}</small></dd></div>
        <div><dt>현재주가</dt><dd>{meta.current_price}<small>{unitAfter(String(meta.current_price ?? ""), "원")}</small></dd></div>
        {up && <div><dt>상승여력</dt><dd>{up}</dd></div>}
      </dl>

      {/* 리포트가 무엇을 말하는지 먼저 (평가 없이 요약) -> 그다음 판정 */}
      <section className={s.thesis}>
        <h3>리포트 요약</h3>
        {summary.map((tx) => (
          <p key={tx.text} className={s.thesisItem}>
            {tx.text}
            <PageLinks pages={tx.pages} onPage={onPage} />
          </p>
        ))}
      </section>

      <section className={s.verdictBlock} style={toneVars(gTone)}>
        <h3>교차검증 결과</h3>
        <div className={s.gradeLine}>
          <strong>{grade.value}</strong>
          <span style={{ fontSize: 14 }}>{GRADE_TEXT[grade.value] ?? ""}</span>
        </div>
        <p>{summaryLines(logic.n_claims, logic.count["수정"], logic.count["확인 필요"], nProblems).map((l, i) => <span key={i} style={{ display: "block" }}>{l}</span>)}</p>
        <h4 className={s.listHead}>
          <span>리포트의 주요 주장과 판정<InfoTip text={CLAIMS_NOTE} /></span>
        </h4>
        <div style={{ display: "flex", flexDirection: "column" }}>
          {claimsByPage(claims).map((c, i) => (
            <button key={c.id} type="button" className={s.claimRow} onClick={() => onOpenClaim(c.id)}>
              <ClaimNumber n={i + 1} tone={toneOf(c.verdict)} />
              <span>
                <strong style={{ fontSize: 13.5 }}><ClaimName n={i + 1} core={c.is_core} /><span className={s.muted} style={{ fontWeight: 400, marginLeft: 8, fontSize: 12 }}>원문 {c.page}쪽, 이의 {c.objections.length}건</span></strong>
                <small>{c.text}</small>
              </span>
              {/* 판정만 둔다. 틀린 숫자는 문제 목록과 오른쪽 패널에 따로 나오고 판정에도 이미 반영된다 */}
              <Verdict label={c.verdict} />
            </button>
          ))}
        </div>
      </section>

      {checklist}
    </article>
  );
}

/** 투자 전에 직접 확인할 사항: 문제 가운데 사람이 꼭 확인해야 할 것(확인 필요, 핵심 주장의 수정) 최대 5개. 체크는 이 브라우저에 저장 */
export function ChecklistBlock({ result, checked, onToggleCheck, onOpen }: {
  result: Result; checked: Record<string, boolean>; onToggleCheck: (key: string) => void; onOpen: (ref: string) => void;
}) {
  const { checklist, claims } = result;
  if (!checklist.length) return null;
  // 체크 기록은 원래 순번(c0, c1 …)으로 저장돼 있으므로 순번을 들고 다닌다
  // 주장 순서(원문 쪽 순)대로. 주장과 묶이지 않은 것은 뒤로
  const order = (id: string | null) => (id ? claimNo(claims, id) || 999 : 999);
  const items = checklist.map((it, i) => ({ it, key: `c${i}` })).sort((a, b) => order(a.it.claim_id) - order(b.it.claim_id));
  const groups = [
    { title: "리포트 논리를 확인할 사항", rows: items.filter(({ it }) => checkGroup(it) === "logic") },
    { title: "리포트 수치를 확인할 사항", rows: items.filter(({ it }) => checkGroup(it) === "number") },
  ].filter((g) => g.rows.length);
  return (
    <section className={s.checkBlock}>
      <h3>투자 전에 직접 확인할 사항 <span className={s.muted} style={{ fontWeight: 400 }}>{items.filter(({ key }) => checked[key]).length}/{items.length}</span></h3>
      {groups.map((g) => (
        <div key={g.title} className={s.checkGroup}>
          <h4>{g.title} <span className={s.muted} style={{ fontWeight: 400 }}>{g.rows.length}</span></h4>
          <div className={s.checkList}>
            {g.rows.map(({ it, key }) => {
              const n = it.claim_id ? claimNo(claims, it.claim_id) : 0;
              return (
                <div key={key} className={s.check}>
                  <input type="checkbox" aria-label="확인함" checked={!!checked[key]} onChange={() => onToggleCheck(key)} />
                  {/* 글을 누르면 그 지적이 원문 위치와 함께 열린다 */}
                  <div className={s.checkBody}>
                    {/* 글을 누르면 그 지적이, 아래 연한 글씨를 누르면 그 주장이 열린다 */}
                    <button type="button" className={s.checkText} onClick={() => onOpen(it.why)}>
                      <Linked text={it.what} />
                    </button>
                    <button type="button" className={s.checkLink} onClick={() => onOpen(it.claim_id ?? it.why)}>
                      {n ? `주장 ${n} 보기` : "원문 보기"} ›
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </section>
  );
}

/* ───────── 반대신문 과정 ───────── */

const DOUBT_HELP: Record<string, string> = {
  "숫자의 기준": "같은 이름의 숫자라도 기준(연결/별도, 평균/기말 등)이 달라 비교가 맞지 않을 수 있다는 지적",
  "기간 선택": "유리한 기간만 골라 성장률·수익률을 계산했을 수 있다는 지적",
  "가정의 근거": "전망에 쓴 가정이 뒷받침되지 않는다는 지적",
  "논리 연결": "근거에서 결론으로 가는 연결이 약하다는 지적",
  "빠진 위험": "결론을 뒤집을 수 있는 위험을 다루지 않았다는 지적",
  "근거의 독립성": "여러 근거가 사실은 같은 출처·같은 가정에서 나왔다는 지적",
};

/** 이의 하나: 원문 인용 -> 지적 -> 배심원 표 한 줄 -> 배심원별 이유 */
export function ObjectionView({ o, nJurors }: { o: Objection; nJurors: number }) {
  const t = o.tally;
  return (
    <div className={s.objection}>
      <div className={s.objMeta}>
        <Verdict label={o.verdict} strong />
        <strong>{o.who === "검사" ? "리스크 검토자" : o.who}</strong>
        <span title={DOUBT_HELP[o.doubt_type]}>{o.doubt_type}</span>
        {o.kind === "사실 오류" && <span>사실 오류</span>}
        {o.split && <span className={s.splitTag} title="판단한 표(타당·과장) 가운데 두 쪽 차이가 1표 이내입니다">배심원 의견 갈림</span>}
      </div>
      {o.quote && (
        <blockquote className={s.objQuote}>
          “{o.quote}”<small>{o.page ? ` 원문 ${o.page}쪽` : ""}{o.quote_found ? "" : ", 원문에서 찾지 못함"}</small>
        </blockquote>
      )}
      <p><Linked text={o.text} /></p>
      {o.evidence.length > 0 && (
        <p className={s.evidence}>반대 근거: {o.evidence.map((e) => `${e.label}${e.page ? ` (${e.page}쪽)` : ""}`).join(", ")}</p>
      )}
      {o.votes.length > 0 ? (
        <>
          <div className={s.tallyLine}>
            <Mix label={`타당 ${t["타당"]}, 확인 불가 ${t["확인 불가"]}, 과장 ${t["과장"]}`}
                 parts={[{ tone: "red", value: t["타당"] }, { tone: "orange", value: t["확인 불가"] }, { tone: "green", value: t["과장"] }]} />
            <span>배심원 {nJurors}명: 타당 {t["타당"]} · 과장 {t["과장"]} · 확인 불가 {t["확인 불가"]}{t["기권"] ? ` · 기권 ${t["기권"]}` : ""}</span>
          </div>
          <ul className={s.votes}>
            {o.votes.map((v) => (
              <li key={v.juror}>
                <span>{v.juror}</span>
                <Verdict label={v.vote} />
                <span>{v.view && <span className={s.voteView}><Linked text={v.view} /></span>}<Linked text={v.reason} /></span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <span className={s.muted} style={{ fontSize: 12 }}>배심원이 추가한 이의라 표가 없습니다. 규칙에 따라 확인 필요까지만 올라갑니다.</span>
      )}
    </div>
  );
}

/** 문제 하나 (논리 또는 숫자) */
function ProblemRow({ p, result, active, onSelect, onOpenRef, onPage }: {
  p: Problem; result: Result; active: boolean; onSelect: (id: string) => void; onOpenRef: (id: string) => void; onPage: (n: number) => void;
}) {
  const claimLink = (cid: string) => (
    <button key={cid} type="button" onClick={(e) => { e.stopPropagation(); onSelect(cid); }}>주장 {claimNo(result.claims, cid)}</button>
  );
  // 카드를 누르면 오른쪽 패널에 열린다 (안의 버튼·링크는 각자 동작)
  const pick = (e: React.MouseEvent) => { if (!(e.target as HTMLElement).closest("button, a, summary")) onSelect(p.id); };
  if (p.kind === "number") {
    const x = p.x;
    return (
      <div className={`${s.objection} ${s.problemCard}`} data-active={active || undefined} onClick={pick}>
        <div className={s.objMeta}>
          <strong>숫자</strong>
          <span>{numberSource(x)}</span>
          <Verdict label={x.status} strong />
          <span className={s.pageLinks} style={{ margin: 0 }}>{x.pages.map((n) => <button key={n} type="button" onClick={() => onPage(n)}>{n}쪽</button>)}</span>
        </div>
        <p title={x.check_id}>{x.name}</p>
        <p className={s.evidence}>
          리포트 {String(x.stated ?? "")}
          {x.computed !== null ? ` → ${numberSource(x) === "다시 계산" ? "다시 계산" : "공식 값"} ${shownComputed(x.computed)}` : ""}
          {x.diff_pct !== null ? ` (차이 ${x.diff_pct}%)` : ""}
        </p>
        {x.reason && <span className={s.muted} style={{ fontSize: 12 }}>{x.reason}</span>}
        {p.claimIds.length > 0 && <span className={s.pageLinks} style={{ margin: 0 }}>이 숫자를 쓴 주장: {p.claimIds.map(claimLink)}</span>}
        <button type="button" className={s.link} style={{ alignSelf: "flex-start" }} onClick={() => onOpenRef(x.check_id)}>원문에서 위치 보기</button>
      </div>
    );
  }
  return (
    <div className={s.problemCard} data-active={active || undefined} onClick={pick}>
      <ObjectionView o={p.o} nJurors={result.logic.n_jurors} />
      <span className={s.pageLinks} style={{ margin: "0 0 0 2px" }}>
        {p.claimId ? <>관련 주장: {claimLink(p.claimId)}</> : <span className={s.muted}>추출된 주장과 묶이지 않은 지적</span>}
        {p.o.quote_found && <button type="button" onClick={() => onOpenRef(p.o.id)}>원문에서 위치 보기</button>}
      </span>
    </div>
  );
}

export function RecordsView({ result, selected, onSelect, onOpenClaim, onPage }: {
  result: Result; selected: string | null; onSelect: (id: string) => void; onOpenClaim: (id: string) => void; onPage: (p: number) => void;
}) {
  const { claims, numbers, logic, extraction, rows } = result;
  const list = problems(result);
  const nNum = list.filter((p) => p.kind === "number").length;
  const major = list.filter((p) => p.verdict === "수정" || p.verdict === "불일치");   // 펼쳐 둘 것
  const minor = list.filter((p) => !major.includes(p));                              // 확인 필요·판정 보류: 접어 둔다
  const kept = keptObjections(result);
  const unverified = unverifiedNumbers(result);
  const row = (p: Problem) => (
    <ProblemRow key={p.id} p={p} result={result} active={selected === p.id} onSelect={onSelect} onOpenRef={onOpenClaim} onPage={onPage} />
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <section className={s.block}>
        <div className={s.sectionHead}>
          <h3>문제 목록<small>{list.length}건 (숫자 {nNum}, 논리 {list.length - nNum}), 중요도 순<InfoTip text={IMPORTANCE_NOTE} /></small></h3>
        </div>
        <div className={s.problemList}>
          {list.length === 0 && <p className={s.muted} style={{ margin: 0, fontSize: 13 }}>문제가 발견되지 않았습니다.</p>}
          {major.map(row)}
          {minor.length > 0 && (
            <details className={s.problemGroup}>
              <summary>확인 필요 <span className={s.muted} style={{ fontWeight: 400 }}>{minor.length}건, 배심원 판단이 갈렸거나 판단한 배심원이 적어 근거를 더 봐야 하는 지적</span></summary>
              <div className={s.problemList} style={{ borderTop: 0 }}>{minor.map(row)}</div>
            </details>
          )}
        </div>
        <p className={s.foot}>
          숫자 문제는 코드가 공식 데이터·계산으로 확인한 것이고, 논리 문제는 리스크 검토자의 이의를 배심원 {logic.n_jurors}명이 투표로 판정한 것입니다.
          맞는 것으로 확인된 숫자 {numbers.count["일치"] ?? 0}건과 배심원이 받아들이지 않은 지적은 목록에 넣지 않았습니다.
        </p>
      </section>

      {kept.length > 0 && (
        <section className={s.block}>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>배심원이 받아들이지 않은 지적 <span className={s.muted} style={{ fontWeight: 400 }}>{kept.length}건 (유지)</span></summary>
            <div className={s.recordObjections} style={{ marginTop: 10 }}>
              {kept.map((o) => <ObjectionView key={o.id} o={o} nJurors={logic.n_jurors} />)}
            </div>
          </details>
        </section>
      )}

      {unverified.length > 0 && (
        <section className={s.block}>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>대조하지 못한 숫자 <span className={s.muted} style={{ fontWeight: 400 }}>{unverified.length}건</span></summary>
            <div className={s.tableWrap} style={{ marginTop: 10 }}>
              <table className={s.table} style={{ minWidth: 560 }}>
                <thead><tr><th>확인하려던 것</th><th className={s.num}>리포트 표기</th><th>못 한 이유</th><th className={s.num}>쪽</th></tr></thead>
                <tbody>
                  {unverified.map((x) => (
                    <tr key={x.check_id}>
                      <td>{x.name}</td>
                      <td className={s.num}>{String(x.stated ?? "")}</td>
                      <td className={s.muted} style={{ fontSize: 12 }}>{x.reason}</td>
                      <td className={s.num}><span className={s.pageLinks} style={{ margin: 0 }}>{x.pages.map((n) => <button key={n} type="button" onClick={() => onPage(n)}>{n}</button>)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={s.foot}>공식 데이터에 같은 기준의 값이 없거나, 리포트가 기간·기준을 밝히지 않아 맞다 틀리다를 정하지 않은 숫자입니다. 식이 없어 확인하지 않은 숫자는 {numbers.n_unchecked}개입니다.</p>
          </details>
        </section>
      )}

      <section className={s.block}>
        <details>
        <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>주장별로 보기 <span className={s.muted} style={{ fontWeight: 400 }}>주장 {claims.length}개, 판사의 판정 근거 포함</span></summary>
        <div style={{ borderTop: "2px solid var(--ink)", marginTop: 10 }}>
          {claimsByPage(claims).map((c, i) => (
            <details key={c.id} className={s.recordClaim} open={c.is_core}>
              <summary>
                <ClaimNumber n={i + 1} tone={toneOf(c.verdict)} />
                <span>{c.is_core && <span className={s.coreTag} style={{ marginLeft: 0, marginRight: 6 }}>핵심</span>}{c.text}</span>
                <small>{c.objections.length ? `이의 ${c.objections.length}건` : "이의 없음"}</small>
                <Verdict label={c.verdict} strong={c.verdict === "수정"} />
              </summary>
              <div className={s.recordBody}>
                <blockquote>
                  {c.text}
                  <span className={s.pageLinks}>
                    <button type="button" onClick={() => onOpenClaim(c.id)}>원문 {c.page}쪽에서 보기</button>
                  </span>
                </blockquote>
                {c.objections.length === 0 && <p className={s.muted} style={{ margin: 0, fontSize: 13 }}>리스크 검토자가 이의를 내지 않았습니다.</p>}
                <div className={s.recordObjections}>
                  {c.objections.map((o) => <ObjectionView key={o.id} o={o} nJurors={logic.n_jurors} />)}
                </div>
                {c.ruling && (
                  <p className={s.ruling}>
                    <span>판사 </span>{c.ruling.reason}
                    <span> 투자의견 영향 {c.ruling.impact}{c.ruling.fix_part ? `, 검토할 부분 ${c.ruling.fix_part}` : ""}</span>
                  </p>
                )}
              </div>
            </details>
          ))}
        </div>
        </details>
      </section>

      {extraction && (
        <section className={s.block} style={{ maxWidth: 720 }}>
          <div className={s.sectionHead}><h3>원문 대조<small>AI가 뽑은 것을 코드가 원문과 한 글자씩 대조</small></h3></div>
          <dl className={s.statGrid}>
            {([["주장", extraction.claims], ["숫자", extraction.data_rows], ["검증 식", extraction.checks], ["표", extraction.tables], ["버린 것", extraction.dropped_total]] as const).map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
          <p className={s.foot}>원문에서 글자 그대로 찾지 못한 주장, 숫자, 표의 줄은 버렸습니다.</p>
        </section>
      )}

      <section className={s.block}>
        <details>
          <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>리포트에서 옮긴 숫자 <span className={s.muted} style={{ fontWeight: 400 }}>{rows.length}개</span></summary>
          <div className={s.tableWrap} style={{ marginTop: 10 }}>
            <table className={s.table} style={{ maxWidth: 820 }}>
              <thead><tr><th>항목</th><th className={s.num}>값</th><th className={s.num}>쪽</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.rid}><td>{r.item}</td><td className={s.num}>{r.value}{unitAfter(r.value, r.unit)}</td>
                    <td className={s.num}><span className={s.pageLinks} style={{ margin: 0 }}><button type="button" onClick={() => onPage(r.page)}>{r.page}</button></span></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </div>
  );
}

/* ───────── 나의 판단 기록 ───────── */

export type Agreement = "동의" | "부분 동의" | "비동의";
export type Action = "매수" | "보유" | "매도";
export interface JournalForm {
  initial: string; initialAction: Action | "";          // AI 결과를 보기 전의 생각과 행동
  agreement: Agreement | ""; action: Action | "";        // 반대신문 결과를 본 뒤의 판단
  final: string; reason: string; notes: string;
}
export interface JournalEntry { at: string; action: string; agreement: string; note: string; before?: string; changed?: boolean }
export const EMPTY_FORM: JournalForm = { initial: "", initialAction: "", agreement: "", action: "", final: "", reason: "", notes: "" };

const AGREEMENTS: Agreement[] = ["동의", "부분 동의", "비동의"];
const ACTIONS: { id: Action; desc: string; tone: "green" | "orange" | "red" }[] = [
  { id: "매수", desc: "리포트 의견을 받아들여 편입하거나 더 산다", tone: "green" },
  { id: "보유", desc: "확인할 것이 남아 판단을 미룬다", tone: "orange" },
  { id: "매도", desc: "매수 논리를 받아들이지 않는다", tone: "red" },
];
export const ACTION_TONE: Record<string, "green" | "orange" | "red"> = { 매수: "green", 보유: "orange", 매도: "red" };

export function JournalView({ name, opinion, target, form, saveLabel, saveColor, onChange, onSave, onReset, onExport, checklist }: {
  name: string; opinion: string; target: string; form: JournalForm; saveLabel: string; saveColor: string;
  onChange: <K extends keyof JournalForm>(k: K, v: JournalForm[K]) => void; onSave: () => void; onReset: () => void; onExport: () => void;
  checklist?: ReactNode;      // 투자 전에 직접 확인할 사항 (반대신문 결과를 본 뒤의 판단 바로 앞)
}) {
  const submit = (e: FormEvent) => { e.preventDefault(); onSave(); };
  return (
    <form className={s.journal} onSubmit={submit}>
      <div className={s.journalHead}>
        <h2>{name} 판단 메모</h2>
        <span className={s.muted} style={{ fontSize: 12.5 }}>{opinion}, 목표가 {target}{unitAfter(String(target ?? ""), "원")}에 대한 내 판단. 이 브라우저에만 저장됩니다.</span>
      </div>

      <div className={s.field}>
        <label htmlFor="j-initial">첫인상 (AI 결과를 보기 전)</label>
        <span id="j-initial-help" className={s.help}>반론을 보기 전의 생각을 남겨 두면, 나중에 무엇이 판단을 바꿨는지 비교할 수 있습니다</span>
        <textarea id="j-initial" aria-describedby="j-initial-help" rows={3} value={form.initial} onChange={(e) => onChange("initial", e.target.value)} />
      </div>

      <fieldset className={s.fieldset}>
        <legend>AI 결과를 보기 전에는 무엇을 하려고 했나요</legend>
        <div className={`${s.segmented} ${s.agree}`} style={{ maxWidth: 420 }}>
          {ACTIONS.map((a) => (
            <button key={a.id} type="button" aria-pressed={form.initialAction === a.id} onClick={() => onChange("initialAction", form.initialAction === a.id ? "" : a.id)}>{a.id}</button>
          ))}
        </div>
      </fieldset>

      {checklist}

      <div className={s.fieldRow}>
        <fieldset className={s.fieldset}>
          <legend>투자의견에 동의하나요</legend>
          <div className={`${s.segmented} ${s.agree}`}>
            {AGREEMENTS.map((a) => (
              <button key={a} type="button" aria-pressed={form.agreement === a} onClick={() => onChange("agreement", form.agreement === a ? "" : a)}>{a}</button>
            ))}
          </div>
        </fieldset>
        <fieldset className={s.fieldset} style={{ flexGrow: 2 }}>
          <legend>
            반대신문 결과를 본 뒤, 무엇을 할 건가요
            {form.initialAction && form.action && (
              <span style={{ marginLeft: 8, fontWeight: 600, color: form.initialAction === form.action ? "var(--muted)" : TONE.orange.ink }}>
                {form.initialAction === form.action ? "처음 판단 그대로" : `처음 ${form.initialAction}에서 ${form.action}로 바뀜`}
              </span>
            )}
          </legend>
          <div className={s.actions}>
            {ACTIONS.map((a) => (
              <button key={a.id} type="button" className={s.actionCard} style={toneVars(a.tone)} aria-pressed={form.action === a.id}
                      onClick={() => onChange("action", form.action === a.id ? "" : a.id)}>
                <strong>{a.id}</strong>
                <span>{a.desc}</span>
              </button>
            ))}
          </div>
        </fieldset>
      </div>

      <div className={s.field}>
        <label htmlFor="j-final">최종 판단</label>
        <textarea id="j-final" rows={3} placeholder="반론까지 읽은 뒤의 결론을 한두 문장으로" value={form.final} onChange={(e) => onChange("final", e.target.value)} />
      </div>

      <div className={s.fieldRow}>
        <div className={s.field}>
          <label htmlFor="j-reason">결정한 이유</label>
          <textarea id="j-reason" rows={5} placeholder="결정을 이끈 근거" value={form.reason} onChange={(e) => onChange("reason", e.target.value)} />
        </div>
        <div className={s.field}>
          <label htmlFor="j-notes">반론을 검토하고 남길 메모</label>
          <textarea id="j-notes" rows={5} placeholder="받아들인 반론, 넘긴 반론, 더 확인할 데이터" value={form.notes} onChange={(e) => onChange("notes", e.target.value)} />
        </div>
      </div>

      <div className={s.formFoot}>
        <span role="status" className={s.saveState} style={{ color: saveColor }}>{saveLabel}</span>
        <button type="button" className={s.btn} onClick={onExport}>CSV로 내보내기</button>
        <button type="button" className={s.btn} onClick={onReset}>모두 지우기</button>
        <button type="submit" className={s.btnPrimary}>판단 저장</button>
      </div>
    </form>
  );
}

/* ───────── 판정 과정 타임라인 (실제로 있는 단계만) ───────── */

/** 이의 하나가 어떤 규칙으로 그 판정이 됐는지 (파이썬 rules.objection_verdict 와 같은 규칙을 글로 옮김) */
export function ruleText(o: Objection, nJurors: number, majority: number): string {
  const t = o.tally;
  const valid = nJurors - (t["기권"] ?? 0);
  if (o.votes.length === 0) return "배심원이 추가한 이의라 표가 없습니다. 규칙상 '확인 필요'까지만 올라갑니다.";
  if (valid < majority) return `유효표 ${valid}표가 과반(${majority}표)에 못 미쳐 판정을 보류했습니다.`;
  // '확인 불가'는 판단을 맡긴 표: 판단한 표(타당+과장) 3표 이상 가운데 과반으로 정한다
  const judged = t["타당"] + t["과장"];
  const need = Math.min(3, nJurors);
  const unsure = t["확인 불가"] ? ` ('확인 불가' ${t["확인 불가"]}표는 판단을 맡긴 표라 세지 않습니다)` : "";
  if (judged < need) return `판단한 표(타당·과장)가 ${judged}표로 ${need}표에 못 미쳐 '확인 필요'입니다. 사람이 직접 봐야 합니다.`;
  if (t["타당"] * 2 > judged) {
    return o.verdict === "수정"
      ? `판단한 ${judged}표 가운데 타당이 ${t["타당"]}표로 과반이라 '수정'입니다.${unsure}`
      : `타당이 과반이지만 근거(숫자나 원문 인용)가 확인되지 않아 '확인 필요'까지만 올렸습니다.`;
  }
  if (t["과장"] * 2 > judged) return `판단한 ${judged}표 가운데 과장이 ${t["과장"]}표로 과반이라 '유지'입니다. 지적은 맞을 수 있으나 결론에 미치는 영향이 작다는 뜻입니다.${unsure}`;
  return `판단한 표가 타당 ${t["타당"]}표, 과장 ${t["과장"]}표로 같아 '확인 필요'입니다.`;
}

export function JudgmentTimeline({ o, nJurors, majority }: { o: Objection; nJurors: number; majority: number }) {
  const t = o.tally;
  return (
    <ol className={s.timeline}>
      <li>
        <strong>원문</strong>
        {o.quote
          ? <blockquote className={s.objQuote}>“{o.quote}”<small>{o.page ? ` 원문 ${o.page}쪽` : ""}{o.quote_found ? "" : ", 원문에서 찾지 못함"}</small></blockquote>
          : <span className={s.muted} style={{ fontSize: 12 }}>원문 인용 없이 근거 숫자로 제기한 이의입니다.</span>}
      </li>
      <li>
        <strong>{o.who === "검사" ? "리스크 검토자" : o.who}의 이의 <span className={s.muted} style={{ fontWeight: 400 }} title={DOUBT_HELP[o.doubt_type]}>{o.doubt_type}{o.kind === "사실 오류" ? ", 사실 오류" : ""}</span></strong>
        <p><Linked text={o.text} /></p>
        {o.evidence.length > 0 && (
          <p className={s.evidence}>반대 근거: {o.evidence.map((e) => `${e.label}${e.page ? ` (${e.page}쪽)` : ""}`).join(", ")}</p>
        )}
      </li>
      {o.votes.length > 0 && (
        <li>
          <strong>배심원 {nJurors}명 투표 <span className={s.muted} style={{ fontWeight: 400 }}>서로의 표를 보지 않고 투표</span></strong>
          <div className={s.tallyLine}>
            <Mix label={`타당 ${t["타당"]}, 확인 불가 ${t["확인 불가"]}, 과장 ${t["과장"]}`}
                 parts={[{ tone: "red", value: t["타당"] }, { tone: "orange", value: t["확인 불가"] }, { tone: "green", value: t["과장"] }]} />
            <span>타당 {t["타당"]} · 과장 {t["과장"]} · 확인 불가 {t["확인 불가"]}{t["기권"] ? ` · 기권 ${t["기권"]}` : ""}</span>
          </div>
          <ul className={s.votes}>
            {o.votes.map((v) => (
              <li key={v.juror}>
                <span>{v.juror}</span>
                <Verdict label={v.vote} />
                <span>{v.view && <span className={s.voteView}><Linked text={v.view} /></span>}<Linked text={v.reason} /></span>
              </li>
            ))}
          </ul>
        </li>
      )}
      <li data-final="true" style={toneVars(toneOf(o.verdict))}>
        <strong>판정 <Verdict label={o.verdict} strong /></strong>
        <p className={s.evidence}>{ruleText(o, nJurors, majority)} 판정은 AI가 아니라 정해진 규칙(코드)이 정합니다.</p>
      </li>
    </ol>
  );
}
