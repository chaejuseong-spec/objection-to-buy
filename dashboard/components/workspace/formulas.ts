"use client";

// 계산 지표 (2026-11-24, 사용자): 이미 있는 지표끼리 더하기·빼기·곱하기·나누기로 새 지표를 만든다.
// 식은 지표 이름을 [ ]로 감싸 적는다. 예: ([매출액] - [매출원가]) / [매출액]
// 값은 기간마다 공시 값으로 코드가 계산한다 (하나라도 없거나 0으로 나누면 그 기간은 '-'). 브라우저에 저장, 모든 회사에 같은 식 적용
import { useSyncExternalStore } from "react";
import type { Series } from "./fin-chart";

export interface Formula { id: string; name: string; expr: string; fmt: Series["fmt"]; scale100?: boolean }
export const CALC_CAT = "내 계산 지표";
const KEY = "maesu.formulas.v1";

let cache: Formula[] | null = null;
const subs = new Set<() => void>();
function read(): Formula[] {
  if (cache) return cache;
  try {
    cache = typeof window === "undefined" ? [] : (JSON.parse(localStorage.getItem(KEY) || "[]") as Formula[]);
  } catch {
    cache = [];
  }
  return cache;
}
function write(next: Formula[]) {
  cache = next;
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* 저장 못 해도 이번 화면에서는 쓴다 */ }
  subs.forEach((f) => f());
}
export const formulas = () => read();
export const addFormula = (f: Omit<Formula, "id">) => write([...read(), { ...f, id: Date.now().toString(36) }]);
export const removeFormula = (id: string) => write(read().filter((f) => f.id !== id));
const EMPTY: Formula[] = [];
export function useFormulas(): Formula[] {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, read, () => EMPTY);
}

/* ───── 식 읽기: [지표 이름], 숫자, + - * / ( ) ───── */
type Tok = { t: "ref"; name: string } | { t: "num"; v: number } | { t: "op"; v: string };
function tokenize(expr: string): Tok[] | string {
  const out: Tok[] = [];
  const re = /\s*(?:\[([^\]]+)\]|(\d+(?:\.\d+)?)|([-+*/×÷()]))/y;
  let i = 0;
  while (i < expr.length) {
    if (/^\s*$/.test(expr.slice(i))) break;
    re.lastIndex = i;
    const m = re.exec(expr);
    if (!m) return `'${expr.slice(i).trim().slice(0, 10)}' 부분을 읽지 못했습니다. 지표 이름은 [ ]로 감싸 주세요.`;
    if (m[1] != null) out.push({ t: "ref", name: m[1].trim() });
    else if (m[2] != null) out.push({ t: "num", v: +m[2] });
    else out.push({ t: "op", v: m[3] === "×" ? "*" : m[3] === "÷" ? "/" : m[3] });
    i = re.lastIndex;
  }
  return out;
}
type Node = { k: "ref"; name: string } | { k: "num"; v: number } | { k: "neg"; a: Node } | { k: "bin"; op: string; a: Node; b: Node };
function parse(toks: Tok[]): Node | string {
  let p = 0;
  const peek = () => toks[p];
  const expr = (): Node | string => {
    let a = term();
    while (typeof a !== "string" && peek()?.t === "op" && "+-".includes((peek() as { v: string }).v)) {
      const op = (toks[p++] as { v: string }).v;
      const b = term();
      if (typeof b === "string") return b;
      a = { k: "bin", op, a, b };
    }
    return a;
  };
  const term = (): Node | string => {
    let a = unary();
    while (typeof a !== "string" && peek()?.t === "op" && "*/".includes((peek() as { v: string }).v)) {
      const op = (toks[p++] as { v: string }).v;
      const b = unary();
      if (typeof b === "string") return b;
      a = { k: "bin", op, a, b };
    }
    return a;
  };
  const unary = (): Node | string => {
    const x = peek();
    if (x?.t === "op" && x.v === "-") { p++; const a = unary(); return typeof a === "string" ? a : { k: "neg", a }; }
    if (x?.t === "op" && x.v === "(") {
      p++;
      const a = expr();
      if (typeof a === "string") return a;
      if (peek()?.t !== "op" || (peek() as { v: string }).v !== ")") return "괄호가 닫히지 않았습니다.";
      p++;
      return a;
    }
    if (x?.t === "ref") { p++; return { k: "ref", name: x.name }; }
    if (x?.t === "num") { p++; return { k: "num", v: x.v }; }
    return "식이 끝나지 않았거나 기호가 연달아 있습니다.";
  };
  const n = expr();
  if (typeof n !== "string" && p < toks.length) return "식 끝에 남는 부분이 있습니다.";
  return n;
}
const refsOf = (n: Node): string[] => n.k === "ref" ? [n.name] : n.k === "neg" ? refsOf(n.a) : n.k === "bin" ? [...refsOf(n.a), ...refsOf(n.b)] : [];
function evalAt(n: Node, get: (name: string) => number | null): number | null {
  if (n.k === "num") return n.v;
  if (n.k === "ref") return get(n.name);
  if (n.k === "neg") { const a = evalAt(n.a, get); return a == null ? null : -a; }
  const a = evalAt(n.a, get), b = evalAt(n.b, get);
  if (a == null || b == null) return null;
  if (n.op === "+") return a + b;
  if (n.op === "-") return a - b;
  if (n.op === "*") return a * b;
  return b === 0 ? null : a / b;
}

