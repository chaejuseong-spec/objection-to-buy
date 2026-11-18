"use client";

// 오른쪽 검토 패널
import type { CodeFinding, EvidenceClaim, LibraryEntry, Result } from "@/lib/types";
import { markTone } from "./document-view";
import { Chevron, ClaimName, ClaimNumber, Collapsible, InfoTip, Mix, Verdict } from "./primitives";
import { findObjection, IMPORTANCE_NOTE, numberSource, problems } from "./problems";
import { Linked } from "./refs";
import { evText, GRADE_TEXT, shownComputed, TONE, toneOf, toneVars } from "./tone";
import { ACTION_TONE, checkGroup, claimNo, claimsByPage, JudgmentTimeline, type JournalEntry } from "./views";
import s from "./workspace.module.css";

/* ───────── 요약 ───────── */

/** 요약 화면의 오른쪽: 판정 한 줄과 가장 중요한 문제 3개. 가운데 화면의 숫자를 되풀이하지 않는다 */
export function SummaryPanel({ result, onOpenDoc, onOpenRef, onAll }: {
  result: Result; onOpenDoc: () => void; onOpenRef: (ref: string) => void; onAll: () => void;
}) {
  const { grade, run } = result;
  const gTone = toneOf(grade.value);
  const list = problems(result);           // 문제 모두 (심각한 순)
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div className={s.panelBlock} style={{ gap: 10 }}>
        <h2>이 리포트의 판정</h2>
        <p className={s.panelTitle} style={{ color: TONE[gTone].ink }}>{grade.value}</p>
        <p className={s.panelText}>{GRADE_TEXT[grade.value] ?? ""}.</p>
      </div>
      <div className={s.panelBlock} style={{ gap: 2 }}>
        <h2 style={{ marginBottom: 6 }}>문제 목록 <span className={s.muted} style={{ fontWeight: 400 }}>{list.length}건, 중요도 순<InfoTip text={IMPORTANCE_NOTE} /></span></h2>
        {list.length === 0 ? <p className={s.panelText}>발견된 문제가 없습니다.</p> : list.map((p) => (
          <button key={p.id} type="button" className={s.issueRow} style={toneVars(toneOf(p.verdict))} onClick={() => onOpenRef(p.id)}>
            <Verdict label={p.verdict} />
            <span>{p.kind === "number" ? `${numberSource(p.x)}: ${p.x.name}` : <Linked text={p.o.text} />}</span>
            <small>{p.page ? `${p.page}쪽` : ""}</small>
          </button>
        ))}
        {list.length > 0 && <button type="button" className={s.link} style={{ alignSelf: "flex-start", marginTop: 8 }} onClick={onAll}>문제 목록 탭에서 자세히 보기</button>}
      </div>
      {run.mock && (
        <div className={s.panelBlock}>
          <p className={s.panelText}>이 결과의 논리 평가는 시연용 가짜 답으로 채웠습니다. 숫자 검증과 원문 대조는 실제 계산입니다.</p>
        </div>
      )}
      <div className={s.panelActions}>
        <button type="button" className={s.btnPrimary} onClick={onOpenDoc}>원문에서 판정 보기</button>
      </div>
    </div>
  );
}

/* ───────── 주장 또는 숫자 하나 ───────── */

