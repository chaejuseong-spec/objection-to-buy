// 색은 판정에만 쓴다. 빨강 = 수정·불일치, 주황 = 확인 필요, 초록 = 유지·일치. 그 밖의 강조는 잉크색.
import type { CSSProperties } from "react";

export type Tone = "red" | "orange" | "green" | "none";

const BY_LABEL: Record<string, Tone> = {
  수정: "red", 불일치: "red", "재검토 필요": "red", 재심리: "red", 동의: "red",
  "확인 필요": "orange", "판정 보류": "orange", "판단 불가": "orange", "확인 불가": "orange", "검증 불가": "orange",
  "조건부 통과": "orange", "판정 불완전": "orange",
  유지: "green", 일치: "green", 통과: "green", 비동의: "green",
  "실제 수치 불일치": "red", "일부 확인": "orange", "근거 확인": "green", "공식 일치": "green", "계산 일치": "green",
  "근거 미확인": "none", "숫자 근거 없음": "none",
};
export const toneOf = (label?: string | null): Tone => (label ? BY_LABEL[label] ?? "none" : "none");

/** 주장의 수치 근거 상태를 화면에 보일 말 (파이썬 evidence.py 의 값은 그대로 두고 보이는 말만 바꾼다).
 *  '근거'는 논리 근거로 읽히기 쉬워, 숫자만 본 결과라는 뜻이 드러나게 '수치'로 쓴다 */
export const EV_TEXT: Record<string, string> = {
  "근거 확인": "수치 맞음", "일부 확인": "수치 일부 맞음", "근거 미확인": "수치 확인 못 함",
  "실제 수치 불일치": "수치 틀림", "숫자 근거 없음": "수치 근거 없음",
  // 숫자 하나의 상태
  "공식 일치": "공식 자료와 맞음", "계산 일치": "다시 계산해 맞음",
};
export const evText = (status: string) => EV_TEXT[status] ?? status;

export const TONE: Record<Tone, { ink: string; line: string; mark: string; soft: string }> = {
  red: { ink: "#B42318", line: "#D92D20", mark: "rgba(217, 45, 32, 0.20)", soft: "#FDF1F0" },
  orange: { ink: "#B54708", line: "#DC6803", mark: "rgba(220, 104, 3, 0.24)", soft: "#FDF6EC" },
  green: { ink: "#067647", line: "#17B26A", mark: "rgba(23, 178, 106, 0.18)", soft: "#EFF8F3" },
  none: { ink: "#5C626B", line: "#A3A8AE", mark: "rgba(92, 98, 107, 0.12)", soft: "#F4F5F3" },
};

/** 원문 위 숫자 문제 표시 색 (판정 색과 구분되게 보라). 2026-11-21 */
export const NUMBER_COLOR = { ink: "#5B3CC4", line: "#7A5AF8", mark: "rgba(122, 90, 248, 0.20)", soft: "#F4F1FE" };
export const numberVars = (): CSSProperties => ({
  ["--ink-tone" as string]: NUMBER_COLOR.ink, ["--tone-line" as string]: NUMBER_COLOR.line,
  ["--mark" as string]: NUMBER_COLOR.mark, ["--soft" as string]: NUMBER_COLOR.soft,
});

/** 판정 색을 CSS 변수로 넘긴다. 스타일시트는 var(--ink-tone) 등으로 읽는다. */
export function toneVars(tone: Tone): CSSProperties {
  const t = TONE[tone];
  return {
    ["--ink-tone" as string]: t.ink,
    ["--tone-line" as string]: t.line,
    ["--mark" as string]: t.mark,
    ["--soft" as string]: t.soft,
  };
}

/** 리포트 평가를 한 문장으로 */
export const GRADE_TEXT: Record<string, string> = {
  "재검토 필요": "핵심 주장이나 투자 결론을 바꿀 수 있는 곳에 수정이 있어 다시 검토해야 합니다",
  "판정 불완전": "핵심 주장의 판정을 내리지 못했습니다(AI 호출 실패 등). 다시 분석해 보세요",
  "조건부 통과": "결론은 유지되지만 고칠 점이나 핵심 주장에 확인할 곳이 남았습니다",
  통과: "주장과 숫자에서 수정할 곳을 찾지 못했습니다",
};

/** 숫자 검증의 '다시 계산' 칸. 공시 대조는 단위가 붙은 글자로 온다. */
export const shownComputed = (v: number | string | null) =>
  v === null ? "" : typeof v === "string" ? v : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 });

/** 값 뒤에 붙일 단위. 값에 이미 단위가 붙어 있으면 빈 글자 ('54.7%' + '%' 를 '54.7%%' 로 찍지 않는다) */
export const unitAfter = (value: string, unit?: string | null) => {
  const u = (unit ?? "").trim();
  return !u || value.replace(/\s/g, "").endsWith(u.replace(/\s/g, "")) ? "" : u;
};