/** 식 검사: 문제가 있으면 이유, 없으면 null. names: 지금 회사에 있는 지표 이름 */
export function checkExpr(expr: string, names: Set<string>): string | null {
  if (!expr.trim()) return "식을 적어 주세요.";
  const toks = tokenize(expr);
  if (typeof toks === "string") return toks;
  const n = parse(toks);
  if (typeof n === "string") return n;
  const refs = refsOf(n);
  if (!refs.length) return "지표를 하나 이상 넣어 주세요.";
  const miss = refs.filter((r) => !names.has(r));
  return miss.length ? `이 회사에 없는 지표: ${miss.map((x) => `[${x}]`).join(", ")}` : null;
}

/** 지표 목록에 계산 지표를 붙인다. 식에 쓴 지표가 이 회사에 없으면 그 계산 지표는 빠진다 */
export function applyFormulas<M extends { key: string; label: string; values: (number | null)[]; marks?: (string | null)[]; src?: string }>(
  metrics: M[], make: (f: Formula, values: (number | null)[], marks: ("derived" | null)[], src: string) => M, list: Formula[] = formulas()): M[] {
  if (!list.length) return metrics;
  const byName = new Map<string, M>();
  for (const m of metrics) if (!byName.has(m.label)) byName.set(m.label, m);
  const n0 = metrics[0]?.values.length ?? 0;
  const extra: M[] = [];
  for (const f of list) {
    const toks = tokenize(f.expr);
    if (typeof toks === "string") continue;
    const node = parse(toks);
    if (typeof node === "string" || refsOf(node).some((r) => !byName.has(r))) continue;
    const values = Array.from({ length: n0 }, (_, i) => {
      const v = evalAt(node, (name) => byName.get(name)!.values[i] ?? null);
      return v == null || !Number.isFinite(v) ? null : f.scale100 ? v * 100 : v;
    });
    const marks = Array.from({ length: n0 }, (_, i) => (refsOf(node).some((r) => byName.get(r)!.marks?.[i] === "derived") ? "derived" as const : null));
    const src = [...new Set(refsOf(node).flatMap((r) => (byName.get(r)!.src ?? "DART").split("·")))].join("·");   // 식에 쓴 지표들의 출처
    extra.push(make(f, values, marks, src));
  }
  return [...metrics, ...extra];
}
