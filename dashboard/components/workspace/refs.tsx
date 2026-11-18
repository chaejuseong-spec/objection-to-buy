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
