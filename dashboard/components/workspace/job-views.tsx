"use client";

// 분석 결과가 아직 없을 때의 화면: 첫 화면, 올린 뒤 비용 확인, 분석 중, 멈춤
import { useEffect, useState, type DragEvent } from "react";
import type { LibraryEntry } from "@/lib/types";
import { pickPdf } from "./chrome";
import { Spinner } from "./primitives";
import s from "./workspace.module.css";

const fmt = (n?: number) => (n ?? 0).toLocaleString("ko-KR");
const mb = (b: number) => `${(b / 1024 / 1024).toFixed(1)}MB`;

function elapsed(fromIso?: string, now = Date.now()) {
  if (!fromIso) return "";
  const sec = Math.max(0, Math.round((now - new Date(fromIso).getTime()) / 1000));
  const m = Math.floor(sec / 60), r = sec % 60;
  return m ? `${m}분 ${r}초` : `${r}초`;
}

export function WelcomeView({ onUpload, uploading, error }: { onUpload: (f: File) => void; uploading: boolean; error: string | null }) {
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = pickPdf(e.dataTransfer.files);
    if (f) onUpload(f);
  };
  return (
    <div className={s.stage}>
      <h2>리서치 리포트 PDF를 올리면, AI 리스크 검토자와 배심원이 주장마다 반대신문을 합니다</h2>
      <p>리포트는 고치지 않고 평가만 합니다. 숫자는 코드가 다시 계산하고, 판정 규칙도 코드가 정합니다.</p>
      <div className={s.bigDrop} data-over={over} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
        {uploading ? (
          <strong style={{ display: "flex", alignItems: "center", gap: 10 }}><Spinner />올리는 중입니다</strong>
        ) : (
          <>
            <strong>PDF 파일을 여기에 끌어다 놓으세요</strong>
            <span>글자를 고를 수 있는 PDF여야 합니다. 스캔한 이미지 PDF는 읽지 못합니다.</span>
            <label className={s.btnPrimary} style={{ marginTop: 6, cursor: "pointer" }}>
              파일 고르기
              <input type="file" accept="application/pdf,.pdf" className={s.visuallyHidden}
                     onChange={(e) => { const f = pickPdf(e.target.files); if (f) onUpload(f); e.target.value = ""; }} />
            </label>
          </>
        )}
        {error && <p className={s.uploadError} role="alert">{error}</p>}
      </div>
      <ol className={s.howList}>
        <li>PDF를 올립니다<span>올리기만 해서는 비용이 들지 않습니다. 쪽수와 예상 사용량을 먼저 보여줍니다.</span></li>
        <li>비용을 확인하고 분석을 시작합니다<span>AI를 8번 부릅니다. 보통 3분에서 10분 걸리고, 그동안 다른 리포트를 볼 수 있습니다.</span></li>
        <li>결과를 읽고 내 판단을 남깁니다<span>원문 위에 판정 색이 칠해지고, 주장마다 이의와 배심원 표, 판사의 글을 볼 수 있습니다.</span></li>
      </ol>
    </div>
  );
}

function DeleteButton({ onDelete, label = "보관함에서 지우기" }: { onDelete: () => void; label?: string }) {
  const [ask, setAsk] = useState(false);
  if (!ask) return <button type="button" className={s.btn} onClick={() => setAsk(true)}>{label}</button>;
  return (
    <span className={s.rowActions} role="group" aria-label="지우기 확인">
      <span style={{ fontSize: 12.5 }}>올린 파일과 결과가 모두 지워집니다.</span>
      <button type="button" className={s.btnDanger} onClick={onDelete}>지우기</button>
      <button type="button" className={s.btn} onClick={() => setAsk(false)}>취소</button>
    </span>
  );
}

