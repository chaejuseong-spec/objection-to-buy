import type { ReactNode } from "react";
import { TONE, toneOf, toneVars, type Tone } from "./tone";
import s from "./workspace.module.css";

/** 주장 번호 배지. "C-1" 같은 코드 대신 각주처럼 1, 2, 3 으로 쓴다. */
export function ClaimNumber({ n, tone, size = "md", muted }: { n: number | string; tone: Tone; size?: "sm" | "md" | "lg"; muted?: boolean }) {
  return (
    <span className={s.claimNo} data-size={size} style={{ background: muted ? "#A3A8AE" : TONE[tone].ink }} aria-hidden="true">
      {n}
    </span>
  );
}

/** 주장 이름: 모두 '주장 n'. 투자의견을 직접 떠받치는 주장에는 작은 '핵심' 표시 */
export function ClaimName({ n, core }: { n: number; core?: boolean }) {
  return (
    <>
      주장 {n}
      {core && <span className={s.coreTag} title="투자의견 핵심 주장: 목표주가·밸류에이션처럼 매수 의견을 직접 떠받치는 주장. 이 주장이 '수정'이면 리포트 전체가 재심리가 됩니다">핵심</span>}
    </>
  );
}

/** 설명 표시: 작은 동그라미 물음표. 마우스를 올리거나 키보드로 고르면 설명 말풍선이 뜬다 */
export function InfoTip({ text }: { text: string }) {
  return (
    <span className={s.infoTip} tabIndex={0} role="note" aria-label={text} data-tip={text}>?</span>
  );
}

/** 판정 글자. strong 이면 색 바탕 배지. */
export function Verdict({ label, strong }: { label: string; strong?: boolean }) {
  const tone = toneOf(label);
  return (
    <span className={strong ? s.statusBadge : s.verdictText} style={toneVars(tone)}>
      {label}
    </span>
  );
}

export function Chevron({ direction = "right", size = 9 }: { direction?: "left" | "right"; size?: number }) {
  return (
    <svg className={direction === "right" ? s.chev : undefined} aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={2.2}>
      <path d={direction === "right" ? "M6 3l5 5-5 5" : "M10 3L5 8l5 5"} />
    </svg>
  );
}

export function Collapsible({ title, meta, tinted, open = true, children }: { title: string; meta?: string; tinted?: boolean; open?: boolean; children: ReactNode }) {
  return (
    <details open={open} className={s.collapsible} data-tinted={tinted || undefined}>
      <summary>
        <Chevron />
        {title}
        {meta && <small>{meta}</small>}
      </summary>
      <div className={s.collapsibleBody}>{children}</div>
    </details>
  );
}

/** 표 비율 막대: 칸마다 색. 한 칸 = 한 건(또는 한 표). */
export function Mix({ parts, label }: { parts: { tone: Tone; value: number }[]; label: string }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  return (
    <div className={s.mix} role="img" aria-label={label}>
      {total === 0 ? <div style={{ flex: 1, background: "var(--line)" }} /> : null}
      {parts.filter((p) => p.value > 0).map((p) => (
        <div key={p.tone} style={{ flex: p.value, background: TONE[p.tone].ink }} />
      ))}
    </div>
  );
}

export function Spinner() {
  return <span className={s.spinner} aria-hidden="true" />;
}

/** 불러오는 동안 보여줄 뼈대. 실제 화면처럼 제목 -> 핵심 수치 -> 목록 모양. 실제 내용이 오면 바로 바뀐다 */
export function Skeleton({ label }: { label: string }) {
  return (
    <div className={s.skeleton} role="status" aria-label={label}>
      <span style={{ width: "46%", height: 26 }} />
      <span style={{ width: "30%", height: 12 }} />
      <div className={s.skeletonRow}>
        {[0, 1, 2, 3].map((i) => <span key={i} style={{ height: 52 }} />)}
      </div>
      {[0, 1, 2, 3, 4].map((i) => <span key={i} style={{ width: `${88 - i * 7}%`, height: 14 }} />)}
    </div>
  );
}
