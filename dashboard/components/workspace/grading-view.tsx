"use client";

// 채점 탭: 내가 쓴 정답지와 에이전트 결과를 맞춰 보고 점수를 기록한다.
import { allObjections, pct, score, type AnswerItem, type AnswerKey, type Grading, type NumMark, type ObjMark, type QualityRun, type ScoreRecord, type Weight } from "@/lib/grading";
import type { Result } from "@/lib/types";
import { Verdict } from "./primitives";
import { shownComputed, TONE } from "./tone";
import s from "./workspace.module.css";

const WEIGHTS: Weight[] = ["상", "중", "하"];
const KINDS = ["사실 오류", "해석 차이"] as const;

const clip = (t: string, n = 70) => (t.length > n ? `${t.slice(0, n)}…` : t);

export function GradingView({ result, answer, grading, saveLabel, onAnswer, onGrading }: {
  result: Result; answer: AnswerKey; grading: Grading; saveLabel: string;
  onAnswer: (k: AnswerKey) => void; onGrading: (g: Grading) => void;
}) {
  const objs = allObjections(result);
  const items = answer.items;
  const hitBy = new Map<string, number[]>();      // 이의 id -> 그 이의가 짚은 정답 번호들
  items.forEach((a, i) => (grading.found[a.id] ?? []).forEach((oid) => hitBy.set(oid, [...(hitBy.get(oid) ?? []), i + 1])));

  const setItem = (i: number, patch: Partial<AnswerItem>) =>
    onAnswer({ ...answer, items: items.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const addItem = () => {
    const n = items.reduce((m, a) => Math.max(m, Number(a.id.replace(/\D/g, "")) || 0), 0) + 1;
    onAnswer({ ...answer, items: [...items, { id: `A-${n}`, claim: "", text: "", kind: "", weight: "중" }] });
  };
  const removeItem = (i: number) => {
    const id = items[i].id;
    const found = { ...grading.found };
    delete found[id];
    onAnswer({ ...answer, items: items.filter((_, j) => j !== i) });
    onGrading({ ...grading, found });
  };
  const toggleFound = (aid: string, oid: string) => {
    const cur = grading.found[aid] ?? [];
    onGrading({ ...grading, found: { ...grading.found, [aid]: cur.includes(oid) ? cur.filter((x) => x !== oid) : [...cur, oid] } });
  };
  const markObj = (oid: string, m: ObjMark) => {
    const objMarks = { ...grading.objMarks };
    if (objMarks[oid] === m) delete objMarks[oid]; else objMarks[oid] = m;
    onGrading({ ...grading, objMarks });
  };
  const markNum = (cid: string, m: NumMark) => {
    const numberMarks = { ...grading.numberMarks };
    if (numberMarks[cid] === m) delete numberMarks[cid]; else numberMarks[cid] = m;
    onGrading({ ...grading, numberMarks });
  };

  return (
    <div className={s.grading}>
      <div className={s.journalHead}>
        <h2>{result.meta.name} 채점</h2>
        <span role="status" className={s.muted} style={{ fontSize: 12.5 }}>{saveLabel}</span>
      </div>
      {result.run.mock && (
        <p className={s.demoNote} style={{ margin: 0 }}>
          시연용 가짜 답으로 만든 결과라 점수에는 의미가 없습니다. 정답지는 지금 써 두면, 같은 PDF를 올려 실제로 분석했을 때 그대로 이어집니다.
        </p>
      )}

      {/* 1. 정답지 */}
      <section aria-labelledby="g-answer" className={s.gradeSection}>
        <div className={s.sectionHead}>
          <h3 id="g-answer">1. 내가 찾은 문제 <small>{items.length}개</small></h3>
          <span className={s.help}>에이전트 결과를 보기 전에 리포트를 읽고 적으면 더 공정합니다. 같은 PDF면 다시 분석해도 이 목록을 씁니다.</span>
        </div>
        {items.length === 0 && <p className={s.gradeEmpty}>아직 적은 문제가 없습니다. 아래 단추로 첫 문제를 추가하세요.</p>}
        <ol className={s.gradeList}>
          {items.map((a, i) => {
            const linked = grading.found[a.id] ?? [];
            return (
              <li key={a.id} className={s.gradeItem}>
                <div className={s.gradeItemHead}>
                  <strong>문제 {i + 1}</strong>
                  <label className={s.gradeSelect}>
                    <span className={s.visuallyHidden}>관련 주장</span>
                    <select value={a.claim} onChange={(e) => setItem(i, { claim: e.target.value })}>
                      <option value="">관련 주장 고르기</option>
                      {result.claims.map((c, j) => <option key={c.id} value={c.id}>주장 {j + 1}: {clip(c.text, 34)}</option>)}
                      <option value="none">주장과 무관 (리포트 전체)</option>
                    </select>
                  </label>
                  <div className={s.segmented} role="group" aria-label="종류">
                    {KINDS.map((k) => <button key={k} type="button" aria-pressed={a.kind === k} onClick={() => setItem(i, { kind: a.kind === k ? "" : k })}>{k}</button>)}
                  </div>
                  <div className={s.segmented} role="group" aria-label="중요도">
                    {WEIGHTS.map((w) => <button key={w} type="button" aria-pressed={a.weight === w} onClick={() => setItem(i, { weight: w })}>중요도 {w}</button>)}
                  </div>
                  <button type="button" className={s.linkBtn} onClick={() => removeItem(i)}>지우기</button>
                </div>
                <textarea aria-label={`문제 ${i + 1} 내용`} rows={2} placeholder="무엇이 왜 문제인지 한두 문장으로 (예: 2026E 배당금수익 추정에 근거가 없다)"
                          value={a.text} onChange={(e) => setItem(i, { text: e.target.value })} />
                <fieldset className={s.matchBox}>
                  <legend>
                    에이전트가 이 문제를 짚었나요?{" "}
                    <span style={{ color: linked.length ? TONE.green.ink : TONE.red.ink }}>{linked.length ? `짚음 (${linked.length}개)` : "못 찾음"}</span>
                  </legend>
                  {objs.length === 0 ? <span className={s.muted}>에이전트가 낸 이의가 없습니다</span> : objs.map(({ o, claimNo: n }) => (
                    <label key={o.id} className={s.matchOpt}>
                      <input type="checkbox" checked={linked.includes(o.id)} onChange={() => toggleFound(a.id, o.id)} />
                      <span><b>주장 {n}</b> {o.who}, {o.doubt_type}: {clip(o.text, 80)}</span>
                    </label>
                  ))}
                </fieldset>
              </li>
            );
          })}
        </ol>
        <button type="button" className={s.btn} style={{ alignSelf: "flex-start" }} onClick={addItem}>+ 문제 추가</button>
      </section>

      {/* 2. 에이전트 이의 */}
      <section aria-labelledby="g-obj" className={s.gradeSection}>
        <div className={s.sectionHead}>
          <h3 id="g-obj">2. 에이전트가 낸 이의 <small>{objs.length}개</small></h3>
          <span className={s.help}>내 문제 목록에 없는 이의는, 맞는 지적인지 헛짚은 것인지 골라 주세요.</span>
        </div>
        <ul className={s.gradeRows}>
          {objs.map(({ o, claimNo: n }) => {
            const hit = hitBy.get(o.id);
            return (
              <li key={o.id}>
                <div className={s.gradeRowText}>
                  <span className={s.gradeMeta}>주장 {n}, {o.who}, {o.doubt_type} <Verdict label={o.verdict} /></span>
                  <p>{o.text}</p>
                </div>
                {hit ? (
                  <span className={s.gradeTag} style={{ color: TONE.green.ink }}>내 문제 {hit.join(", ")}와 같음</span>
                ) : (
                  <div className={s.segmented} role="group" aria-label="이 이의 평가">
                    <button type="button" aria-pressed={grading.objMarks[o.id] === "valid"} onClick={() => markObj(o.id, "valid")}>맞는 추가 지적</button>
                    <button type="button" aria-pressed={grading.objMarks[o.id] === "miss"} onClick={() => markObj(o.id, "miss")}>헛짚음</button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {/* 3. 숫자 검증 */}
      <section aria-labelledby="g-num" className={s.gradeSection}>
        <div className={s.sectionHead}>
          <h3 id="g-num">3. 숫자 검증 <small>{result.numbers.results.length}건</small></h3>
          <span className={s.help}>코드가 내린 결과(일치, 불일치, 확인 불가)가 맞는지 원문을 보고 골라 주세요.</span>
        </div>
        {result.numbers.results.length === 0 && <p className={s.gradeEmpty}>이 분석에는 숫자 검증이 없습니다.</p>}
        <ul className={s.gradeRows}>
          {result.numbers.results.map((c) => (
            <li key={c.check_id}>
              <div className={s.gradeRowText}>
                <span className={s.gradeMeta}>{c.name} <Verdict label={c.status} /></span>
                <p>{[c.stated ? `리포트 ${c.stated}` : "", c.computed !== null ? `${c.kind === "krx" ? "시장" : c.kind === "ecos" || c.kind === "kosis" ? "통계" : c.source ? "공시" : "다시 계산"} ${shownComputed(c.computed)}` : "", c.reason].filter(Boolean).join(", ")}</p>
              </div>
              <div className={s.segmented} role="group" aria-label="이 검증 결과 평가">
                <button type="button" aria-pressed={grading.numberMarks[c.check_id] === "right"} onClick={() => markNum(c.check_id, "right")}>결과가 맞음</button>
                <button type="button" aria-pressed={grading.numberMarks[c.check_id] === "wrong"} onClick={() => markNum(c.check_id, "wrong")}>결과가 틀림</button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/* ───────── 오른쪽: 점수 ───────── */

export function ScorePanel({ result, answer, grading, history, quality, busy, onRecord }: {
  result: Result; answer: AnswerKey; grading: Grading; history: ScoreRecord[]; quality: QualityRun[]; busy: boolean; onRecord: () => void;
}) {
  const sc = score(result, answer, grading);
  const c = sc.counts;
  const left = [c.ungradedObjections ? `이의 ${c.ungradedObjections}개` : "", c.ungradedNumbers ? `숫자 검증 ${c.ungradedNumbers}건` : ""].filter(Boolean);
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div className={s.panelBlock}>
        <h2>지금 점수</h2>
        <dl className={s.statGrid} style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <div><dt>찾은 비율</dt><dd>{pct(sc.recall)}</dd></div>
          <div><dt>헛짚은 비율</dt><dd>{pct(sc.missRate)}</dd></div>
          <div><dt>숫자 정확도</dt><dd>{pct(sc.numberAcc)}</dd></div>
        </dl>
        <p className={s.panelText} style={{ fontSize: 12.5 }}>
          내 문제 {c.answers}개 중 {c.found}개를 찾았습니다(중요도 반영 {pct(sc.weightedRecall)}).
          채점한 이의 {c.hits + c.valid + c.miss}개 중 {c.miss}개가 헛짚음, 숫자 검증 {c.right + c.wrong}건 중 {c.right}건이 맞았습니다.
        </p>
        {left.length > 0 && <p className={s.panelText} style={{ fontSize: 12, color: TONE.orange.ink }}>아직 채점하지 않은 것: {left.join(", ")}</p>}
        <button type="button" className={s.btnPrimary} disabled={busy || c.answers === 0} onClick={onRecord}>
          이 점수 기록하기
        </button>
        <span className={s.muted} style={{ fontSize: 12 }}>
          {result.run.mock ? "시연용 결과라 기록해도 비교에는 쓰지 마세요." : "에이전트를 고친 뒤 다시 분석하고 채점하면, 아래에서 버전끼리 비교할 수 있습니다."}
        </span>
      </div>

      <div className={s.panelBlock} style={{ gap: 0 }}>
        <h2 style={{ marginBottom: 8 }}>점수 기록 <span className={s.muted} style={{ fontWeight: 400 }}>같은 리포트</span></h2>
        {history.length === 0 ? <span className={s.muted} style={{ fontSize: 12.5 }}>아직 기록한 점수가 없습니다</span> : (
          <table className={s.scoreTable}>
            <thead><tr><th>기록</th><th>찾음</th><th>헛짚음</th><th>숫자</th></tr></thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.at}>
                  <td>
                    {new Date(h.at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}{" "}
                    <small>{h.version ? `${h.version.commit}${h.version.dirty ? "+수정" : ""}, ${h.version.model}` : "버전 기록 없음"}{h.mock ? ", 시연용" : ""}</small>
                  </td>
                  <td>{pct(h.recall)}</td>
                  <td>{pct(h.missRate)}</td>
                  <td>{pct(h.numberAcc)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className={s.panelBlock} style={{ gap: 0 }}>
        <h2 style={{ marginBottom: 4 }}>자동 오류 삽입 테스트 <span className={s.muted} style={{ fontWeight: 400 }}>숫자 오류</span></h2>
        <span className={s.muted} style={{ fontSize: 12, marginBottom: 8 }}>
          확인된 숫자를 일부러 틀리게 바꾼 사본으로, 에이전트가 오류를 몇 개 잡는지 코드가 잽니다. 테스트 자료이며 실제 분석 결과와 따로 저장됩니다.
        </span>
        {quality.length === 0 ? (
          <span className={s.muted} style={{ fontSize: 12.5 }}>
            아직 측정하지 않았습니다. 터미널에서 python -m tribunal.quality make library/{"<id>"}/run 으로 세트를 만들고 run --yes 로 잽니다.
          </span>
        ) : (
          <table className={s.scoreTable}>
            <thead><tr><th>기록</th><th>탐지율</th><th>오탐</th><th>검증 불가</th></tr></thead>
            <tbody>
              {quality.map((q) => (
                <tr key={q.at}>
                  <td>
                    {new Date(q.at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}{" "}
                    <small>{q.version.commit}{q.version.dirty ? "+수정" : ""}, {q.model}, 오류 {q.metrics.injected}개 중 {q.metrics.caught}개</small>
                  </td>
                  <td>{pct(q.metrics.detect_rate_checkable)}<small>전체 {pct(q.metrics.detect_rate_all)}</small></td>
                  <td>{q.metrics.extra_false_alarms}</td>
                  <td>{pct(q.metrics.unverifiable_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className={s.panelBlock}>
        <h2>채점하는 법</h2>
        <ol className={s.steps}>
          <li>리포트를 읽고 내가 보기에 문제인 곳을 1번에 적습니다.</li>
          <li>문제마다 에이전트 이의 중 같은 것을 체크합니다.</li>
          <li>2번에서 남은 이의를 맞는 지적 또는 헛짚음으로 고릅니다.</li>
          <li>3번에서 숫자 검증 결과가 맞았는지 고릅니다.</li>
          <li>이 점수 기록하기를 누릅니다. 고칠 때마다 반복해 비교합니다.</li>
        </ol>
      </div>
    </div>
  );
}