export function ReadyView({ entry, busy, error, onStart, onDelete, otherRunning }: {
  entry: LibraryEntry; busy: boolean; error: string | null; onStart: () => void; onDelete: () => void; otherRunning?: string;
}) {
  const est = entry.estimate;
  const unreadable = !!est?.error;
  return (
    <div className={s.stage}>
      <h2>올린 리포트를 분석할 준비가 됐습니다</h2>
      <p>{entry.fileName} ({mb(entry.size)}). 아래 사용량을 확인하고 시작을 누르면 AI를 부릅니다. 이 단계부터 비용이 발생합니다.</p>
      {unreadable ? (
        <p className={s.errorBox}>{est?.error} 다른 PDF를 올려 주세요.</p>
      ) : (
        <dl className={s.costTable}>
          <div><dt>쪽수</dt><dd>{est?.pages ?? "?"}<small>쪽</small></dd></div>
          <div><dt>AI 호출</dt><dd>{est?.calls ?? 8}<small>번</small></dd></div>
          <div><dt>사용 모델</dt><dd style={{ fontSize: 13.5 }}>{est?.model ?? "—"}</dd></div>
          <div><dt>최대 사용량</dt><dd style={{ fontSize: 13.5 }}>입력 {fmt(est?.inputTokens)}<br />출력 {fmt(est?.outputTokens)}<small>토큰</small></dd></div>
        </dl>
      )}
      {!unreadable && (
        <p className={s.notice}>
          최대 사용량은 넉넉하게 잡은 상한이라 실제로는 대부분 이보다 적게 씁니다. 정확한 요금은 Google AI Studio의 사용량 화면에서 확인할 수 있습니다.
        </p>
      )}
      {error && <p className={s.errorBox} role="alert">{error}</p>}
      <div className={s.rowActions}>
        {!unreadable && (
          <button type="button" className={s.btnPrimary} style={{ height: 40, padding: "0 18px" }} disabled={busy || !!otherRunning} onClick={onStart}>
            {busy ? <><Spinner />시작하는 중</> : "비용을 확인했고, 분석을 시작합니다"}
          </button>
        )}
        <DeleteButton onDelete={onDelete} label={unreadable ? "지우기" : "취소하고 지우기"} />
      </div>
      {otherRunning && <p className={s.muted} style={{ margin: 0, fontSize: 12.5 }}>{otherRunning} 분석이 끝나면 시작할 수 있습니다.</p>}
    </div>
  );
}

export function StageList({ entry, now }: { entry: LibraryEntry; now?: number }) {
  const firstOpen = entry.stages.findIndex((x) => !x.done);
  return (
    <ol className={s.stageList}>
      {entry.stages.map((st, i) => {
        const state = st.done ? "done" : i === firstOpen && entry.state === "running" ? "now" : "todo";
        return (
          <li key={st.label} data-state={state}>
            {state === "now" ? <Spinner /> : <i className={s.dot} data-done={st.done} aria-hidden="true" />}
            <span>{st.label}</span>
            <small>{state === "done" ? "끝남" : state === "now" ? elapsed(entry.startedAt, now) : ""}</small>
          </li>
        );
      })}
    </ol>
  );
}

export function RunningView({ entry }: { entry: LibraryEntry }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className={s.stage} aria-live="polite">
      <h2>반대신문을 진행하고 있습니다</h2>
      <p>
        시작한 지 {elapsed(entry.startedAt, now)} 지났습니다. 보통 3분에서 10분 걸립니다.
        이 창을 닫거나 다른 리포트를 봐도 분석은 계속되고, 끝나면 여기에 결과가 나타납니다.
      </p>
      <StageList entry={entry} now={now} />
    </div>
  );
}

export function FailedView({ entry, busy, error, onRetry, onDelete }: {
  entry: LibraryEntry; busy: boolean; error: string | null; onRetry: () => void; onDelete: () => void;
}) {
  const daily = /하루 사용 한도/.test(entry.error ?? "");
  const server = !daily && /503|504|500|429|UNAVAILABLE|DEADLINE|일시 오류|늦어|시간 안에/.test(entry.error ?? "");
  return (
    <div className={s.stage}>
      <h2>분석이 끝까지 가지 못했습니다</h2>
      <p className={s.errorBox}>{entry.error}</p>
      <p>
        {daily
          ? "Google이 정한 하루 사용 한도를 다 썼습니다. 오늘은 다시 시도해도 같은 결과가 나옵니다. 위에 적힌 시간이 지난 뒤 다시 시도하거나, Google AI Studio에서 결제를 켜 등급을 올리면 바로 쓸 수 있습니다."
          : server
          ? "AI 회사 서버가 바빠서 생긴 일시적인 문제로 보입니다. 프로그램이 이미 몇 번 기다렸다 다시 불러 봤습니다. 몇 분 뒤 다시 시도하면 대부분 됩니다. 실패한 호출은 보통 요금이 청구되지 않습니다."
          : "다시 시도해도 같은 문제가 나오면, 결과 폴더의 log.txt 를 확인하거나 다른 PDF로 해 보세요."}
        {" "}다시 시도하면 끝난 단계는 건너뛰고 멈춘 곳부터 이어서 합니다.
      </p>
      {entry.stages.length > 0 && <StageList entry={entry} />}
      {error && <p className={s.errorBox} role="alert">{error}</p>}
      <div className={s.rowActions}>
        <button type="button" className={s.btnPrimary} disabled={busy} onClick={onRetry}>
          {busy ? <><Spinner />시작하는 중</> : "다시 시도 (비용 발생)"}
        </button>
        <DeleteButton onDelete={onDelete} />
      </div>
    </div>
  );
}
