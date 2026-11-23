"use client";

// 분석이 끝난 리포트의 가운데 화면: 요약, 검토 과정(문제 목록, 이의와 배심원 표), 나의 판단 기록
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type { Claim, Objection, Result } from "@/lib/types";
import { Chevron, InfoTip, Verdict, VoteTally } from "./primitives";
import { IMPORTANCE_NOTE, keptObjections, numberSource, problems, unverifiedNumbers, type Problem } from "./problems";
import { Linked } from "./refs";
import { GRADE_TEXT, shownComputed, TONE, toneOf, toneVars, unitAfter } from "./tone";
import { NumbersTable } from "./numbers-table";
import s from "./workspace.module.css";

/** 배심원이 추가한 이의인가 (id 가 J- 로 시작) */
const isAdded = (o: Objection) => o.id.startsWith("J-");

/** 이의의 원문 인용. 코드가 붙인 문장 전체(quote_ctx)가 있으면 문장 전체를 보여 주고 리스크 검토자가 짚은 구절만 굵게 */
export function QuoteBlock({ o }: { o: Objection }) {
  const c = o.quote_ctx;
  const page = c?.page ?? o.page;
  return (
    <blockquote className={s.objQuote}>
      {c
        ? <>“{c.before && <><span className={s.ctxSoft}>{c.before}</span> </>}<span className={s.ctxHit}>{c.quote}</span>{c.after && <> <span className={s.ctxSoft}>{c.after}</span></>}”</>
        : <>“{o.quote}”</>}
      <small>{page ? ` 원문 ${page}쪽` : ""}{o.quote_found ? "" : ", 원문에서 찾지 못함"}</small>
    </blockquote>
  );
}

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
  "투자의견을 떠받치는 핵심 주장입니다(원문 쪽 순서).",
  "핵심 주장에 수정이 있으면 리포트 등급이 '재검토 필요'가 됩니다.",
  "누르면 그 주장에 걸린 가장 무거운 지적이 열립니다.",
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
    ? `핵심 주장 ${nClaims}개 중 ${parts.join(", ")}이 필요합니다.`
    : `핵심 주장 ${nClaims}개 모두 큰 문제가 없습니다.`;
  return nProblems ? [first, `리포트 전체에서 수정하거나 확인할 지적이 ${nProblems}건 나왔습니다.`] : [first];
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
  const { meta, grade, claims, summary } = result;
  const gTone = toneOf(grade.value);
  const up = upside(meta);
  const nProblems = problems(result).length;
  const core = claimsByPage(claims).filter((c) => c.is_core);
  return (
    <article className={s.summary}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 className={s.summaryTitle}>{meta.title}</h2>
        <span className={s.muted}>{meta.report_date} 발간, 가격 기준일 {meta.snapshot_date}</span>
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
          <span style={{ fontSize: 14 }}>{GRADE_TEXT[grade.value] ?? ""}{grade.holds ? ` (일부 판정 못 함 ${grade.holds}건)` : ""}</span>
        </div>
        <p>{summaryLines(core.length, core.filter((c) => c.verdict === "수정").length, core.filter((c) => c.verdict === "확인 필요").length, nProblems).map((l, i) => <span key={i} style={{ display: "block" }}>{l}</span>)}</p>
        {core.length > 0 && <>
          <h4 className={s.listHead}>
            <span>핵심 주장 판정<InfoTip text={CLAIMS_NOTE} /></span>
          </h4>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {core.map((c) => (
              <button key={c.id} type="button" className={s.coreRow} style={toneVars(toneOf(c.verdict))} onClick={() => onOpenClaim(c.id)}>
                <Verdict label={c.verdict} />
                <span>{c.text}</span>
                <small>{c.page ? `${c.page}쪽` : ""}</small>
              </button>
            ))}
          </div>
        </>}
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

/* ───────── 문제 목록 (이의와 배심원 표) ───────── */

export const DOUBT_HELP: Record<string, string> = {
  "숫자의 기준": "같은 이름의 숫자라도 기준(연결/별도, 평균/기말 등)이 달라 비교가 맞지 않을 수 있다는 지적",
  "기간 선택": "유리한 기간만 골라 성장률·수익률을 계산했을 수 있다는 지적",
  "가정의 근거": "전망에 쓴 가정이 뒷받침되지 않는다는 지적",
  "논리 연결": "근거에서 결론으로 가는 연결이 약하다는 지적",
  "위험 누락": "결론을 뒤집을 수 있는 위험을 다루지 않았다는 지적",
  "근거의 독립성": "여러 근거가 사실은 같은 출처·같은 가정에서 나왔다는 지적",
};

/** 이의 하나: 원문 인용 -> 지적 -> 배심원 표 한 줄 -> 배심원별 이유 */
export function ObjectionView({ o, nJurors, inRow }: { o: Objection; nJurors: number; inRow?: boolean }) {
  const t = o.tally;
  return (
    <div className={s.objection}>
      <div className={s.objMeta}>
        {!inRow && <Verdict label={o.verdict} strong />}
        <strong>{o.who === "검사" ? "리스크 검토자" : o.who}의 지적</strong>
        {!inRow && <span className={s.typeTag} title={DOUBT_HELP[o.doubt_type]}>{o.doubt_type}{o.kind === "사실 오류" ? " · 사실 오류" : ""}</span>}
        {o.split && <span className={s.splitTag} title="판단한 표(동의·비동의) 가운데 두 쪽 차이가 1표 이내입니다">배심원 의견 갈림</span>}
        {o.verdict === "유지" && o.verdict_reason && <span className={s.splitTag} title={o.verdict_reason === "경미한 지적" ? "배심원 과반이 이의에 동의했지만 결론에 미치는 영향(중요도)이 작다고 봤습니다" : "판단한 배심원 과반이 이의에 동의하지 않았습니다"}>{o.verdict_reason}</span>}
        {o.importance && o.verdict !== "유지" && <span className={s.splitTag} title="동의한 배심원 다수가 매긴 중요도">중요도 {o.importance}</span>}
        {o.importance_split && <span className={s.splitTag} title="동의한 배심원 가운데 중요도를 상과 하로 본 사람이 함께 있습니다">중요도 의견 갈림</span>}
        {isAdded(o) && <span className={s.splitTag} title="리스크 검토자가 놓친 것을 배심원이 찾아 추가한 이의입니다. 이의를 낸 배심원을 뺀 나머지가 표결합니다">배심원이 새로 찾은 문제</span>}
      </div>
      {o.quote && <QuoteBlock o={o} />}
      <p><Linked text={o.text} /></p>
      {o.evidence.length > 0 && (
        <p className={s.evidence}>이의 근거: {o.evidence.map((e) => `${e.label}${e.page ? ` (${e.page}쪽)` : ""}`).join(", ")}</p>
      )}
      {o.votes.length > 0 ? (
        <>
          <VoteTally tally={t} who={o.round2 ? `2차 표결 ${o.votes.length}명 투표(이의를 낸 배심원 제외)` : `배심원 ${nJurors}명 투표`} />
          <ul className={s.votes}>
            {o.votes.map((v) => (
              <li key={v.juror}>
                <span>{v.juror}</span>
                <span><Verdict label={v.vote} />{v.importance ? <small className={s.muted}> 중요도 {v.importance}</small> : null}</span>
                <span>{v.view && <span className={s.voteView}><Linked text={v.view} /></span>}<Linked text={v.reason} /></span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <span className={s.muted} style={{ fontSize: 12 }}>배심원이 추가한 이의인데 2차 표결 기록이 없습니다(예전 분석). 규칙에 따라 확인 필요까지만 올라갑니다.</span>
      )}
    </div>
  );
}

/** 문제 하나 (논리 또는 숫자): 처음엔 한 줄, 누르면 그 자리에서 펼친다 (2026-11-21 검토 과정 탭 C안) */
function ProblemRow({ p, result, open, onToggle, onOpenRef, onPage }: {
  p: Problem; result: Result; open: boolean; onToggle: (id: string) => void; onOpenRef: (id: string) => void; onPage: (n: number) => void;
}) {
  const core = p.kind === "logic" && result.claims.some((c) => c.id === p.claimId && c.is_core);
  const head = p.kind === "number"
    ? <><span className={s.typeTag}>숫자 · {numberSource(p.x)}</span><span className={s.rowText}>{p.x.name}</span></>
    : <>
        {core && <span className={s.coreTag} style={{ marginLeft: 0 }}>핵심 주장</span>}
        <span className={s.typeTag} title={DOUBT_HELP[p.o.doubt_type]}>{p.o.doubt_type}{p.o.kind === "사실 오류" ? " · 사실 오류" : ""}</span>
        <span className={s.rowText}><Linked text={p.o.text} /></span>
      </>;
  const ruling = p.kind === "logic" ? (p.o.ruling ?? result.claims.find((c) => c.id === p.claimId)?.ruling ?? null) : null;
  return (
    <div id={`row-${p.id}`} className={s.problemRow} data-open={open || undefined}>
      <button type="button" className={s.problemHead} style={toneVars(toneOf(p.verdict))} aria-expanded={open} onClick={() => onToggle(p.id)}>
        <Chevron size={9} />
        <Verdict label={p.kind === "number" ? p.x.status : p.verdict} strong />
        <small>{p.page ? `${p.page}쪽` : ""}</small>
        <span className={s.problemHeadText}>{head}</span>
      </button>
      {open && (
        <div className={s.problemBody}>
          {p.kind === "number" ? (
            <>
              <p className={s.evidence}>
                리포트 {String(p.x.stated ?? "")}
                {p.x.computed !== null ? ` → ${numberSource(p.x) === "다시 계산" ? "다시 계산" : "공식 값"} ${shownComputed(p.x.computed)}` : ""}
                {p.x.diff_pct !== null ? ` (차이 ${p.x.diff_pct}%)` : ""}
              </p>
              {p.x.reason && <span className={s.muted} style={{ fontSize: 12 }}>{p.x.reason}</span>}
              <span className={s.pageLinks} style={{ margin: 0 }}>{p.x.pages.map((n) => <button key={n} type="button" onClick={() => onPage(n)}>원문 {n}쪽</button>)}</span>
            </>
          ) : (
            <>
              <ObjectionView o={p.o} nJurors={result.logic.n_jurors} inRow />
              {ruling && (
                <p className={s.ruling}>
                  <span>판사{!p.o.ruling ? " (이 지적이 걸린 주장 전체에 대한 설명)" : ""} </span><Linked text={ruling.reason} />
                  <span> 투자의견 영향 {ruling.impact}{ruling.fix_part ? `, 검토할 부분 ${ruling.fix_part}` : ""}</span>
                </p>
              )}
              {p.o.quote_found && <button type="button" className={s.link} style={{ alignSelf: "flex-start" }} onClick={() => onOpenRef(p.o.id)}>원문에서 위치 보기</button>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function RecordsView({ result, selected, onSelect, onOpenClaim, onPage, onCheck }: {
  result: Result; selected: string | null; onSelect: (id: string) => void; onOpenClaim: (id: string) => void; onPage: (p: number) => void;
  onCheck: (ref: string) => void;
}) {
  const { numbers, logic } = result;
  const list = problems(result);
  const nNum = list.filter((p) => p.kind === "number").length;
  const kept = keptObjections(result);
  const unverified = unverifiedNumbers(result);
  // 펼친 줄: 직접 연 것 + 오른쪽 요약에서 고른 것(selected). 고른 것을 접으면 접힌 채로 둔다
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const [closed, setClosed] = useState<Set<string>>(() => new Set());
  const isOpen = (id: string) => opened.has(id) || (id === selected && !closed.has(id));
  const toggle = (id: string) => {
    const now = isOpen(id);
    setOpened((m) => { const n = new Set(m); if (now) n.delete(id); else n.add(id); return n; });
    setClosed((m) => { const n = new Set(m); if (now) n.add(id); else n.delete(id); return n; });
    if (!now) onSelect(id);
  };
  useEffect(() => {                   // 오른쪽 요약에서 고르면 그 줄로 옮겨 간다
    if (selected) document.getElementById(`row-${selected}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);
  const row = (p: Problem) => (
    <ProblemRow key={p.id} p={p} result={result} open={isOpen(p.id)} onToggle={toggle} onOpenRef={onOpenClaim} onPage={onPage} />
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <section className={s.block}>
        <details open>
          {/* 아래 '배심원이 받아들이지 않은 지적'과 같은 모양: 제목을 누르면 접고 편다 */}
          <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>
            문제 목록 <span className={s.muted} style={{ fontWeight: 400 }}>{list.length}건 (숫자 {nNum}, 논리 {list.length - nNum}), 중요도 순</span><InfoTip text={IMPORTANCE_NOTE} />
          </summary>
          <div className={s.problemList} style={{ marginTop: 10 }}>
            {list.length === 0 && <p className={s.muted} style={{ margin: 0, fontSize: 13 }}>문제가 발견되지 않았습니다.</p>}
            {list.map(row)}
          </div>
          <p className={s.foot}>
            숫자 문제는 코드가 공식 데이터·계산으로 확인한 것이고, 논리 문제는 리스크 검토자의 이의를 배심원 {logic.n_jurors}명이 투표로 판정한 것입니다.
            맞는 것으로 확인된 숫자 {numbers.count["일치"] ?? 0}건과 배심원이 받아들이지 않은 지적은 목록에 넣지 않았습니다.
          </p>
        </details>
      </section>

      {kept.length > 0 && (
        <section className={s.block}>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>배심원이 받아들이지 않은 지적 <span className={s.muted} style={{ fontWeight: 400 }}>{kept.length}건 (유지)</span></summary>
            <div className={s.problemList} style={{ marginTop: 10 }}>
              {kept.map((o) => row({ kind: "logic", id: o.id, verdict: o.verdict, page: o.page ?? null, o, claimId: o.claim_id }))}
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

      <NumbersTable result={result} onPage={onPage} onCheck={onCheck} />
    </div>
  );
}

/* ───────── 나의 판단 기록 ───────── */

export type Agreement = "동의" | "부분 동의" | "비동의";
export type Action = "매수" | "보유" | "매도";
export interface JournalForm {
  initial: string; initialAction: Action | "";          // AI 결과를 보기 전의 생각과 행동
  agreement: Agreement | ""; action: Action | "";        // 검토 결과를 본 뒤의 판단
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
  checklist?: ReactNode;      // 투자 전에 직접 확인할 사항 (검토 결과를 본 뒤의 판단 바로 앞)
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
        <span id="j-initial-help" className={s.help}>이의를 보기 전의 생각을 남겨 두면, 나중에 무엇이 판단을 바꿨는지 비교할 수 있습니다</span>
        <textarea id="j-initial" aria-describedby="j-initial-help" rows={3} value={form.initial} onChange={(e) => onChange("initial", e.target.value)} />
      </div>

      <fieldset className={s.fieldset}>
        <legend>AI 결과를 보기 전에는 무엇을 하려고 했나요</legend>
        <div className={`${s.segmented} ${s.agree}`}>
          {ACTIONS.map((a) => (
            <button key={a.id} type="button" aria-pressed={form.initialAction === a.id} onClick={() => onChange("initialAction", form.initialAction === a.id ? "" : a.id)}>{a.id}</button>
          ))}
        </div>
      </fieldset>

      {checklist}

      {/* 두 질문은 한 줄씩 (2026-11-21 사용자 요청) */}
      <fieldset className={s.fieldset}>
          <legend>투자의견에 동의하나요</legend>
          <div className={`${s.segmented} ${s.agree}`}>
            {AGREEMENTS.map((a) => (
              <button key={a} type="button" aria-pressed={form.agreement === a} onClick={() => onChange("agreement", form.agreement === a ? "" : a)}>{a}</button>
            ))}
          </div>
      </fieldset>
      <fieldset className={s.fieldset}>
          <legend>
            검토 결과를 본 뒤, 무엇을 할 건가요
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

      {/* 최종 판단과 결정한 이유를 한 칸으로 (2026-11-21 사용자 요청). 예전 기록의 '결정한 이유'는 이어 붙여 보여 주고, 고치면 한 칸에 합쳐진다 */}
      <div className={s.field}>
        <label htmlFor="j-final">최종 판단과 이유</label>
        <span id="j-final-help" className={s.help}>이의까지 읽은 뒤의 결론과, 그 결정을 이끈 근거를 적습니다. 받아들인 지적과 그 이유, 넘긴 지적과 그 이유, 더 확인할 데이터를 함께 적으면 나중에 판단을 돌아보기 좋습니다</span>
        <textarea id="j-final" aria-describedby="j-final-help" rows={7}
                  placeholder="예: 매수 의견에 부분 동의. 할인 축소 근거가 '검토' 수준이라는 지적은 받아들임. 배당 성장률 지적은 영향이 작다고 보고 넘김. 3분기 지분 매각 공시를 더 확인할 것"
                  value={[form.final, form.reason].filter(Boolean).join("\n\n")} onChange={(e) => onChange("final", e.target.value)} />
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
  if (o.votes.length === 0) return "배심원이 추가한 이의인데 2차 표결 기록이 없습니다(예전 분석). 규칙상 '확인 필요'까지만 올라갑니다.";
  if (o.round2) {                // 배심원 추가 이의: 이의를 낸 배심원을 뺀 나머지가 같은 규칙으로 표결
    nJurors = o.votes.length;
    majority = Math.floor(nJurors / 2) + 1;
  }
  const t = o.tally;
  const valid = nJurors - (t["기권"] ?? 0);
  if (valid < majority) return `유효표 ${valid}표가 과반(${majority}표)에 못 미쳐 판정을 보류했습니다.`;
  // '판단 불가'는 판단을 맡긴 표: 판단한 표(동의+비동의)가 3표 이상일 때 판정한다
  const agree = t["동의"], dis = t["비동의"], judged = agree + dis;
  const need = Math.min(3, nJurors);
  const unsure = t["판단 불가"] ? ` ('판단 불가' ${t["판단 불가"]}표는 판단을 맡긴 표라 세지 않습니다)` : "";
  if (judged < need) return `판단한 표(동의·비동의)가 ${judged}표로 ${need}표에 못 미쳐 '확인 필요'입니다. 사람이 직접 봐야 합니다.`;
  if (dis * 2 > judged) return `판단한 ${judged}표 가운데 비동의가 ${dis}표로 과반이라 '유지'(이의 불인정)입니다.${unsure}`;
  if (agree * 2 > judged) {
    const m = t["중요도"] ?? { 상: 0, 중: 0, 하: 0, 미확인: agree };
    const imp = m["미확인"] === agree ? "예전 분석이라 중요도 기록이 없습니다" :
      `동의한 배심원의 중요도는 상 ${m["상"]} · 중 ${m["중"]} · 하 ${m["하"]}${m["미확인"] ? ` · 미확인 ${m["미확인"]}` : ""}`;
    if (o.verdict === "수정") return `판단한 ${judged}표 가운데 동의가 ${agree}표로 과반이고, ${imp}라 '수정'입니다.${unsure}`;
    if (o.verdict === "유지") return `동의가 ${agree}표로 과반이지만 ${imp}로 '하'가 과반이라 '유지'(경미한 지적)입니다. 지적은 맞지만 결론에 미치는 영향이 작다는 뜻입니다.`;
    if (o.verdict_reason === "근거가 확인되지 않음") return `동의가 과반이고 중요도도 높지만 근거(숫자나 원문 인용)가 확인되지 않아 '확인 필요'까지만 올렸습니다.`;
    return `동의가 ${agree}표로 과반이지만 중요도 의견이 갈려(${imp}) '확인 필요'입니다.`;
  }
  return `판단한 표가 동의 ${agree}표, 비동의 ${dis}표로 같아 '확인 필요'입니다.`;
}

export function JudgmentTimeline({ o, nJurors, majority }: { o: Objection; nJurors: number; majority: number }) {
  const t = o.tally;
  return (
    <ol className={s.timeline}>
      <li>
        <strong>원문</strong>
        {o.quote
          ? <QuoteBlock o={o} />
          : <span className={s.muted} style={{ fontSize: 12 }}>원문 인용 없이 근거 숫자로 제기한 이의입니다.</span>}
      </li>
      <li>
        <strong>{o.who === "검사" ? "리스크 검토자" : o.who}의 지적 <span className={s.typeTag} style={{ marginLeft: 6 }} title={DOUBT_HELP[o.doubt_type]}>{o.doubt_type}{o.kind === "사실 오류" ? " · 사실 오류" : ""}</span></strong>
        <p><Linked text={o.text} /></p>
        {o.evidence.length > 0 && (
          <p className={s.evidence}>이의 근거: {o.evidence.map((e) => `${e.label}${e.page ? ` (${e.page}쪽)` : ""}`).join(", ")}</p>
        )}
      </li>
      {o.votes.length > 0 && (
        <li>
          <strong>{o.round2 ? `2차 표결 ${o.votes.length}명` : `배심원 ${nJurors}명 투표`} {o.round2 && <span className={s.muted} style={{ fontWeight: 400 }}>이의를 낸 배심원 제외</span>}</strong>
          <VoteTally tally={t} />
          <ul className={s.votes}>
            {o.votes.map((v) => (
              <li key={v.juror}>
                <span>{v.juror}</span>
                <span><Verdict label={v.vote} />{v.importance ? <small className={s.muted}> 중요도 {v.importance}</small> : null}</span>
                <span>{v.view && <span className={s.voteView}><Linked text={v.view} /></span>}<Linked text={v.reason} /></span>
              </li>
            ))}
          </ul>
        </li>
      )}
      <li data-final="true" style={toneVars(toneOf(o.verdict))}>
        <strong>판정 <Verdict label={o.verdict} strong /></strong>
        <p className={s.evidence}>{ruleText(o, nJurors, majority)}</p>
      </li>
    </ol>
  );
}