export function DetailPanel({ result, selected, checked, onToggleCheck, onStep, onOpenRef, onSelect, position, canPrev = true, canNext = true }: {
  result: Result; selected: string | null; checked: Record<string, boolean>; onToggleCheck: (k: string) => void;
  onStep: (d: number) => void; onOpenRef?: (ref: string) => void; onSelect?: (ref: string) => void; position: string;
  canPrev?: boolean; canNext?: boolean;
}) {
  const claim = result.claims.find((c) => c.id === selected);
  const check = result.numbers.results.find((x) => x.check_id === selected);
  const objection = !claim && !check ? findObjection(result, selected) : undefined;
  const go = (ref: string) => (onSelect ?? onOpenRef)?.(ref);

  const nav = (
    <>
      <button type="button" className={s.iconBtn} aria-label="이전 표시" disabled={!canPrev} onClick={() => onStep(-1)}><Chevron direction="left" size={11} /></button>
      <button type="button" className={s.iconBtn} aria-label="다음 표시" disabled={!canNext} onClick={() => onStep(1)}><Chevron size={11} /></button>
    </>
  );

  if (claim) {
    const n = claimNo(result.claims, claim.id);
    const tone = toneOf(claim.verdict);
    const items = result.checklist.map((it, i) => ({ it, key: `c${i}` })).filter(({ it }) => it.claim_id === claim.id);
    const done = items.filter(({ key }) => checked[key]).length;
    // 판정을 정한 이의: 주장 판정과 같은 판정 가운데 타당 표가 가장 많은 것 (표가 있는 이의만)
    const decisive = claim.objections.filter((o) => o.verdict === claim.verdict && o.votes.length > 0)
      .sort((a, b) => b.tally["타당"] - a.tally["타당"])[0];
    // 이 주장에 걸린 문제: 배심원이 판정한 이의 + 근거 숫자의 불일치. 한 줄씩 보이고 누르면 그 문제가 열린다
    const issues = [
      ...claim.objections.map((o) => ({ id: o.id, verdict: o.verdict, page: o.page ?? null, text: o.text, o })),
      ...(claim.code ?? []).map((f) => ({ id: f.check_id, verdict: f.verdict, page: null, text: `숫자: ${f.name}`, o: undefined })),
    ].sort((a, b) => (b.verdict === "수정" ? 1 : 0) - (a.verdict === "수정" ? 1 : 0));
    return (
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div className={s.panelBlock} style={toneVars(tone)}>
          <div className={s.claimHead}>
            <ClaimNumber n={n} tone={tone} size="lg" />
            <div>
              <strong><ClaimName n={n} core={claim.is_core} /></strong>
              <small>{position}, 원문 {claim.page}쪽</small>
            </div>
            {nav}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <span className={s.statusBadge}>{claim.verdict}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: TONE[tone].ink }}>
              {claim.verdict === "수정" ? "판단한 배심원 과반이 이의를 받아들임" : claim.verdict === "유지" ? "중대한 반론 없음" : "근거를 더 봐야 함"}
            </span>
          </div>
          {decisive && (
            // 주장의 판정은 걸린 이의 중 가장 무거운 판정을 따른다. 그 판정을 정한 이의의 배심원 표
            <div className={s.tallyLine} title="이 주장의 판정을 정한 이의의 배심원 표">
              <Mix label={`타당 ${decisive.tally["타당"]}, 확인 불가 ${decisive.tally["확인 불가"]}, 과장 ${decisive.tally["과장"]}`}
                   parts={[{ tone: "red", value: decisive.tally["타당"] }, { tone: "orange", value: decisive.tally["확인 불가"] }, { tone: "green", value: decisive.tally["과장"] }]} />
              <span>배심원 {result.logic.n_jurors}명: 타당 {decisive.tally["타당"]} · 과장 {decisive.tally["과장"]} · 확인 불가 {decisive.tally["확인 불가"]}
                {claim.objections.length > 1 ? " (판정을 정한 이의 기준)" : ""}</span>
            </div>
          )}
          <p className={s.quote}><span>{claim.text}</span></p>
          {onOpenRef && <button type="button" className={s.link} style={{ alignSelf: "flex-start" }} onClick={() => onOpenRef(claim.id)}>원문에서 위치 보기</button>}
        </div>
        <div className={s.panelBlock} style={{ gap: 2 }}>
          <h2 style={{ marginBottom: 6 }}>이 주장에 걸린 문제 <span className={s.muted} style={{ fontWeight: 400 }}>{issues.length}건</span></h2>
          {issues.length === 0 ? <p className={s.panelText}>리스크 검토자가 이의를 내지 않았고, 근거 수치에도 문제가 없습니다.</p> :
            issues.map((it) => (
              <div key={it.id} className={s.issueBlock}>
                <button type="button" className={s.issueRow} style={toneVars(toneOf(it.verdict))} onClick={() => go(it.id)}>
                  <Verdict label={it.verdict} />
                  <span><Linked text={it.text} /></span>
                  <small>{it.page ? `${it.page}쪽` : ""}</small>
                </button>
                {it.o && it.o.votes.length > 0 && (
                  // 배심원의 판정 근거: 이름·표·이유 (누르면 위 줄에서 이의 전체와 판정 과정이 열린다)
                  <ul className={s.miniVotes} aria-label="배심원 판정 근거">
                    {it.o.votes.map((v) => (
                      <li key={v.juror}>
                        <span>{v.juror}</span>
                        <Verdict label={v.vote} />
                        <span>{v.view && <span className={s.voteView}><Linked text={v.view} /></span>}<Linked text={v.reason} /></span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
        </div>
        {claim.ruling && (
          <div className={s.panelBlock}>
            <h2>판사의 판정 근거</h2>
            <p className={s.panelText}><Linked text={claim.ruling.reason} /></p>
            <span className={s.muted} style={{ fontSize: 12 }}>
              투자의견 영향 <b style={{ color: TONE[tone].ink }}>{claim.ruling.impact}</b>{claim.ruling.fix_part ? `, 검토할 부분 ${claim.ruling.fix_part}` : ""}
            </span>
          </div>
        )}
        {items.length > 0 && (
          <Collapsible title="투자 전에 직접 확인할 사항" meta={`${done}/${items.length}`} tinted>
            {items.map(({ it, key }) => (
              <label key={key} className={s.check}>
                <input type="checkbox" checked={!!checked[key]} onChange={() => onToggleCheck(key)} />
                <span><Linked text={it.what} /><small>{checkGroup(it) === "logic" ? "리포트 논리를 확인할 사항" : "리포트 수치를 확인할 사항"}</small></span>
              </label>
            ))}
          </Collapsible>
        )}
        <EvidenceBlock ev={result.evidence?.claims.find((x) => x.claim_id === claim.id)} code={claim.code ?? []} checklist={items.map(({ it }) => it.what)} />
      </div>
    );
  }

  if (check) {
    const tone = toneOf(check.status);
    const src = check.source;
    const rows: [string, string][] = [["리포트 표기", String(check.stated ?? "")]];
    if (check.computed !== null) {
      rows.push([check.kind === "krx" ? "시장 값" : check.kind === "ecos" || check.kind === "kosis" ? "공식 통계" : src ? "공시 값" : "다시 계산", shownComputed(check.computed)]);
      if (check.diff_pct !== null) rows.push(["차이", `${check.diff_pct}%`]);
    }
    return (
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div className={s.panelBlock} style={toneVars(tone)}>
          <div className={s.claimHead}>
            <span className={s.claimNo} data-size="lg" style={{ background: TONE[tone].ink }} aria-hidden="true">#</span>
            <div><strong>{check.kind === "krx" ? "시장 데이터 대조" : check.kind === "ecos" ? "거시 통계 대조" : check.kind === "kosis" ? "산업 통계 대조" : src ? "공시 대조" : "숫자 검증"}</strong><small>{position}, 원문 {check.pages.join(", ")}쪽</small></div>
            {nav}
          </div>
          <h2 className={s.panelTitle}>{check.name}</h2>
          <Verdict label={check.status} strong />
          <dl className={s.facts} style={{ gridTemplateColumns: `repeat(${rows.length}, 1fr)` }}>
            {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd style={{ fontSize: 13.5 }}>{v}</dd></div>)}
          </dl>
          {check.note && <p className={s.panelText}>식: {check.note}</p>}
          {check.reason && <p className={s.panelText}>{check.reason}</p>}
          {src && (
            <dl className={s.sourceList}>
              {(src.display ?? []).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
              <div><dt>작성일 대비</dt><dd style={{ color: src.timing === "작성 당시 공개" ? undefined : TONE.orange.ink }}>{src.timing} (리포트 작성 {src.report_date})</dd></div>
              <div><dt>조회 시점</dt><dd>{src.fetched_at.replace("T", " ")}</dd></div>
              {src.url && <div><dt>원문</dt><dd><a href={src.url} target="_blank" rel="noreferrer" className={s.link}>{src.url.includes("dart.fss") ? "DART에서 공시 원문 보기" : src.url.includes("ecos") ? "한국은행 ECOS 열기" : src.url.includes("kosis") ? "KOSIS 통계표 열기" : "KRX 정보데이터시스템 열기"}</a></dd></div>}
            </dl>
          )}
          <span className={s.muted} style={{ fontSize: 12 }}>{src ? "공식 데이터와 코드가 대조한 결과입니다. AI 의견이 들어가지 않습니다." : "코드가 계산한 결과입니다. AI 의견이 들어가지 않습니다."}</span>
          {onOpenRef && <button type="button" className={s.link} style={{ alignSelf: "flex-start" }} onClick={() => onOpenRef(check.check_id)}>원문에서 위치 보기</button>}
        </div>
      </div>
    );
  }

  if (objection) {
    const tone = toneOf(objection.verdict);
    return (
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div className={s.panelBlock} style={toneVars(tone)}>
          <div className={s.claimHead}>
            <span className={s.claimNo} data-size="lg" style={{ background: TONE[tone].ink }} aria-hidden="true">!</span>
            <div>
              <strong>원문에서 짚은 지적</strong>
              <small>{position}{objection.page ? `, 원문 ${objection.page}쪽` : ""}</small>
            </div>
            {nav}
          </div>
          {objection.claim_id
            ? <button type="button" className={s.link} style={{ alignSelf: "flex-start", fontSize: 12 }} onClick={() => go(objection.claim_id!)}>관련 주장: 주장 {claimNo(result.claims, objection.claim_id)} 보기</button>
            : <span className={s.muted} style={{ fontSize: 12 }}>추출된 주장과 묶이지 않은 지적입니다.</span>}
          {onOpenRef && objection.quote_found && <button type="button" className={s.link} style={{ alignSelf: "flex-start" }} onClick={() => onOpenRef(objection.id)}>원문에서 위치 보기</button>}
        </div>
        <div className={s.panelBlock}>
          <JudgmentTimeline o={objection} nJurors={result.logic.n_jurors} majority={result.logic.majority} />
        </div>
      </div>
    );
  }

  return (
    <div className={s.panelBlock}>
      <h2>검토 패널</h2>
      <p className={s.panelText}>원문의 칠해진 곳이나 목록의 주장을 누르면 이의, 배심원 표, 판사의 글이 여기에 열립니다.</p>
    </div>
  );
}

/** 원문에 표시된 곳 목록 */
export function MarkListPanel({ result, refs, selected, onSelect }: { result: Result; refs: string[]; selected: string | null; onSelect: (r: string) => void }) {
  return (
    <Collapsible title="표시된 곳" meta={`${refs.length}곳`}>
      <div className={s.markList}>
        {refs.map((ref) => {
          const c = result.claims.find((x) => x.id === ref);
          const k = result.numbers.results.find((x) => x.check_id === ref);
          const o = !c && !k ? findObjection(result, ref) : undefined;
          const tone = markTone(result, ref);
          const page = result.marks.find((m) => m.ref === ref)?.page;
          return (
            <button key={ref} type="button" className={s.markRow} style={toneVars(tone)} aria-pressed={selected === ref} onClick={() => onSelect(ref)}>
              {c ? <ClaimNumber n={claimNo(result.claims, ref)} tone={tone} /> : <span className={s.claimNo} data-size="md" style={{ background: TONE[tone].ink }} aria-hidden="true">{o ? "!" : "#"}</span>}
              <span>
                <strong>{c ? c.text : o ? o.text : k?.name}</strong>
                <small>{c ? (c.is_core ? "주장 (핵심)" : "주장") : o ? "원문 지적" : "숫자"}{page ? `, ${page}쪽` : ""}</small>
              </span>
              <Verdict label={c ? c.verdict : o ? o.verdict : k?.status ?? ""} />
            </button>
          );
        })}
      </div>
    </Collapsible>
  );
}

/* ───────── 근거 숫자 (근거 종합표) ───────── */

function EvidenceBlock({ ev, code, checklist }: { ev?: EvidenceClaim; code: CodeFinding[]; checklist: string[] }) {
  if (!ev) return null;
  const tone = toneOf(ev.status);
  const todo = ev.todo.filter((t) => !checklist.includes(t));         // 아래 '직접 확인할 사항'과 겹치는 것은 빼고
  return (
    <Collapsible title="근거 수치와 출처" meta={ev.numbers.length ? `${ev.numbers.length}개` : "없음"} open={false}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span className={s.statusBadge} style={toneVars(toneOf(ev.status))}>{evText(ev.status)}</span>
        <span className={s.muted} style={{ fontSize: 12 }}>수치 근거 신뢰도 <b style={{ color: tone === "none" ? undefined : TONE[tone].ink }}>{ev.confidence}</b></span>
      </div>
      <p className={s.panelText} style={{ fontSize: 12.5 }}>{ev.confidence_why}</p>
      {code.length > 0 && (
        <p className={s.panelText} style={{ fontSize: 12.5, color: TONE.red.ink }}>
          코드 검증 결과로 판정을 올렸습니다: {code.map((c) => `${c.check_id} ${c.name} (${c.verdict})`).join(", ")}
        </p>
      )}
      {ev.numbers.length > 0 && (
        <ul className={s.evList}>
          {ev.numbers.map((n) => {
            const nt = toneOf(n.status);
            const first = n.checks.find((c) => c.status === n.status.replace("공식 ", "").replace("계산 ", "")) ?? n.checks[0];
            return (
              <li key={n.rid}>
                <div className={s.evHead}>
                  <span><b>{n.item}</b> {n.value}{n.page ? <span className={s.muted}> · {n.page}쪽</span> : null}</span>
                  <span className={s.evTag}>{n.nature}</span>
                </div>
                <div className={s.evStatus}>
                  <span style={{ color: nt === "none" ? "var(--muted)" : TONE[nt].ink, fontWeight: 700 }}>{n.status === "근거 미확인" ? "확인 못 함" : evText(n.status)}</span>
                  {first && <span className={s.muted}>{first.check_id} {first.source || first.name}</span>}
                  {first?.url && <a href={first.url} target="_blank" rel="noreferrer" className={s.link}>원문</a>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {todo.length > 0 && (
        <>
          <strong style={{ fontSize: 12 }}>추가로 확인할 것</strong>
          <ul className={s.evTodo}>{todo.map((t) => <li key={t}>{t}</li>)}</ul>
        </>
      )}
      <span className={s.muted} style={{ fontSize: 11.5 }}>숫자가 맞는지만 본 것입니다. 숫자가 맞아도 투자 논리가 옳다는 뜻은 아닙니다.</span>
    </Collapsible>
  );
}

/* ───────── 나의 판단 기록 ───────── */

export function JournalPanel({ result, saveLabel, saveColor, history, onOpenClaim }: {
  result: Result; saveLabel: string; saveColor: string; history: JournalEntry[]; onOpenClaim: (id: string) => void;
}) {
  const open = claimsByPage(result.claims).map((c, i) => ({ c, n: i + 1 })).filter(({ c }) => c.verdict !== "유지");
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div className={s.panelBlock} style={{ gap: 4 }}>
        <h2>저장 상태</h2>
        <span style={{ fontSize: 13, fontWeight: 600, color: saveColor }}>{saveLabel}</span>
        <span className={s.muted} style={{ fontSize: 12 }}>저장할 때마다 아래 이력에 한 줄씩 쌓입니다</span>
      </div>
      <div className={s.panelBlock} style={{ gap: 4 }}>
        <h2 style={{ marginBottom: 6 }}>아직 결론 나지 않은 주장</h2>
        {open.length === 0 ? <span className={s.muted} style={{ fontSize: 12.5 }}>모든 주장이 유지 판정입니다</span> :
          open.map(({ c, n }) => {
            const tone = toneOf(c.verdict);
            return (
              <button key={c.id} type="button" className={s.pointRow} style={toneVars(tone)} onClick={() => onOpenClaim(c.id)}>
                <ClaimNumber n={n} tone={tone} />
                <span>
                  <strong style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.text}</strong>
                  <small>{c.verdict}{c.ruling ? `, 투자의견 영향 ${c.ruling.impact}` : ""}</small>
                </span>
              </button>
            );
          })}
      </div>
      <div className={s.panelBlock} style={{ gap: 0 }}>
        <h2 style={{ marginBottom: 8 }}>이전 판단</h2>
        {history.length === 0 ? <span className={s.muted} style={{ fontSize: 12.5 }}>아직 남긴 판단이 없습니다</span> :
          history.map((h, i) => {
            const d = new Date(h.at);
            return (
              <div key={`${h.at}-${i}`} className={s.historyRow}>
                <time dateTime={h.at}>{d.toLocaleDateString("ko-KR", { month: "long", day: "numeric" })}<br />{d.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })}</time>
                <div>
                  <span style={{ fontSize: 13 }}>
                    {h.before && <span className={s.muted}>{h.before} → </span>}
                    <strong style={{ color: ACTION_TONE[h.action] ? TONE[ACTION_TONE[h.action]].ink : undefined }}>{h.action}</strong>
                    {h.changed && <span style={{ marginLeft: 6, fontSize: 11.5, fontWeight: 600, color: TONE.orange.ink }}>바뀜</span>}
                    <span className={s.muted}>, {h.agreement}</span>
                  </span>
                  <p>{h.note}</p>
                </div>
              </div>
            );
          })}
      </div>
    </div>
  );
}

/* ───────── 분석 전, 분석 중, 멈춤 ───────── */

export function EntryPanel({ entry }: { entry: LibraryEntry }) {
  const rows: [string, string][] = [
    ["파일", entry.fileName],
    ["올린 시각", new Date(entry.uploadedAt).toLocaleString("ko-KR")],
    ["크기", `${(entry.size / 1024 / 1024).toFixed(1)}MB`],
  ];
  if (entry.estimate?.pages) rows.push(["쪽수", `${entry.estimate.pages}쪽`]);
  if (entry.startedAt) rows.push(["분석 시작", new Date(entry.startedAt).toLocaleTimeString("ko-KR")]);
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div className={s.panelBlock} style={{ gap: 0 }}>
        <h2 style={{ marginBottom: 8 }}>올린 파일</h2>
        <dl style={{ margin: 0 }}>
          {rows.map(([k, v]) => (
            <div key={k} className={s.historyRow} style={{ gridTemplateColumns: "72px 1fr" }}>
              <dt className={s.muted} style={{ fontSize: 12 }}>{k}</dt>
              <dd style={{ margin: 0, fontSize: 12.5, wordBreak: "break-all" }}>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className={s.panelBlock}>
        <h2>분석 순서</h2>
        <ol className={s.steps}>
          <li>추출 담당이 원문에서 주장과 숫자를 뽑고, 코드가 원문과 글자 그대로 대조합니다</li>
          <li>리스크 검토자가 주장마다 이의를 냅니다</li>
          <li>배심원 5명이 서로 보지 않고 투표합니다</li>
          <li>코드가 표를 세고, 판사가 판결문을 씁니다</li>
        </ol>
      </div>
    </div>
  );
}

export function WelcomePanel({ total, running }: { total: number; running?: LibraryEntry }) {
  return (
    <div className={s.panelBlock}>
      <h2>보관함</h2>
      <p className={s.panelText}>
        {total ? `올린 리포트 ${total}건이 왼쪽 보관함에 있습니다.` : "올린 리포트가 아직 없습니다."}
        {running ? ` 지금 ${running.company ?? running.fileName}을(를) 분석하고 있습니다.` : ""}
      </p>
      <p className={s.panelText}>올린 파일과 결과는 이 컴퓨터의 프로젝트 폴더(library)에만 저장됩니다.</p>
    </div>
  );
}
