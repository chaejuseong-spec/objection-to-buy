"use client";

// AI가 쓴 글 속의 숫자 id(R-09)를 사람이 읽는 이름으로 바꿔 보여준다. 마우스를 올리면 값과 쪽이 보인다.
// 계산은 하지 않고, 분석 결과(result.rows)에 있는 이름을 그대로 쓴다. 없는 id는 그대로 둔다.
import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { Result } from "@/lib/types";
import s from "./workspace.module.css";

type Row = Result["rows"][number];
const RowsContext = createContext<Map<string, Row>>(new Map());

export function RowsProvider({ rows, children }: { rows: Row[]; children: ReactNode }) {
  const map = useMemo(() => new Map(rows.map((r) => [r.rid, r])), [rows]);
  return <RowsContext.Provider value={map}>{children}</RowsContext.Provider>;
}

// 괄호 안에 id만 나열한 것 '(R-05, R-09, R-45)' 또는 글 속의 id 하나 'R-09'
const TOKEN = /\(\s*[RM]-\d+(?:\s*[,·]\s*[RM]-\d+)*\s*\)|\b[RM]-\d+\b/g;
const ONE = /[RM]-\d+/g;

function describe(r: Row) {
  const unit = r.unit && /\d$/.test(r.value) ? r.unit : "";
  return `${r.item}: ${r.value}${unit}${r.page ? ` · 원문 ${r.page}쪽` : ""}`;
}

/** 글 속의 R-# 를 정리한다. 괄호 안 id 목록 '(R-05, R-09)'은 지우고, 문장 속 id 하나는 그 숫자의 이름으로 바꾼다
 *  (마우스를 올리면 값과 쪽). 근거 숫자 목록은 이의의 '반대 근거' 줄에 따로 보인다 */
export function Linked({ text }: { text: string }) {
  const rows = useContext(RowsContext);
  if (!text) return null;
  text = plainIds(text);        // 숫자 id 말고 다른 내부 번호는 먼저 지운다
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    const ids = m[0].match(ONE) ?? [];
    const known = ids.map((id) => rows.get(id)).filter((r): r is Row => !!r);
    if (!known.length && !m[0].startsWith("(")) continue;     // 모르는 id 하나는 그대로 둔다
    const before = text.slice(last, m.index);
    if (m[0].startsWith("(")) {
      // 괄호 안 근거 id 목록은 보여주지 않는다 (근거 숫자는 이의의 '반대 근거' 줄에 이름과 쪽으로 따로 보인다)
      out.push(before.replace(/\s+$/, ""));
    } else {
      out.push(before);
      out.push(<abbr key={`${m.index}`} className={s.refName} title={describe(known[0])}>{known[0].item}</abbr>);
    }
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

// AI 글 속의 내부 번호(O-3, J-statistician, C-3, K-4, X-3, T-1:0:2)를 지운다. 사람은 번호가 아니라 내용으로 읽는다 (2026-11-20 사용자 요청)
// 숫자 id(R-09)는 Linked 가 이름으로 바꾼다. 'K-ICS'처럼 하이픈 뒤가 숫자가 아닌 말은 건드리지 않는다
const JUROR: Record<string, string> = { accountant: "회계사", industry: "산업 전문가", short: "공매도 투자자", statistician: "통계학자", behavioral: "행동경제학자" };
const ID = String.raw`(?:[A-Z]-\d[\d:]*|J-[a-z]+)`;
const ID_LIST = new RegExp(String.raw`\s*\(\s*${ID}(?:\s*[,·/]\s*${ID})*\s*\)`, "g");     // '(K-4, K-5)', '(X-3)'
export function plainIds(text: string): string {
  return text
    .replace(/배심원\s*추가\s*이의\s*J-([a-z]+)/g, (_, k: string) => `${JUROR[k] ?? "배심원"}가 추가한 지적`)   // 직업 이름이 모두 모음으로 끝나 '가'
    .replace(/(추가한|낸)\s*J-[a-z]+/g, "$1 지적")
    .replace(ID_LIST, "")
    .replace(/J-([a-z]+)/g, (_, k: string) => `${JUROR[k] ?? "배심원"}가 추가한 지적`)
    .replace(/\bO-\d+(는|가|를|와)?/g, (_, j?: string) => "지적" + (j ? { 는: "은", 가: "이", 를: "을", 와: "과" }[j] : ""))   // 받침에 맞춘 조사
    .replace(/\bC-\d+(는|가|를|와)?/g, (_, j?: string) => "다른 주장" + (j ? { 는: "은", 가: "이", 를: "을", 와: "과" }[j] : ""))
    .replace(/\s*\b[DEFKTX]-\d[\d:]*/g, "");
}

/** 판사 글: 내부 번호를 지우고, 숫자 id 는 이름으로 */
export function JudgeText({ text }: { text: string }) {
  return <Linked text={text} />;
}
