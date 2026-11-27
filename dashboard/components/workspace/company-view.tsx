"use client";

// 기본 종목정보 탭 (2026-11-22): 회사의 '지금' 재무·실적·배당·시세. 리포트 검증과 별개의 참고 화면.
// 데이터: result.company (파이썬 tribunal/external/company.py). DART·KRX·금감원 FISIS 값 그대로 + 코드가 계산한 비율. 없는 값은 '-'.
// 하위 메뉴는 왼쪽 목록의 '기본 종목정보' 아래에 들여 보인다 (Workspace 가 상태를 가진다). 출처는 화면 맨 아래 한 줄.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Company, CoreCell, FinKey, FinPeriod, FisisPeer, PeerRow, PeerSeries, Sector, StatementTable } from "@/lib/types";
import { FinChart, FinCharts, FinTable, fmtWon, useLatest, type Series } from "./fin-chart";
import { ExportButton, MetricExplorer, toRows, type Metric, type XlsxPayload, type XlsxRow } from "./explorer";
import { applyFormulas, CALC_CAT, useFormulas } from "./formulas";
import { day, getHistory, PriceChart, PriceExport, useHistoryRows, weekKey, type PxOpts } from "./price-chart";
import { DayChart, FlowsView } from "./flows-view";
import { SectorBar, SectorPerf, useCurrentSector, useSectorPick } from "./toss-sector";
import { InfoTip } from "./primitives";
import c from "./company.module.css";

// 차트 색 (2026-11-22, 색약 검사 통과 조합): 막대 파랑·초록, 선 주황·보라
const C1 = "#2a78d6", C2 = "#1baf7a", OR = "#eb6834", OR2 = "#6250d6";
export type CompanyMenu = "overview" | "fin" | "peers" | "flows";
export function companyMenus(co?: Company | null): { id: CompanyMenu; label: string }[] {
  if (!co || co.error) return [];
  return [
    { id: "overview", label: "기업개요" }, { id: "fin", label: "재무" }, { id: "peers", label: "동종업계 비교" }, { id: "flows", label: "수급" },
  ];
}
/** 종목정보 자료 형식 번호: tribunal/external/company.py 의 CO_V 와 같이 올린다 (테스트가 확인) */
const COMPANY_V = 51;   // 29: 전면 개편 1차 (DART 3년+올해, 태그로 줄 맞춤)
type Mode = "annual" | "half" | "quarter";
const MODE_NAME: Record<Mode, string> = { annual: "연간", half: "반기", quarter: "분기" };
const PER_YEAR: Record<Mode, number> = { annual: 1, half: 2, quarter: 4 };

/* ───────── 반기 (2026-11-24): 분기 값으로 만든다. 손익 = 두 분기 합, 재무상태 = 반기 말 값, 현금흐름 = 연초 누적 차감 ───────── */
const IS_KEYS = new Set<string>(["revenue", "op", "pretax", "ni", "ni_owner", "cogs", "gross", "sga", "ins_profit", "inv_profit", "div_rev", "da"]);
const CF_KEYS = new Set<string>(["capex", "cfo", "cfi", "cff"]);
const BS_KEYS: FinKey[] = ["assets", "liab", "equity", "equity_owner", "cur_assets", "cur_liab", "inventory"];
const halfCache = new WeakMap<FinPeriod[], FinPeriod[]>();
function halfList(quarter: FinPeriod[]): FinPeriod[] {
  const hit = halfCache.get(quarter);
  if (hit) return hit;
  const at = (k: string) => quarter.find((p) => p.period === k);
  const out: FinPeriod[] = [];
  for (const y of [...new Set(quarter.map((p) => p.period.slice(0, 4)))]) {
    for (const [a, b] of [["03", "06"], ["09", "12"]] as const) {
      const pa = at(`${y}.${a}`), pb = at(`${y}.${b}`);
      if (!pb) continue;
      const h: FinPeriod = { period: pb.period, end: pb.end, rcept_no: pb.rcept_no, derived: pb.derived?.includes("roe") ? ["roe"] : [], missing: [], roe: pb.roe ?? null, roa: pb.roa ?? null };
      for (const k of [...IS_KEYS] as FinKey[]) {
        h[k] = pa?.[k] != null && pb[k] != null ? pa[k]! + pb[k]! : null;
        if (pa?.derived?.includes(k) || pb.derived?.includes(k)) h.derived!.push(k);
        if (pa?.missing?.includes(k) || pb.missing?.includes(k)) h.missing!.push(k);
      }
      for (const k of [...CF_KEYS] as FinKey[]) {          // 현금흐름은 연초 누적: 상반기 = 6월 누적, 하반기 = 12월 누적 − 6월 누적
        const prev = b === "12" ? at(`${y}.06`) : undefined;
        h[k] = pb[k] == null ? null : b === "06" ? pb[k]! : prev?.[k] != null ? pb[k]! - prev[k]! : null;
        if (b === "12") h.derived!.push(k);
      }
      for (const k of BS_KEYS) {
        h[k] = pb[k] ?? null;
        if (pb.missing?.includes(k)) h.missing!.push(k);
      }
      out.push(h);
    }
  }
  halfCache.set(quarter, out);
  return out;
}
/** 차트 가로축 = DART 기간 + 금감원 기간 (2026-11-26 사용자): 금감원 값만 있는 분기·연도는 빈 칸(DART 값 없음)으로 덧붙인다.
 *  비금융은 금감원 자료가 없어 그대로. 재무제표 표에는 쓰지 않는다 (원래 자료 그대로) */
const padCache = new WeakMap<object, Company["statements"]>();
function padSector(co: Company): Company {
  const s = co.sector && !co.sector.error ? co.sector : null;
  const months = (s?.series ?? []).map((r) => String(r.period ?? "")).filter((p) => /^\d{4}\.(03|06|09|12)$/.test(p));
  if (!months.length) return co;
  const st0 = co.statements;
  let st = padCache.get(st0);
  if (!st) {
    const blank = (period: string): FinPeriod => ({ period, end: "", rcept_no: "", derived: [], missing: [] } as unknown as FinPeriod);
    const qHas = new Set(st0.quarter.map((p) => p.period)), aHas = new Set(st0.annual.map((p) => p.period));
    const quarter = [...st0.quarter, ...months.filter((m) => !qHas.has(m)).map(blank)].sort((a, b) => a.period.localeCompare(b.period));
    const years = months.filter((m) => m.endsWith(".12")).map((m) => m.slice(0, 4)).filter((y) => !aHas.has(y));
    const annual = [...st0.annual, ...years.map(blank)].sort((a, b) => a.period.localeCompare(b.period));
    st = { ...st0, quarter, annual };
    padCache.set(st0, st);
  }
  return { ...co, statements: st };
}
function periodList(st: { annual: FinPeriod[]; quarter: FinPeriod[] }, mode: Mode): FinPeriod[] {
  return mode === "annual" ? st.annual : mode === "quarter" ? st.quarter : halfList(st.quarter);
}
/** 재무제표 계정 표의 반기 열: 손익 = 두 분기 합, 재무상태 = 반기 말, 현금흐름(연초 누적) = 차감 */
function tableCols(t: StatementTable, sj: "IS" | "BS" | "CF", mode: Mode): Record<string, Record<string, number | null>> {
  if (mode !== "half") return t[mode];
  const q = t.quarter, out: Record<string, Record<string, number | null>> = {};
  for (const y of [...new Set(Object.keys(q).map((k) => k.slice(0, 4)))]) {
    for (const [a, b] of [["03", "06"], ["09", "12"]] as const) {
      const ca = q[`${y}.${a}`], cb = q[`${y}.${b}`], c6 = q[`${y}.06`];
      if (!cb) continue;
      const col: Record<string, number | null> = {};
      for (const acc of Object.keys(cb)) {
        const vb = cb[acc], va = ca?.[acc];
        col[acc] = sj === "BS" ? vb ?? null
          : sj === "IS" ? (va != null && vb != null ? va + vb : null)
            : b === "06" ? vb ?? null : (vb != null && c6?.[acc] != null ? vb - c6[acc]! : null);
      }
      out[`${y}.${b}`] = col;
    }
  }
  return out;
}
/** 금감원 손익 금액(분기 값 a, 연초 누적 b=_ytd): 연간 = 12월 누적, 반기 = 6월 누적 / 12월 누적 − 6월 누적 */
function fisisValue(series: Record<string, unknown>[], k: string, key: string, won: boolean, mode: Mode): number | null {
  const at = (p: string) => series.find((r) => r.period === p) as Record<string, number | null> | undefined;
  const p = mode === "annual" ? `${k}.12` : k;
  if (!won || mode === "quarter") return at(p)?.[key] ?? null;
  if (mode === "annual" || p.endsWith(".06")) return at(p)?.[`${key}_ytd`] ?? null;
  if (!p.endsWith(".12")) return null;
  const full = at(p)?.[`${key}_ytd`], first = at(`${p.slice(0, 4)}.06`)?.[`${key}_ytd`];
  return full != null && first != null ? full - first : null;
}

/* ───────── 계산 (모두 공시 값으로 코드가 계산. 하나라도 없으면 null) ───────── */
const ratio = (a?: number | null, b?: number | null) => (a != null && b != null && b !== 0 ? (a / b) * 100 : null);
function growth(cur?: number | null, prev?: number | null) {
  return cur != null && prev != null && prev > 0 ? ((cur - prev) / prev) * 100 : null;   // 기준이 0 이하이면 증가율을 정하지 않는다
}
const halfLabel = (period: string) => `${period.slice(2, 4)}년 ${period.endsWith(".06") ? "상반기" : "하반기"}`;
const label = (p: FinPeriod, mode: Mode) => (mode === "annual" ? `${p.period}년` : mode === "half" ? halfLabel(p.period) : `${p.period.slice(2, 4)}년 ${+p.period.slice(5)}월`);
const qLabel = (period: string) => `${period.slice(2, 4)}년 ${+period.slice(5)}월`;

// 전면 개편 1차 (2026-11-25): 재무는 최근 3년 + 올해만 받는다. '전체' = 받은 기간 모두 (값 10 은 넉넉히 자르지 않는다는 뜻)
const FS_RANGE: [number, string][] = [[1, "1년"], [3, "3년"], [5, "5년"], [10, "전체"]];
function sliceRange<T>(list: T[], mode: Mode, years: number) {
  return list.slice(-(years * PER_YEAR[mode]));
}
function prevOf(list: FinPeriod[], p: FinPeriod, mode: Mode, sameQuarter = true): FinPeriod | undefined {
  const i = list.indexOf(p);
  if (mode === "annual" || !sameQuarter) return list[i - 1];
  const want = `${+p.period.slice(0, 4) - 1}${p.period.slice(4)}`;
  return list.find((x) => x.period === want);
}
function mark(p: FinPeriod, ...keys: string[]): "derived" | "missing" | null {
  if (keys.some((k) => p.missing?.includes(k))) return "missing";
  if (keys.some((k) => p.derived?.includes(k))) return "derived";
  return null;
}
function money(key: FinKey, name: string, color: string, rows: FinPeriod[], kind: "bar" | "line" = "bar"): Series {
  return { key, label: name, color, kind, fmt: "won", values: rows.map((p) => p[key] ?? null), marks: rows.map((p) => mark(p, key)) };
}
/** 현금흐름은 분기 보고서에 연초부터 누적으로만 있다 -> 같은 해 앞 분기 누적을 빼서 분기 값으로 */
function quarterFlow(list: FinPeriod[], p: FinPeriod, key: FinKey): number | null {
  const v = p[key];
  if (v == null) return null;
  const m = +p.period.slice(5);
  if (m === 3) return v;
  const prev = list.find((x) => x.period === `${p.period.slice(0, 5)}${String(m - 3).padStart(2, "0")}`);
  return prev?.[key] != null ? v - prev[key]! : null;
}

type Group = "stability" | "liquidity" | "profit" | "growth" | "sales" | "inventory" | "capex";
const GROUPS: Record<Group, string> = {
  stability: "안정성", liquidity: "유동성", profit: "수익성", growth: "성장성", sales: "수익성",   // 매출·영업이익·마진을 수익성 묶음에 합침 (2026-11-26 사용자)
  inventory: "재고", capex: "설비투자",
};

function groupSeries(g: Group, all: FinPeriod[], mode: Mode, years: number): Series[] {
  const rows = sliceRange(all, mode, years);
  const days = mode === "quarter" ? 91 : mode === "half" ? 182 : 365;
  switch (g) {
    case "stability":
      return [money("equity", "자본총계", C1, rows), money("liab", "부채총계", C2, rows),
        { key: "debt", label: "부채비율", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.liab, p.equity)), marks: rows.map((p) => mark(p, "liab", "equity")) }];
    case "liquidity":
      return [money("cur_assets", "유동자산", C1, rows), money("cur_liab", "유동부채", C2, rows),
        { key: "cur", label: "유동비율", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.cur_assets, p.cur_liab)) }];
    case "profit": {
      // ROE·ROA (2026-11-26 사용자, 모든 화면 같은 값): Python add_returns 가 기간마다 계산해 둔 값.
      // 분기·반기 = 최근 4분기 순이익 ÷ (지금 + 1년 전)/2, 연간 = 그해 순이익 ÷ (전년 말 + 그해 말)/2. 지배주주 값이 없고 비지배지분이 있으면 빈칸
      const roe = (p: FinPeriod) => p.roe ?? null;
      const roa = (p: FinPeriod) => p.roa ?? null;
      return [money("op", "영업이익", C1, rows), money("ni", "당기순이익", C2, rows),
        { key: "roe", label: mode !== "annual" ? "ROE (최근 4분기)" : "ROE", color: OR, kind: "line", fmt: "pct", values: rows.map(roe), marks: rows.map((p) => mark(p, "roe")) },
        { key: "roa", label: mode !== "annual" ? "ROA (최근 4분기)" : "ROA", color: OR2, kind: "line", fmt: "pct", dashed: true, values: rows.map(roa) }];
    }
    case "growth":
      return [money("op", "영업이익", C1, rows), money("ni", "당기순이익", C2, rows),
        { key: "op_g", label: mode !== "annual" ? "영업이익 증가율(전년 동기)" : "영업이익 증가율", color: OR, kind: "line", fmt: "pct", signed: true, values: rows.map((p) => growth(p.op, prevOf(all, p, mode)?.op)) },
        { key: "ni_g", label: mode !== "annual" ? "순이익 증가율(전년 동기)" : "순이익 증가율", color: OR2, kind: "line", fmt: "pct", signed: true, dashed: true, values: rows.map((p) => growth(p.ni, prevOf(all, p, mode)?.ni)) }];
    case "sales": {
      const out: Series[] = [money("revenue", "매출액", C1, rows), money("op", "영업이익", C2, rows),
        { key: "opm", label: "영업이익률", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.op, p.revenue)) }];
      if (!all.some((p) => p.gross != null)) return out;                  // 매출총이익을 적지 않는 회사는 마진 줄 없이
      return [...out, money("gross", "매출총이익", C1, rows), money("sga", "판매비와관리비", C2, rows),
        { key: "gpm", label: "매출총이익률", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.gross, p.revenue)) },
        { key: "sgar", label: "판관비율", color: OR2, kind: "line", fmt: "pct", dashed: true, values: rows.map((p) => ratio(p.sga, p.revenue)) }];
    }
    case "inventory":
      return [money("inventory", "재고자산", C1, rows),
        { key: "dio", label: "재고 보유일수", color: OR, kind: "line", fmt: "days", values: rows.map((p) => p.inventory != null && p.cogs ? (p.inventory / p.cogs) * days : null) }];
    case "capex": {
      const v = rows.map((p) => (mode === "quarter" ? quarterFlow(all, p, "capex") : p.capex ?? null));
      return [{ key: "capex", label: "설비투자", color: C1, kind: "bar", fmt: "won", values: v.map((x) => (x != null ? Math.abs(x) : null)) },
        { key: "capexr", label: "매출 대비 설비투자", color: OR, kind: "line", fmt: "pct", values: rows.map((p, i) => ratio(v[i] != null ? Math.abs(v[i]!) : null, p.revenue)) }];
    }
  }
}

/* ───────── 작은 부품 ───────── */
function Select<T extends string | number>({ value, options, onChange, label: aria }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <select className={c.select} aria-label={aria} value={String(value)}
            onChange={(e) => onChange(options.find(([v]) => String(v) === e.target.value)![0])}>
      {options.map(([v, t]) => <option key={String(v)} value={String(v)}>{t}</option>)}
    </select>
  );
}
function Segment<T extends string | number>({ value, options, onChange, label: aria }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className={c.segment} role="group" aria-label={aria}>
      {options.map(([v, t]) => <button key={String(v)} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>{t}</button>)}
    </div>
  );
}
function Section({ title, sub, right, children }: { title: string; sub?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className={c.section}>
      <div className={c.sectionHead}><h3>{title}{sub && <small>{sub}</small>}</h3>{right}</div>
      {children}
    </section>
  );
}
const Notes = ({ items }: { items: (ReactNode | false | null | undefined)[] }) => {
  const shown = items.filter(Boolean);
  return shown.length ? <ul className={c.notes}>{shown.map((x, i) => <li key={i}>{x}</li>)}</ul> : null;
};

/* ───────── 머리글: 회사·종목코드 + 시세 ───────── */
function Header({ co, refreshing, failed }: { co: Company; refreshing?: boolean; failed?: boolean }) {
  const p = co.price;
  // 현재가 (2026-11-26 사용자): 기업개요·경쟁사와 같은 토스 현재가(10분), 시가총액 = 현재가 × KRX 상장주식수. 못 받으면 KRX 종가
  const lp = useLastPrices([co.ticker])[co.ticker] ?? null;
  const close = lp?.c ?? p?.close ?? null;
  // 등락 기준 = KRX 공식 전일 종가 (토스 전일 값은 넥스트레이드 시간외까지 들어가 공식 종가와 다름). 같은 날이면 KRX 값 그대로
  const base = lp && p?.close != null && p.date < lp.d ? p.close : lp && p?.date === lp.d ? (p.close ?? 0) - (p.change ?? 0) : lp?.prev ?? null;
  const change = lp ? (base ? lp.c - base : null) : p?.change ?? null;
  const rate = lp ? (base ? ((lp.c - base) / base) * 100 : null) : p?.rate ?? null;
  const mktcap = lp && p?.shares != null ? lp.c * p.shares : p?.mktcap ?? null;
  const up = (change ?? 0) > 0, down = (change ?? 0) < 0;
  const ymd = lp?.d ?? p?.date ?? "";
  const day = ymd ? `${ymd.slice(0, 4)}.${ymd.slice(4, 6)}.${ymd.slice(6, 8)} ${lp ? "현재가" : "종가"}` : "";
  return (
    <header className={c.header}>
      <div className={c.headLeft}>
        <h2 className={c.headName}>{co.name}<span>{co.ticker}</span>{p?.market && <span>{p.market}</span>}</h2>
        {close != null ? (
          <div className={c.headPrice}>
            <b>{close.toLocaleString("ko-KR")}원</b>
            {change != null && <span className={up ? c.pos : down ? c.neg : undefined}>
              {up ? "+" : ""}{change.toLocaleString("ko-KR")}원 ({up ? "+" : ""}{(rate ?? 0).toFixed(2)}%)
            </span>}
            <small>{day}{refreshing ? ", 최신 시세 확인 중" : failed ? ", 최신 시세를 받지 못함" : ""}</small>
          </div>
        ) : <div className={c.headPrice}><small>시세 없음</small></div>}
      </div>
      <dl className={c.headStats}>
        <div><dt>1일 범위</dt><dd>{p?.low != null && p.high != null ? `${p.low.toLocaleString("ko-KR")} ~ ${p.high.toLocaleString("ko-KR")}원` : "-"}</dd></div>
        <div><dt>52주 범위</dt><dd>{co.range52 ? `${co.range52.low.toLocaleString("ko-KR")} ~ ${co.range52.high.toLocaleString("ko-KR")}원` : "-"}</dd></div>
        <div><dt>시가총액</dt><dd>{fmtWon(mktcap)}</dd></div>
      </dl>
    </header>
  );
}

/* ───────── 매출 구성: 도넛 + 목록 (재무제표 주석 영업부문, 없으면 사업보고서의 비중 표) ───────── */
const MIX = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"];   // 색약 검사 통과 순서. 7번째부터는 '기타'로 묶는다
function SalesMix({ mix }: { mix: NonNullable<Company["sales_mix"]> }) {
  const sorted = [...mix.items].sort((a, b) => b.share - a.share);
  const items = sorted.length > MIX.length
    ? [...sorted.slice(0, MIX.length - 1), { name: "기타", amount: sorted.slice(MIX.length - 1).reduce((t, x) => t + (x.amount ?? 0), 0) || null, share: sorted.slice(MIX.length - 1).reduce((t, x) => t + x.share, 0) }]
    : sorted;
  const total = items.reduce((t, x) => t + x.share, 0) || 1;
  const R = 52, W = 14, C = 2 * Math.PI * R;
  const lens = items.map((x) => (x.share / total) * C);
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((t, v) => t + v, 0));   // 조각마다 시작 위치
  return (
    <Section title={mix.title} sub={`${mix.year}년`}>
      <div className={c.mix}>
        <svg width={140} height={140} viewBox="0 0 140 140" role="img" aria-label={`${mix.title}: ${items.map((x) => `${x.name} ${x.share.toFixed(1)}%`).join(", ")}`}>
          <g transform="rotate(-90 70 70)">
            {items.map((x, i) => (
              <circle key={x.name} cx={70} cy={70} r={R} fill="none" stroke={MIX[i]} strokeWidth={W}
                      strokeDasharray={`${Math.max(lens[i] - 2, 0.5)} ${C}`} strokeDashoffset={-starts[i]}><title>{`${x.name} ${x.share.toFixed(2)}%`}</title></circle>
            ))}
          </g>
        </svg>
        <ul className={c.mixList}>
          {items.map((x, i) => (
            <li key={x.name}><span><i style={{ background: MIX[i] }} />{x.name}</span><span>{x.amount ? <em>{fmtWon(x.amount)}</em> : null}<b>{x.share.toFixed(2)}%</b></span></li>
          ))}
          {mix.adjust != null && <li className={c.mixSum}><span>조정</span><span><em>{fmtWon(mix.adjust)}</em><b /></span></li>}
          {mix.total != null && <li className={c.mixSum}><span>{mix.total_label ?? "합계"}</span><span><em>{fmtWon(mix.total)}</em><b /></span></li>}
        </ul>
      </div>
      {mix.profit && <ProfitSplit p={mix.profit} />}
    </Section>
  );
}

/* 이익 구성: 보험손익·투자손익 (손익계산서 값 그대로). 막대 길이는 둘 중 큰 값 기준 */
function ProfitSplit({ p }: { p: NonNullable<NonNullable<Company["sales_mix"]>["profit"]> }) {
  const both = p.total != null && p.total > 0 && p.items.every((x) => x.amount >= 0);
  const COLORS = ["#2a78d6", "#1baf7a"];
  if (both) {                                          // 막대 한 개를 비중대로 나눠 칠한다
    return (
      <div className={c.profit}>
        <h4>이익 구성<small>영업이익 {fmtWon(p.total, 2)}</small></h4>
        <div className={c.stack} role="img" aria-label={p.items.map((x) => `${x.name} ${((x.amount / p.total!) * 100).toFixed(1)}%`).join(", ")}>
          {p.items.map((x, k) => <i key={x.name} style={{ width: `${(x.amount / p.total!) * 100}%`, background: COLORS[k % 2] }} />)}
        </div>
        <ul className={c.stackLegend}>
          {p.items.map((x, k) => (
            <li key={x.name}><span><i style={{ background: COLORS[k % 2] }} />{x.name}</span><em>{fmtWon(x.amount, 2)}</em><b>{((x.amount / p.total!) * 100).toFixed(1)}%</b></li>
          ))}
        </ul>
      </div>
    );
  }
  const max = Math.max(...p.items.map((x) => Math.abs(x.amount)), 1);
  return (
    <div className={c.profit}>
      <h4>이익 구성</h4>
      <ul>
        {p.items.map((x) => (
          <li key={x.name}>
            <span>{x.name}</span>
            <span className={c.profitTrack}><i className={x.amount < 0 ? c.profitNeg : undefined} style={{ width: `${(Math.abs(x.amount) / max) * 100}%` }} /></span>
            <em>{fmtWon(x.amount, 2)}</em>
            <b />
          </li>
        ))}
        {p.total != null && <li className={c.mixSum}><span>영업이익</span><span /><em>{fmtWon(p.total, 2)}</em><b /></li>}
      </ul>
    </div>
  );
}

/* ───────── 기업개요 ───────── */
function Overview({ co }: { co: Company }) {
  const i = co.info, m = co.metrics, p = co.price;
  // 현재 주가 기준 (2026-11-26 사용자): 주가 추이의 최근 가격(장중이면 지금 가격)으로 PER·PBR·배당수익률·시가총액. EPS·BPS·ROE 는 재무 값이라 주가와 무관
  const lp = useLastPrices([co.ticker])[co.ticker] ?? null;      // 경쟁사 표와 같은 현재가 자료 (같은 PER 이 나오게)
  const px = lp?.c ?? p?.close ?? null;
  const pxDay = lp ? `${+lp.d.slice(4, 6)}.${+lp.d.slice(6, 8)}` : p?.date ? `${+p.date.slice(4, 6)}.${+p.date.slice(6, 8)}` : "";
  const per = px != null && m.eps != null && m.eps > 0 ? px / m.eps : null;
  const pbr = px != null && m.bps != null && m.bps > 0 ? px / m.bps : null;
  const mktcap = px != null && p?.shares != null ? px * p.shares : p?.mktcap ?? null;
  const rows: [string, ReactNode][] = [
    ["최대주주", co.holder ? <>{co.holder.name}{co.holder.share != null ? ` ${co.holder.share.toFixed(2)}%` : ""}
      {co.holder.group != null && co.holder.group !== co.holder.share && <small className={c.holderSub}>특수관계인 포함 {co.holder.group.toFixed(2)}%</small>}</> : "-"],
    ["홈페이지", i.hm_url ? <a href={/^https?:/.test(i.hm_url) ? i.hm_url : `https://${i.hm_url}`} target="_blank" rel="noreferrer">{i.hm_url}</a> : "-"],
    ["결산월", i.acc_mt ? `${+i.acc_mt}월` : "-"], ["상장주식수", p?.shares != null ? `${p.shares.toLocaleString("ko-KR")}주` : "-"],
    ["시가총액", fmtWon(mktcap)],
  ];
  const ind: [string, string, string][] = [
    ["PER", per != null ? `${per.toFixed(1)}배` : "-", "현재 주가 ÷ EPS(회사 공시 기본주당이익 최근 4분기 합)"],
    ["PBR", pbr != null ? `${pbr.toFixed(2)}배` : "-", "현재 주가 ÷ BPS(최근 분기말 주당순자산)"],
    ["EPS", m.eps != null ? `${Math.round(m.eps).toLocaleString("ko-KR")}원` : "-", m.basis?.eps || "최근 4분기 지배주주 순이익 ÷ 유통주식수"],
    ["BPS", m.bps != null ? `${Math.round(m.bps).toLocaleString("ko-KR")}원` : "-", m.basis?.bps || "최근 분기말 지배주주 자본 ÷ 유통주식수"],
    ["ROE", m.roe != null ? `${m.roe.toFixed(1)}%` : "-", m.basis?.roe || "최근 4분기 지배주주 순이익 ÷ 평균 지배주주 자본"],
  ];
  const dv = co.dividends.length ? co.dividends[co.dividends.length - 1] : null;      // 최근 사업연도 배당 (회사 공시)
  // 배당수익률 = 확정된 최근 연간 주당배당금 ÷ 현재가 (재무·일간과 같은 규칙, 분할 전 배당은 지금 주식 수 기준)
  const divNow = dv?.dps != null && px ? (dv.dps / ((co.valuation ?? []).find((r) => r.period === `${dv.year}.12`)?.split || 1) / px) * 100 : null;
  if (dv) ind.push(
    ["주당배당금", dv.dps != null ? `${dv.dps.toLocaleString("ko-KR")}원` : "-", `${dv.year}년 보통주 1주당 현금배당 (회사 공시)`],
    ["시가배당률", dv.yield != null ? `${dv.yield.toFixed(1)}%` : "-", `${dv.year}년 주당배당금 ÷ 배당기준일 무렵 주가 (회사 공시)`],
    ["배당수익률", divNow != null ? `${divNow.toFixed(1)}%` : "-", `${dv.year}년 주당배당금(확정된 최근 연간) ÷ 현재 주가`],
    ["배당성향", dv.payout != null ? `${dv.payout.toFixed(1)}%` : "-", `${dv.year}년 현금배당 총액 ÷ 연결 당기순이익 (회사 공시)`],
  );
  return (
    <>
      <PriceChart ticker={co.ticker} name={co.name} />
      <Section title="기업 정보">
        <dl className={c.infoRow}>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </Section>
      {co.sales_mix && <SalesMix mix={co.sales_mix} />}
      <Section title="투자 지표" sub={`현재 주가 ${px != null ? `${Math.round(px).toLocaleString("ko-KR")}원` : "-"}${pxDay ? ` (${pxDay})` : ""} 기준${dv ? `, 배당은 ${dv.year}년` : ""}`}>
        <dl className={c.metricRow}>
          {ind.map(([k, v, t]) => <div key={k}><dt>{k}<InfoTip text={t} /></dt><dd>{v}</dd></div>)}
        </dl>
        <ValuationTrend co={co} />
      </Section>
      <DividendTrend co={co} />
    </>
  );
}

/* ───────── 일별 지표 (2026-11-26 사용자): PER·PBR·종가·배당수익률만 고르면 일별 선 ─────────
   PER = 그날 종가 ÷ 그날까지 공시된 최근 EPS, PBR = ÷ 최근 BPS, 배당수익률 = 그날까지 확정된(사업보고서 공시) 최근 주당배당금 ÷ 그날 종가.
   종가는 수정주가, EPS·BPS·배당은 분할 보정 값이라 서로 맞다. 분기 말 값은 재무 > 투자 지표와 같다 */
export const DAILY_KEYS = new Set(["v_per", "v_pbr", "v_close", "v_divy"]);
type ModeD = Mode | "daily";
const MODE_OPTS: [ModeD, string][] = [["daily", "일간"], ["quarter", "분기"], ["half", "반기"], ["annual", "연간"]];   // 순서 (2026-11-26 사용자)
const DAILY_META: Record<string, { label: string; fmt: (v: number) => string; xfmt: Series["fmt"]; note: string }> = {
  v_per: { label: "PER", fmt: (v) => `${v.toFixed(1)}배`, xfmt: "x", note: "그날 종가 ÷ 그날까지 공시된 최근 EPS (회사 공시 기본주당이익 최근 4분기 합)" },
  v_pbr: { label: "PBR", fmt: (v) => `${v.toFixed(2)}배`, xfmt: "x", note: "그날 종가 ÷ 그날까지 공시된 최근 BPS" },
  v_close: { label: "종가", fmt: (v) => `${Math.round(v).toLocaleString("ko-KR")}원`, xfmt: "krw", note: "일별 수정주가 (액면분할 등 보정)" },
  v_divy: { label: "배당수익률", fmt: (v) => `${v.toFixed(2)}%`, xfmt: "pct", note: "그날까지 확정된 최근 연간 주당배당금 ÷ 그날 종가 (그해 배당은 다음 해 사업보고서 공시일부터)" },
};
type DaySubject = { id: string; label: string; color: string; ticker: string; valuation?: Company["valuation"]; dividends?: Company["dividends"] };
type ValDay = { d: string; c: number; per: number | null; pbr: number | null; eps: number | null; bps: number | null; from: string | null;
  divy: number | null; dps: number | null; dyear: number | null };
function dailyVal(hist: { d: string; c: number }[], valuation: Company["valuation"], dividends?: Company["dividends"]): ValDay[] {
  const pts = (valuation ?? []).filter((r) => r.known).sort((a, b) => `${a.known}${a.period}`.localeCompare(`${b.known}${b.period}`));
  const vAt = new Map((valuation ?? []).map((r) => [r.period, r]));
  const dv = (dividends ?? []).filter((x) => x.dps != null).map((x) => {
    const r = vAt.get(`${x.year}.12`);
    return { known: r?.known ?? `${x.year + 1}0331`, dps: x.dps! / (r?.split || 1), year: x.year };     // 분할 전 배당도 지금 주식 수 기준으로
  }).sort((a, b) => a.known.localeCompare(b.known));
  const out: ValDay[] = [];
  let j = -1, k = -1;
  for (const h of hist) {
    while (j + 1 < pts.length && pts[j + 1].known! <= h.d) j++;
    while (k + 1 < dv.length && dv[k + 1].known <= h.d) k++;
    const p = j >= 0 ? pts[j] : null, eps = p?.eps ?? null, bps = p?.bps ?? null;
    out.push({ d: h.d, c: h.c, eps, bps, from: p?.period ?? null, per: eps != null && eps > 0 ? h.c / eps : null, pbr: bps != null && bps > 0 ? h.c / bps : null,
      divy: k >= 0 && h.c ? (dv[k].dps / h.c) * 100 : null, dps: k >= 0 ? dv[k].dps : null, dyear: k >= 0 ? dv[k].year : null });
  }
  return out;
}
const dailyGet = (key: string, r: ValDay) => (key === "v_per" ? r.per : key === "v_pbr" ? r.pbr : key === "v_close" ? r.c : key === "v_divy" ? r.divy : null);
function useHistories(tickers: string[]) {
  const key = tickers.join(",");
  const [got, setGot] = useState<Record<string, { d: string; c: number }[]>>({});
  useEffect(() => {
    let off = false;
    key.split(",").filter(Boolean).forEach((t) => getHistory(t).then((b) => {
      if (!off) setGot((g) => ({ ...g, [t]: (b.rows ?? []).map((r) => ({ d: r.d, c: r.c })) }));
    }));
    return () => { off = true; };
  }, [key]);
  return got;
}
/** 기간 칸 첫 칸의 시작일 (YYYYMMDD): 일별 차트가 같은 기간을 보이게 */
function fromOf(period: string | undefined, mode: Mode): string {
  if (!period) return "";
  const y = +period.slice(0, 4), m = period.length > 4 ? +period.slice(5, 7) : 12;
  const start = mode === "annual" ? 1 : mode === "half" ? m - 5 : m - 2;
  return `${y}${String(Math.max(1, start)).padStart(2, "0")}01`;
}
function subjectsOf(co: Company, ready: PeerPick[], me = true): DaySubject[] {
  return [
    ...(me ? [{ id: co.ticker, label: co.name, color: ME_COLOR, ticker: co.ticker, valuation: co.valuation, dividends: co.dividends }] : []),
    ...ready.flatMap((x) => ("ticker" in x.d && x.d.ticker ? [{ id: x.id, label: x.d.name, color: PEER_COLORS[x.slot], ticker: x.d.ticker, valuation: x.d.valuation ?? null }] : [])),
  ];
}
function DailyMetrics({ keys, subjects, from, name }: { keys: string[]; subjects: DaySubject[]; from: string; name: string }) {
  const hs = useHistories(subjects.map((x) => x.ticker));
  const [exp, setExp] = useState(false);
  const closeExp = useCallback(() => setExp(false), []);
  const per = useMemo(() => subjects.map((sj) => ({ sj, rows: hs[sj.ticker] ? dailyVal(hs[sj.ticker], sj.valuation ?? null, sj.dividends) : null })), [subjects, hs]);
  const me = per[0];
  if (!me || !me.rows) return <p className={c.empty}>주가 자료를 받는 중…</p>;
  const maps = per.map((x) => new Map((x.rows ?? []).map((r) => [r.d, r])));
  type Row = { d: string; v: (number | null)[][] };
  const all: Row[] = me.rows.map((r) => ({ d: r.d, v: keys.map((k) => maps.map((m) => { const x = m.get(r.d); return x ? dailyGet(k, x) : null; })) }));
  const shown = from ? all.filter((r) => r.d >= from) : all;
  // 마우스 옆 상자 (2026-11-26 사용자): 이 회사만이면 그날 주가·EPS(BPS·배당)·기준 실적, 비교 중이면 회사별 값
  const won = (x: number | null) => (x == null ? "-" : `${Math.round(x).toLocaleString("ko-KR")}원`);
  const tipRows = (k: string, d: string): [string, string][] | null => {
    const show = (x: number | null) => (x == null ? "-" : DAILY_META[k].fmt(x));
    if (per.length > 1) return per.map((x, si) => { const r = maps[si].get(d); return [`● ${x.sj.label}`, show(r ? dailyGet(k, r) : null)]; });
    const r = maps[0].get(d);
    if (!r) return null;
    if (k === "v_per") return [["주가", won(r.c)], ["EPS", won(r.eps)], ["기준 실적", r.from ? `${r.from.slice(0, 4)}.${r.from.slice(5)}` : "-"], ["PER", show(r.per)]];
    if (k === "v_pbr") return [["주가", won(r.c)], ["BPS", won(r.bps)], ["기준 실적", r.from ? `${r.from.slice(0, 4)}.${r.from.slice(5)}` : "-"], ["PBR", show(r.pbr)]];
    if (k === "v_divy") return [["주가", won(r.c)], ["주당배당금", r.dps != null ? `${won(r.dps)} (${r.dyear}년)` : "-"], ["배당수익률", show(r.divy)]];
    return [["종가", won(r.c)]];
  };
  const makeExport = (o: PxOpts): XlsxPayload => {
    const inRange = o.range === "custom" ? all.filter((x) => x.d >= o.from.replaceAll("-", "") && x.d <= o.to.replaceAll("-", ""))
      : o.range === "all" ? all : all.slice(-Number(o.range));
    const wk = (d: string) => (o.step === "m" ? d.slice(0, 6) : weekKey(d));
    const sel = o.step === "d" ? inRange : inRange.filter((x, i) => i === inRange.length - 1 || wk(inRange[i + 1].d) !== wk(x.d));
    const ps = sel.map((x) => day(x.d));
    const basis = `${o.step === "d" ? "일별" : o.step === "w" ? "주말 값" : "월말 값"}, 수정주가 기준`;
    return { title: `${name} ${keys.map((k) => DAILY_META[k].label).join("·")}`, periods: [], rows: [], sheets: keys.map((k, ki) => ({
      title: `${name} ${DAILY_META[k].label}`, sheet: DAILY_META[k].label, basis, periods: ps, sources: ["토스증권", "OpenDART"],
      notes: [`${DAILY_META[k].label}: ${DAILY_META[k].note}`],
      rows: per.map((x, si) => ({ label: x.sj.label, legend: x.sj.label, fmt: DAILY_META[k].xfmt, kind: "line" as const, strong: si === 0, values: sel.map((r) => r.v[ki][si]) })),
    })) };
  };
  return (
    <>
      <div className={c.explorerBar}>
        <button type="button" className={c.exportBtn} onClick={() => setExp(true)}>엑셀로 내보내기</button>
        {per.some((x) => !x.rows) && <small className={c.modalHint}>비교 회사 주가 받는 중…</small>}
      </div>
      {exp && <PriceExport title="일별 지표 엑셀로 내보내기" first={all[0].d} last={all[all.length - 1].d} onClose={closeExp} make={makeExport} />}
      <div className={c.cmpGrid}>
        {keys.map((k, ki) => (
          <div key={k}>
            <div className={c.chartUnit}>{DAILY_META[k].label}<small> 일별</small></div>
            <DayChart key={`${k}|${per.map((x) => x.sj.id).join()}|${from}`} rows={shown} fmt={DAILY_META[k].xfmt} height={228} format={DAILY_META[k].fmt}
              tip={(d) => tipRows(k, d)} lines={per.map((x, si) => ({ label: x.sj.label, color: x.sj.color, get: (r: Row) => r.v[ki][si] }))} />
          </div>
        ))}
      </div>
      <ul className={c.notes}>
        {keys.map((k) => <li key={k}>{DAILY_META[k].label}: {DAILY_META[k].note}</li>)}
        {keys.includes("v_divy") && per.length > 1 && <li>배당수익률은 이 회사만 (비교 회사 배당 자료 없음)</li>}
      </ul>
    </>
  );
}

/** PER·PBR 추이 (2026-11-26 사용자: 일별): PER = 그날 종가 ÷ 그날까지 공시된 최근 EPS(공시 기본주당이익 최근 4분기 합),
 *  PBR = 그날 종가 ÷ 그날까지 공시된 최근 BPS. 공시일 = min(보고서 접수일, 법정 기한). 수정주가와 분할 보정 EPS·BPS 끼리 나눈다.
 *  분기 말 값은 재무 > 투자 지표와 같다. EPS·BPS 막대는 분기·연간 */
function ValuationTrend({ co }: { co: Company }) {
  const [mode, setMode] = useState<"quarter" | "annual">("annual");
  const [years, setYears] = useState(10);
  const [exp, setExp] = useState(false);
  const closeExp = useCallback(() => setExp(false), []);
  const hist = useHistoryRows(co.ticker);
  const daily = useMemo<ValDay[]>(() => dailyVal(hist ?? [], co.valuation).filter((r) => r.from), [hist, co.valuation]);
  const cut = (() => { const x = new Date(); x.setFullYear(x.getFullYear() - years); return `${x.getFullYear()}${String(x.getMonth() + 1).padStart(2, "0")}${String(x.getDate()).padStart(2, "0")}`; })();
  const shown = daily.filter((r) => r.d >= cut);
  const all = (co.valuation ?? []).filter((r) => mode === "quarter" || r.period.endsWith(".12"));
  const rows = all.slice(-(years * (mode === "quarter" ? 4 : 1)));
  if (!rows.some((r) => r.per != null || r.pbr != null) && !daily.some((r) => r.per != null)) return null;
  const periods = rows.map((r) => (mode === "annual" ? `${r.period.slice(0, 4)}년` : qLabel(r.period)));
  const per: Series = { key: "per", label: "PER", color: C1, kind: "line", fmt: "x", src: "KRX·DART", values: rows.map((r) => r.per ?? null) };
  const pbr: Series = { key: "pbr", label: "PBR", color: OR, kind: "line", fmt: "x", src: "KRX·DART", values: rows.map((r) => r.pbr) };
  // EPS·BPS: 분할 전 값은 지금 주식 수 기준으로 나눠 맞추고 * 표시. EPS = 회사 공시 기본주당이익 (분기: 최근 4분기 합, 연간: 그해 공시값)
  const split = rows.map((r) => (r.split ? "derived" as const : null));
  const epsOf = (r: (typeof rows)[number]) => (mode === "annual" ? r.eps_y : r.eps) ?? null;
  const eps: Series = { key: "eps", label: "EPS", color: C1, kind: "bar", fmt: "krw", src: "DART", values: rows.map(epsOf), marks: split };
  const bps: Series = { key: "bps", label: "BPS", color: OR, kind: "bar", fmt: "krw", src: "DART", values: rows.map((r) => r.bps ?? null),
    marks: rows.map((r) => (r.split || r.bps_calc ? "derived" as const : null)) };
  const hasPS = rows.some((r) => epsOf(r) != null || r.bps != null);
  const splits = (rows.some((r) => r.split) ? co.valuation ?? [] : []).filter((r) => r.split_at).map((r) => `${r.period.slice(0, 4)}년 ${+r.period.slice(5, 7)}월 ${r.split_at! >= 1 ? `1주→${Math.round(r.split_at!)}주` : `${Math.round(1 / r.split_at!)}주→1주`}`);
  const byDay = new Map(daily.map((r) => [r.d, r]));
  const won = (v: number) => `${Math.round(v).toLocaleString("ko-KR")}원`;
  // 마우스 옆 상자 (주가 차트처럼): 그날 주가, EPS(BPS)와 어느 실적인지, PER(PBR)
  const tipOf = (which: "per" | "pbr") => (d: string): [string, string][] | null => {
    const r = byDay.get(d);
    if (!r) return null;
    const v = which === "per" ? r.per : r.pbr, base = which === "per" ? r.eps : r.bps;
    return [["주가", won(r.c)], [which === "per" ? "EPS" : "BPS", base != null ? won(base) : "-"],
      ["기준 실적", r.from ? `${r.from.slice(0, 4)}.${r.from.slice(5)}` : "-"],
      [which === "per" ? "PER" : "PBR", v != null ? `${v.toFixed(which === "per" ? 1 : 2)}배` : "-"]];
  };
  const makeExport = (o: PxOpts): XlsxPayload => {
    const inRange = o.range === "custom" ? daily.filter((x) => x.d >= o.from.replaceAll("-", "") && x.d <= o.to.replaceAll("-", ""))
      : o.range === "all" ? daily : daily.slice(-Number(o.range));
    const key = (d: string) => (o.step === "m" ? d.slice(0, 6) : weekKey(d));
    const sel = o.step === "d" ? inRange : inRange.filter((x, i) => i === inRange.length - 1 || key(inRange[i + 1].d) !== key(x.d));
    const ps = sel.map((x) => day(x.d));
    const basis = `${o.step === "d" ? "일별" : o.step === "w" ? "주말 값" : "월말 값"}, 종가(수정주가) ÷ 그날까지 공시된 최근 EPS·BPS`;
    const src = ["토스증권", "OpenDART"];
    return { title: `${co.name} PER·PBR`, periods: [], rows: [], sheets: [
      { title: `${co.name} PER`, sheet: "PER", ticker: co.ticker, basis, periods: ps, sources: src, rows: [{ label: "PER", fmt: "x", kind: "line", values: sel.map((x) => x.per) }] },
      { title: `${co.name} PBR`, sheet: "PBR", ticker: co.ticker, basis, periods: ps, sources: src, rows: [{ label: "PBR", fmt: "x", kind: "line", values: sel.map((x) => x.pbr) }] },
      { title: `${co.name} PER·PBR 일별 자료`, sheet: "일별 자료", ticker: co.ticker, basis, periods: ps, sources: src, no_chart: true, rows: [
        { label: "종가", fmt: "krw", kind: "line", values: sel.map((x) => x.c) }, { label: "EPS (최근 4분기)", fmt: "krw", kind: "line", values: sel.map((x) => x.eps) },
        { label: "BPS", fmt: "krw", kind: "line", values: sel.map((x) => x.bps) }, { label: "PER", fmt: "x", kind: "line", values: sel.map((x) => x.per) },
        { label: "PBR", fmt: "x", kind: "line", values: sel.map((x) => x.pbr) }] },
    ] };
  };
  return (
    <div className={c.valTrend}>
      <div className={c.sectionHead}>
        <h3>PER·PBR·EPS·BPS 추이<small>일별 종가 ÷ 공시일 기준 EPS·BPS</small></h3>
        <div className={c.filters}>
          <Segment label="기간" value={years} onChange={setYears} options={[[3, "3년"], [5, "5년"], [10, "10년"]]} />
          {daily.length > 0 && <button type="button" className={c.exportBtn} onClick={() => setExp(true)}>엑셀로 내보내기</button>}
        </div>
      </div>
      {exp && daily.length > 0 && <PriceExport title="PER·PBR 엑셀로 내보내기" first={daily[0].d} last={daily[daily.length - 1].d} onClose={closeExp} make={makeExport} />}
      {hist == null ? <p className={c.empty}>주가 자료를 받는 중…</p> : (
        <div className={c.cmpGrid}>
          <div><div className={c.chartUnit}>PER<small> 배</small></div>
            <DayChart key={`per${years}`} rows={shown} fmt="x" height={228} format={(v) => `${v.toFixed(1)}배`} tip={tipOf("per")}
              lines={[{ label: "PER", color: C1, get: (r) => r.per }]} /></div>
          <div><div className={c.chartUnit}>PBR<small> 배</small></div>
            <DayChart key={`pbr${years}`} rows={shown} fmt="x" height={228} format={(v) => `${v.toFixed(2)}배`} tip={tipOf("pbr")}
              lines={[{ label: "PBR", color: OR, get: (r) => r.pbr }]} /></div>
        </div>
      )}
      {hasPS && <>
        <div className={c.sectionHead} style={{ marginTop: 12 }}>
          <span className={c.chartUnit}>EPS·BPS</span>
          <div className={c.filters}><Segment label="연간·분기" value={mode} onChange={setMode} options={[["annual", "연간"], ["quarter", "분기"]]} /></div>
        </div>
        <div className={c.cmpGrid}>
          {[eps, bps].map((x) => (
            <div key={x.key}><div className={c.chartUnit}>{x.label}<small> 원</small></div>
              <FinChart key={`${x.key}${mode}${years}`} periods={periods} series={[x]} height={200} /></div>
          ))}
        </div>
      </>}
      <FinTable periods={periods} series={hasPS ? [per, pbr, eps, bps] : [per, pbr]} />
      <Notes items={[
        "PER: 그날 종가 ÷ 그날까지 공시된 최근 EPS (회사 공시 기본주당이익 최근 4분기 합, 0 이하면 '-'). 표의 값은 기간 말 날짜 기준",
        "PBR: 그날 종가 ÷ 그날까지 공시된 최근 BPS (지배주주 자본 ÷ 유통주식수)",
        "공시일: 보고서 접수일과 법정 제출 기한(분기·반기 45일, 사업보고서 90일) 중 빠른 날",
        hasPS && "EPS: 분기는 최근 4분기 합, 연간은 그해 사업보고서 값",
        splits.length > 0 && `*: 액면분할·병합(${splits.join(", ")}) 전 값을 지금 주식 수 기준으로 나눠 맞춘 값`,
      ]} />
    </div>
  );
}

/* ───────── 재무 ───────── */
// 지표 탐색 목록 (2026-11-22): 화면에 이미 있던 값만 모은다. 비율·증가율은 공시 값을 코드가 나눈 것('계산' 표시)
const FLOW = new Set<string>(["revenue", "op", "ni", "gross", "sga", "capex", "cogs", "pretax", "ni_owner", "cfo", "cfi", "cff"]);
const NOTE: Record<string, string> = {
  rnd: "연구개발비용 계 (사업보고서 연구개발 활동)", rnd_ratio: "연구개발비용 계 ÷ 매출액 (공시 값)",
  backlog: "계약했으나 아직 매출로 인식하지 않은 금액, 연말 기준 (사업보고서 수주상황)",
  debt: "부채총계 ÷ 자본총계", cur: "유동자산 ÷ 유동부채", roe: "분기·반기: 최근 4분기 지배주주 순이익 ÷ ((지금 + 1년 전 지배주주 자본) / 2). 연간: 그해 지배주주 순이익 ÷ ((전년 말 + 그해 말) / 2). 비지배지분이 없으면 전체 값, *: 지배주주 값을 전체 − 비지배지분으로 계산. 첫해의 1년 전 자본(금융회사 2022년 말)은 다음 해 사업보고서의 전기말 칸",
  op_g: "전년 같은 기간 대비, 기준이 적자면 표시 안 함", ni_g: "전년 같은 기간 대비, 기준이 적자면 표시 안 함",
  opm: "영업이익 ÷ 매출액", gpm: "매출총이익 ÷ 매출액", sgar: "판매비와관리비 ÷ 매출액",
  dio: "재고자산 ÷ 매출원가 × 기간 일수", capex: "현금흐름표의 유형자산 취득", capexr: "설비투자 ÷ 매출액",
  csm: "보유 계약에서 앞으로 인식할 미실현 이익", csm_new: "그 분기 새 계약으로 추가 적립된 CSM",
  csm_amort: "CSM 중 그 분기 보험이익으로 인식된 금액", kics: "가용자본 ÷ 요구자본 (법정 최소 100%)",
  ins_profit: "손익계산서의 보험서비스결과 (보험수익 − 보험서비스비용 등)", inv_profit: "손익계산서의 투자손익 (투자수익 − 투자비용)",
  div_rev: "손익계산서의 배당금수익 (보유 주식에서 받은 배당)",
  roa: "분기·반기: 최근 4분기 순이익 ÷ ((지금 + 1년 전 총자산) / 2). 연간: 그해 순이익 ÷ ((전년 말 + 그해 말 총자산) / 2)",
};
const EXTRA: [FinKey, string][] = [["assets", "자산총계"], ["pretax", "법인세차감전순이익"], ["ni_owner", "지배주주 순이익"], ["equity_owner", "지배주주 자본"],
  ["cogs", "매출원가"], ["div_rev", "배당수익"], ["cfo", "영업활동 현금흐름"], ["cfi", "투자활동 현금흐름"], ["cff", "재무활동 현금흐름"]];
// 업권별 화면 (2026-11-24 사용자): 금감원(FISIS)에서 업권을 찾은 회사 = 금융회사. 묶음 = 사업 지표 / 이익 구성 / 규모 + 공통(수익성·성장성·안정성)
export const BIZ_CAT = "사업 지표", PL_CAT = "이익 구성", SIZE_CAT = "규모";
export const FSS_CAT = "사업 지표";       // 금감원 값 묶음 (2026-11-26 사용자: 이름 '사업 지표', 출처 옆에 별도·연결)
const VAL_CAT = "투자 지표";      // 사업지표 개편 (2026-11-25): FISIS 값은 DART 숫자와 묶음을 나눈다 (회사 개별 기준)
const finPart = (co: Company) => (co.sector && !co.sector.error && co.sector.part ? co.sector.part : null);
const isInsPart = (co: Company) => ["H", "I"].includes(finPart(co) ?? "") || isInsurer(co);
const ACCOUNT_CAT = "계정";

// 보험사: DART CSM 이 있거나 금감원 업권이 생보·손보 (K-ICS 는 이제 금감원 값만이라 DART kics 로는 판단하지 않는다)
const isInsurer = (co: Company) => !!co.insurance?.some((r) => r.csm != null) || ["H", "I"].includes(co.sector && !co.sector.error ? co.sector.part ?? "" : "");

function catalog(co: Company, groups: Group[], mode: Mode, years: number): Metric[] {
  const st = co.statements;
  const list = periodList(st, mode);
  const rows = sliceRange(list, mode, years);
  const keys = rows.map((p) => p.period);
  const out: Metric[] = [];
  const seen = new Set<string>();
  const push = (m: Metric) => { if (seen.has(m.key) || !m.values.some((v) => v != null)) return; seen.add(m.key); out.push(m); };
  const yearEnd = (k: string) => (mode === "annual" ? `${k}.12` : k);       // 연간은 12월 말 값 (잔액·비율만)

  // 보험사: CSM·K-ICS (분기 흐름 값은 분기 화면에서만)
  const ins = co.insurance ?? [];
  if (isInsurer(co)) {
    const src = ins.some((r) => r.src?.csm === "FISIS" || r.src?.kics === "FISIS") ? "DART·FISIS" : "DART";
    const at = (k: string) => ins.find((r) => r.period === yearEnd(k));
    ([["csm", "CSM 잔액", "line", "won", false], ["csm_new", "신계약 CSM", "bar", "won", true], ["csm_amort", "CSM 상각액", "bar", "won", true],
      ] as const).forEach(([key, name, kind, fmt, flow]) => {      // K-ICS 는 '금감원 업권 지표'에서 (DART 값은 뺌, 2026-11-25 사용자)
      if (flow && mode !== "quarter") return;
      push({ key, label: name, kind, fmt, cat: key === "csm_amort" ? PL_CAT : SIZE_CAT, src, note: NOTE[key],
        values: keys.map((k) => at(k)?.[key] ?? null), marks: keys.map((k) => (at(k)?.derived?.includes(key) || (key === "csm" && at(k)?.derived?.includes("csm_prior")) ? "derived" : null)) });
    });
  }
  // 보험사 이익 구성 (DART 손익계산서): 보험손익·투자손익. 보험사만 (지주사·카드사 손익계산서의 보험 줄은 보이지 않는다)
  if (isInsPart(co)) {
    ([["ins_profit", "보험손익"], ["inv_profit", "투자손익"]] as const).forEach(([k, name]) => {
      push({ key: k, label: name, fmt: "won", kind: "bar", cat: PL_CAT, src: "DART",
        values: rows.map((p) => p[k] ?? null), marks: rows.map((p) => mark(p, k)), note: NOTE[k] });
    });
  }
  // 사업보고서 본문 표: 연구개발비·수주잔고 (연간 값. 분기 화면에서는 12월 칸에만, 이름에 '연간')
  const bz = finPart(co) ? null : co.business;
  if (bz) {
    const yr = (k: string) => (mode === "annual" ? k : k.endsWith(".12") ? k.slice(0, 4) : "");
    const rnd = (k: string) => bz.rnd.find((r) => String(r.year) === yr(k));
    const bl = (k: string) => bz.backlog.find((r) => String(r.year) === yr(k));
    const ann = mode === "annual" ? "" : " (연간)";
    push({ key: "rnd", label: `연구개발비${ann}`, kind: "bar", fmt: "won", cat: "사업 지표", src: "DART", note: NOTE.rnd, values: keys.map((k) => rnd(k)?.amount ?? null) });
    push({ key: "rnd_ratio", label: `연구개발비 / 매출액${ann}`, kind: "line", fmt: "pct", cat: "사업 지표", src: "DART", note: NOTE.rnd_ratio, values: keys.map((k) => rnd(k)?.ratio ?? null) });
    push({ key: "backlog", label: "수주잔고", kind: "line", fmt: "won", cat: "사업 지표", src: "DART", note: NOTE.backlog, values: keys.map((k) => bl(k)?.amount ?? null) });
  }
  // 금융회사: 금감원 업권 지표 — 한 묶음 '금감원 업권 지표 (개별)' (DART 숫자와 섞지 않는다. 보험 K-ICS 도 금감원 값은 여기)
  const s = co.sector && !co.sector.error ? co.sector : null;
  if (s?.series?.length) {
    s.metrics.forEach((m) => {
      const flow = m.unit === "원", money = flow || m.unit === "잔액";     // 흐름 금액: 분기 값, 연간은 12월 누적 / 잔액: 그 시점 값
      // 출처 옆에 기준 (2026-11-26 사용자): 금감원 표 이름이 '연결'(금융지주 요약연결손익계산서)이면 연결, 나머지는 회사 개별(별도) 업무보고서
      // 금융지주(L)는 BIS·CET1·필요자본비율·여신건전성도 그룹 연결 값 (2026-11-26 확인: KB 총여신 495.7조·위험가중자산 346조 = 그룹 규모).
      // 이중레버리지비율만 지주회사 개별 자기자본 기준. 다른 업권 기준 확인은 2차
      const con = /\(연결\)/.test(m.label) || (s.part === "L" && m.key !== "dlev"), name = m.label.replace(/\s*\(연결\)/, "");
      // 같은 이름의 DART 지표가 있으면(보험손익·투자손익) 금감원 값임을 이름에도 붙인다 (차트 범례에서 구분)
      push({ key: `fisis_${m.key}`, label: out.some((x) => x.label === name) ? `${name} (금감원)` : name, kind: flow ? "bar" : "line", fmt: money ? "won" : m.unit === "수" ? "num" : "pct",
        cat: FSS_CAT, src: con ? "FISIS 연결" : "FISIS 별도", note: m.desc || undefined,
        values: keys.map((k) => fisisValue(s.series, k, m.key, flow, mode)) });
    });
  }
  // 핵심 재무제표 (금융회사, 2026-11-24): 이익 구성·규모. 연간 값이라 분기·반기 화면은 12월 칸에 '(연간)'
  const core = co.core;
  if (core) {
    const PL: Record<string, string[]> = { bank: ["net_int", "net_fee", "provision", "sga"],
      holding: ["net_int", "net_fee", "provision", "ins_result", "fv_pl", "other_op", "sga"],
      card: ["net_int", "net_fee", "lease_net", "provision", "sga"], securities: ["net_fee", "net_int", "fv_net", "sga", "personnel"] };
    const SZ: Record<string, string[]> = { bank: ["loans", "deposits"], holding: ["loans", "deposits", "ins_liab"],
      card: ["fin_ac", "bonds", "borrow"], securities: ["sec_pl", "deposits", "borrow"], insurance: ["ins_liab", "inv_contract"] };
    const yr = (k: string) => (mode === "annual" ? k : k.endsWith(".12") ? k.slice(0, 4) : "");
    const ann = mode === "annual" ? "" : " (연간)";
    const at = (key: string, k: string) => core.cells[key]?.[yr(k)];
    const val = (key: string, k: string) => { const x = at(key, k); return x && x.st !== "na" ? x.v ?? null : null; };
    const add = (key: string, cat: string, kind: "bar" | "line") => push({
      key: `core_${key}`, label: `${core.items.find((i) => i.key === key)?.label ?? key}${ann}`, fmt: "won", kind, cat, src: "DART",
      values: keys.map((k) => val(key, k)), marks: keys.map((k) => (at(key, k)?.st === "derived" ? "derived" : null)) });
    (PL[core.template] ?? []).forEach((k) => add(k, PL_CAT, "bar"));
    (SZ[core.template] ?? []).forEach((k) => add(k, SIZE_CAT, "line"));
    if (core.template === "bank" || core.template === "holding") {
      push({ key: "core_ldr", label: `예대율${ann}`, fmt: "pct", kind: "line", cat: SIZE_CAT, src: "DART", calc: true, note: "대출채권 ÷ 예수부채",
        values: keys.map((k) => ratio(val("loans", k), val("deposits", k))) });
    }
  }
  // 재무 묶음 (안정성·수익성 …)
  for (const g of groups) {
    for (const x of groupSeries(g, list, mode, years)) {
      push({ key: x.key, label: x.label, fmt: x.fmt, values: x.values, marks: x.marks, signed: x.signed,
        kind: x.fmt === "won" && FLOW.has(x.key) ? "bar" : "line", cat: GROUPS[g], calc: x.fmt !== "won", src: "DART", note: NOTE[x.key] });   // 비율도 DART 값으로만 계산 → 출처 DART
    }
  }
  // 투자 지표 (2026-11-26 사용자: 기업개요의 투자 지표를 재무에도, 모든 회사). 시세 지표는 분기 말 값(연간·반기는 그 기간 말),
  // 배당은 회사 공시 연간 값(분기·반기 화면은 12월 칸에 '연간'), ROE 는 수익성 ROE 와 같은 값
  const val = co.valuation ?? [];
  const vAt = (k: string) => val.find((r) => r.period === yearEnd(k));
  if (val.length) {
    ([["v_close", "종가(기간 말)", "krw", "close", "분기 마지막 거래일 종가 (KRX)"],
      ["v_mktcap", "시가총액(기간 말)", "won", "mktcap", "기간 마지막 거래일 KRX 시가총액 (보통주 종가 × 상장주식수, 자기주식 포함·우선주 제외)"],
      ["v_per", "PER", "x", "per", "기간 말 종가 ÷ 그날까지 공시된 최근 EPS (회사 공시 기본주당이익 최근 4분기 합)"],
      ["v_pbr", "PBR", "x", "pbr", "기간 말 종가 ÷ 그날까지 공시된 최근 BPS"],
      ["v_eps", "EPS", "krw", mode === "annual" ? "eps_y" : "eps", mode === "annual" ? "그해 회사 공시 기본주당이익 (연간)" : "회사 공시 기본주당이익, 최근 4분기 합 (4분기 = 연간 − 3분기 누적)"],
      ["v_bps", "BPS", "krw", "bps", "기간 말 지배주주 자본 ÷ 그 분기 유통주식수(자기주식 제외·우선주 포함, 없으면 앞 보고서). *: 지배주주 자본을 전체 − 비지배지분으로 계산하거나 액면분할 보정"]] as const).forEach(([key, name, fmt, f, note]) =>
      push({ key, label: name, kind: "line", fmt, cat: VAL_CAT, src: "KRX·DART", calc: key !== "v_close", note,
        values: keys.map((k) => vAt(k)?.[f] ?? null),
        marks: f === "eps" || f === "eps_y" || f === "bps" ? keys.map((k) => (vAt(k)?.split || (f === "bps" && vAt(k)?.bps_calc) ? "derived" : null)) : undefined }));
  }
  const roeM = out.find((m) => m.key === "roe");
  if (roeM) push({ ...roeM, key: "v_roe", cat: VAL_CAT });
  const dvs = co.dividends ?? [];
  if (dvs.length) {
    const dyr = (k: string) => (mode === "annual" ? k : k.endsWith(".12") ? k.slice(0, 4) : "");
    const dAt = (k: string) => dvs.find((r) => String(r.year) === dyr(k));
    const ann = mode === "annual" ? "" : " (연간)";
    push({ key: "v_dps", label: `주당배당금${ann}`, kind: "bar", fmt: "krw", cat: VAL_CAT, src: "DART", note: "보통주 1주당 현금배당 (회사 공시)", values: keys.map((k) => dAt(k)?.dps ?? null) });
    push({ key: "v_yield", label: `시가배당률${ann}`, kind: "line", fmt: "pct", cat: VAL_CAT, src: "DART", note: "주당배당금 ÷ 배당기준일 무렵 주가 (회사 공시)", values: keys.map((k) => dAt(k)?.yield ?? null) });
    push({ key: "v_divy", label: "배당수익률", kind: "line", fmt: "pct", cat: VAL_CAT, src: "KRX·DART", calc: true,
      note: "그날까지 확정된(사업보고서 공시) 최근 연간 주당배당금 ÷ 기간 말 종가. 분기배당은 연간 합계로만",
      values: keys.map((k) => vAt(k)?.divy ?? null) });
    push({ key: "v_payout", label: `배당성향${ann}`, kind: "line", fmt: "pct", cat: VAL_CAT, src: "DART", note: "현금배당 총액 ÷ 연결 당기순이익 (회사 공시)", values: keys.map((k) => dAt(k)?.payout ?? null) });
  }
  // EBITDA (2026-11-24, 사용자): 영업이익 + 감가상각비·상각비(재무제표 주석). 분기·반기·연간. 금융회사는 넣지 않는다
  if (!finPart(co)) {
    const ebitda = rows.map((p) => (p.op != null && p.da != null ? p.op + p.da : null));
    const emark = rows.map((p) => (p.derived?.includes("da") || p.derived?.includes("op") ? "derived" as const : null));
    const basis = [...new Set(rows.map((p) => p.da_basis).filter(Boolean))].join(", ");
    push({ key: "ebitda", label: "EBITDA", fmt: "won", kind: "bar", cat: GROUPS.sales, src: "DART", values: ebitda, marks: emark,
      note: `영업이익 + ${basis || "감가상각비 + 무형자산상각비"} (재무제표 주석)` });
    push({ key: "ebitda_m", label: "EBITDA 마진", fmt: "pct", kind: "line", cat: GROUPS.sales, src: "DART", calc: true,
      values: rows.map((p, i) => ratio(ebitda[i], p.revenue)), marks: emark, note: "EBITDA ÷ 매출액" });
  }
  // 주요 계정 (위 묶음에 없는 것). 분기 현금흐름은 연초 누적에서 앞 분기 누적을 빼 3개월 값으로
  for (const [k, name] of EXTRA) {
    const cf = k === "cfo" || k === "cfi" || k === "cff";
    push({ key: k, label: name, fmt: "won", kind: FLOW.has(k) ? "bar" : "line", cat: "주요 계정", src: "DART",
      values: rows.map((p) => (mode === "quarter" && cf ? quarterFlow(list, p, k) : p[k] ?? null)),
      marks: rows.map((p) => (mode === "quarter" && cf && +p.period.slice(5) !== 3 ? "derived" : mark(p, k))),
      note: cf && mode === "quarter" ? "분기 값 = 연초 누적 − 앞 분기 누적" : undefined });
  }
  // 재무제표의 나머지 계정 전부
  ([["IS", "손익계산서"], ["BS", "재무상태표"], ["CF", "현금흐름표"]] as const).forEach(([tab, name]) => {
    const t = st.tables[tab];
    if (!t) return;
    const cols = tableCols(t, tab, mode);
    // 계정 목록 (2026-11-26 사용자): 위 지표와 겹쳐도 계정은 모두 넣는다. 다만 ① 값이 전부 0인 계정은 빼고,
    // ② 같은 표 안에서 모든 기간 값이 같은 줄은 먼저 나온 하나만 남긴다 (예: 희석주당이익 = 기본주당이익이면 희석은 뺌)
    const pers = Object.keys(cols);
    const sig = (acc: string) => pers.map((p) => cols[p]?.[acc] ?? "").join(",");
    const kept = new Set<string>();
    t.accounts.forEach((a) => {
      const acc = `${a.id}|${a.name}`;
      const vals = pers.map((p) => cols[p]?.[acc]).filter((v): v is number => v != null);
      if (vals.length && vals.every((v) => v === 0)) return;
      const s = sig(acc);
      if (vals.length && kept.has(s)) return;
      kept.add(s);
      push({ key: `${tab}:${acc}`, label: tab === "CF" && mode === "quarter" ? `${a.name} (연초 누적)` : a.name, fmt: "won",
        kind: tab === "BS" ? "line" : "bar", cat: `${name} ${ACCOUNT_CAT}`, src: "DART", values: keys.map((k) => cols[k]?.[acc] ?? null) });
    });
  });
  // 내가 만든 계산 지표 (식은 브라우저에 저장, 회사마다 같은 식을 그 회사 값으로 계산)
  return applyFormulas(out, (f, values, marks, src) => ({ key: `calc:${f.id}`, label: f.name, fmt: f.fmt, kind: f.fmt === "won" ? "bar" : "line",
    cat: CALC_CAT, calc: true, src, note: `${f.expr}${f.scale100 ? " × 100" : ""}`, values, marks }));
}

function Financial({ co: base, cardsUrl }: { co: Company; cardsUrl: string }) {
  // 연결·별도 (2026-11-24, 사용자): 연결 재무가 기본. '별도'를 누르면 회사 단독 재무를 받아 같은 화면에
  const [basis, setBasis] = useState<"con" | "sep">("con");
  const [sep, setSep] = useState<{ st?: Company["statements"]; core?: Company["core"]; err?: string; busy?: boolean }>({});
  const pickBasis = (b: "con" | "sep") => {
    setBasis(b);
    if (b === "sep" && !sep.st && !sep.busy) {
      setSep({ busy: true });
      fetch(`/api/stocks/${base.ticker}/separate`).then((r) => r.json())
        .then((j) => setSep(j?.statements ? { st: j.statements, core: j.core ?? null } : { err: j?.error ?? "별도 기준 재무를 받지 못했습니다." }))
        .catch(() => setSep({ err: "별도 기준 재무를 받지 못했습니다." }));
    }
  };
  const co0: Company = basis === "sep" && sep.st ? { ...base, statements: sep.st, core: sep.core ?? null, insurance: null } : base;
  const co = padSector(co0);                           // 차트 가로축: DART + 금감원 기간 (재무제표 표는 co0 그대로)
  const st = co.statements;
  const peers = usePeers(co);
  const cards = useCards(cardsUrl);
  const common = useCards("/api/charts");
  const sepPeers = useSeparatePeers(peers.ready, basis === "sep" && !!sep.st);        // 별도를 고르면 비교 회사도 별도
  const cmpReady = sepPeers.ready;                 // 모든 회사 공통 차트
  const groups = groupsOf(co);
  const [mode, setMode] = useState<Mode>("quarter");
  const [daily, setDaily] = useState(false);           // 일간 (2026-11-26 사용자): 일별 지표만, 일별 차트
  const [years, setYears] = useState(3);
  useFormulas();                                       // 계산 지표를 더하거나 지우면 다시 그린다
  const list = periodList(st, mode);
  const periods = sliceRange(list, mode, years).map((p) => label(p, mode));
  const metrics = catalog(co, groups, mode, years);
  const catNames = [...new Set(metrics.map((m) => m.cat))];
  for (const top of [SIZE_CAT, PL_CAT, FSS_CAT, BIZ_CAT, CALC_CAT]) {          // 맨 위부터: 내 계산 지표, 사업 지표, 이익 구성, 규모
    if (catNames.includes(top)) { catNames.splice(catNames.indexOf(top), 1); catNames.unshift(top); }
  }
  const cats = catNames.map((name) => ({ name, open: false }));          // 처음엔 모두 닫힘, 고른 지표가 있는 묶음만 열림
  const insurer = isInsurer(co);
  const want = insurer ? ["ni", "roe"]
    : co.financial && co.sector?.series?.length ? [`fisis_${co.sector.metrics[0]?.key}`, "ni"]
      : ["revenue", "op", "opm"];
  const defaults = want.filter((k) => metrics.some((m) => m.key === k));
  const controls = (
    <div className={c.filters}>
      <Segment label="기간" value={years} onChange={setYears} options={FS_RANGE} />
      <Segment<ModeD> label="연간·반기·분기·일간" value={daily ? "daily" : mode} onChange={(v) => { if (v === "daily") setDaily(true); else { setDaily(false); setMode(v); } }} options={MODE_OPTS} />
    </div>
  );
  return (
    <>
      {insurer && <Insurance co={co} />}
      <Section title="지표 탐색" sub={st.fs ? `${st.fs} 기준` : undefined}
               right={base.statements.fs === "연결" ? <Segment label="연결·별도" value={basis} onChange={pickBasis} options={[["con", "연결"], ["sep", "별도"]]} /> : undefined}>
        {basis === "sep" && !sep.st && <p className={c.empty}>{sep.err ?? "별도 기준 재무를 받는 중입니다."}</p>}
        <MetricExplorer key={co.ticker} metrics={metrics} cats={cats} periods={periods} controls={controls} back={PER_YEAR[mode]}
                        me={{ name: co.name, color: ME_COLOR }}
                        peerPane={peers.any ? <PeerPicker co={co} p={peers} pane /> : undefined}
                        compare={cmpReady.length ? (m) => cmpReady.map((x) => peerLine(x, m, mode, years, sliceRange(list, mode, years))) : undefined}
                        onSave={(keys, together, all) => (all ? common : cards).add({ keys, together, mode, years, common: all || undefined, daily: daily || undefined,
                          peers: all ? [] : peers.checked.map(([id]) => id),             // 공통 차트에는 비교 회사를 넣지 않는다 (회사마다 다르다)
                          title: keys.map((k) => metrics.find((m) => m.key === k)?.label ?? k).join(" · ") })}
                        exportInit={{ mode, years }}
                        daily={daily ? { keys: DAILY_KEYS, render: (keys) =>
                          <DailyMetrics keys={keys} subjects={subjectsOf(co, cmpReady)} from={fromOf(sliceRange(list, mode, years)[0]?.period, mode)} name={co.name} /> } : undefined}
                        onExport={(series0, focus, cmp0, together, o) => {
                          // 엑셀 창에서 고른 기간·단위 (2026-11-26 사용자). 화면과 다르면 같은 지표·같은 비교 회사로 다시 계산
                          const md = o?.mode ?? mode, yr = o?.years ?? years;
                          const same = md === mode && yr === years;
                          const all2 = same ? metrics : catalog(co, groups, md, yr);
                          const series = same ? series0 : series0.map((x) => all2.find((m) => m.key === x.key)).filter((m): m is Metric => !!m)
                            .map((m) => ({ ...m, color: series0.find((x) => x.key === m.key)?.color ?? ME_COLOR }));
                          const rows2 = sliceRange(periodList(st, md), md, yr);
                          const cmp = !cmp0 || same ? cmp0 : series.flatMap((m, k): XlsxRow[] => [
                            { label: `${m.label} · ${co.name}`, legend: co.name, group: m.key, fmt: m.fmt, kind: m.kind, values: m.values, calc: m.calc, src: m.src, strong: true },
                            ...cmpReady.map((x) => { const l = peerLine(x, m, md, yr, rows2);
                              return { label: `${m.label} · ${l.label}`, legend: l.label, group: m.key, fmt: l.fmt, kind: l.kind, values: l.values, calc: l.calc, src: l.src,
                                dashed: !!together && k === 1 && m.kind === "line" }; })]);
                          return { together,
                          title: `${co.name} 재무 지표`, sheet: "재무 지표", ticker: co.ticker,
                          basis: `${st.fs ?? ""} 기준, ${MODE_NAME[md]}${md === "annual" && (insurer || co.sector?.series?.length) ? " (사업 지표 비율·잔액은 12월 말 값)" : ""}`,
                          periods: rows2.map((p) => p.period),
                          rows: cmp ?? toRows(focus ? [...series.filter((x) => x.key === focus), ...series.filter((x) => x.key !== focus)] : series, focus),
                          notes: series.map((x) => metrics.find((m) => m.key === x.key)?.note && `${x.label}: ${metrics.find((m) => m.key === x.key)!.note}`).filter(Boolean) as string[],
                          sources: [...new Set([...new Set([...series, ...(cmp ?? [])].flatMap((x) => (x.src ?? "DART").split("·")))].map((x) => (x === "DART" ? "OpenDART" : x.startsWith("FISIS") ? "금융감독원 금융통계정보시스템" : x)))],
                        }; }}
                        defaults={defaults.length ? defaults : metrics.slice(0, 2).map((m) => m.key)} />
        <Notes items={[
          mode === "quarter" && "*: 계산 값 (12월 = 연간 − 3분기 누적, 분기 현금흐름 = 누적 차감)",
          mode === "half" && "반기: 손익 = 두 분기 합, 재무상태 = 6·12월 말 값. *: 계산 값 (하반기 현금흐름 = 연간 − 상반기 누적)",
          basis === "sep" && peers.ready.length > 0 && `비교 회사: 상장사는 별도 기준, 금감원 자료는 회사 개별 기준, 비상장 DART 회사는 연결 기준${sepPeers.waiting ? ` (별도 재무 받는 중 ${sepPeers.waiting}곳)` : ""}`,
          metrics.some((m) => m.marks?.some((x) => x === "missing")) && "'-': 공시 API 값 오류로 표시 안 함",
          mode === "annual" && (insurer || co.sector?.series?.length) && "연간 화면의 사업 지표: 비율·잔액 = 12월 말 값, 금액 = 1~12월 합",
        ]} />
      </Section>
      {/* 목차: 지표 탐색 / 저장된 차트 / 재무제표 (2026-11-24, 사용자) */}
      <Section title="저장된 차트" sub={`${cards.list.length + common.list.length}개`}>
        {cards.list.length + common.list.length > 0
          ? <div className={c.cardGrid}>
              {common.list.map((cd) => <ChartCard key={cd.id} co={co} card={{ ...cd, common: true }} groups={groups}
                                                  onRemove={() => common.remove(cd.id)} onRename={(t) => common.rename(cd.id, t)} />)}
              {cards.list.map((cd) => <ChartCard key={cd.id} co={co} card={cd} groups={groups}
                                                 onRemove={() => cards.remove(cd.id)} onRename={(t) => cards.rename(cd.id, t)} />)}
            </div>
          : <p className={c.empty}>저장된 차트가 없습니다.</p>}
      </Section>
      {co.core ? <CoreTable core={co.core} /> : null}
      {co.core ? <details className={c.allAccounts}><summary>회사 재무제표 전체 계정 (DART 그대로)</summary><Statements st={co0.statements} /></details>
        : <Statements st={co0.statements} />}
    </>
  );
}

/* ───────── 핵심 재무제표 (2026-11-24 사용자): 업권별로 정한 20~30줄, 연간 10년. 칸마다 출처 상태 ───────── */
const CORE_MARK: Record<string, string> = { derived: "†", restated: "*" };
const fmtEok = (v: number) => (Math.abs(v) >= 1e8 ? `${Math.round(v / 1e8).toLocaleString("ko-KR")}억` : Math.round(v).toLocaleString("ko-KR"));
function CoreTable({ core }: { core: NonNullable<Company["core"]> }) {
  const [tab, setTab] = useState<"IS" | "BS" | "CF">("IS");          // 예전 재무제표처럼 고른 표만 (2026-11-24 사용자)
  const [years, setYears] = useState(10);
  const box = useLatest(`core${tab}${years}`);
  const ys = core.years.slice(-years).map(String).filter((y) => core.sources?.[y]);    // 보고서가 없는 해(상장·설립 전) 열은 뺀다
  const cellOf = (key: string, y: string) => core.cells[key]?.[y];
  const show = (key: string, y: string) => {
    const x = cellOf(key, y);
    if (!x) return "-";
    if (x.st === "na") return <span className={c.coreNa}>해당 없음</span>;
    if (x.st === "old_none") return <span className={c.coreNa} title={`${x.form_year}년부터 생긴 줄. 그 전 ${x.why === "basis" ? "회계기준" : "양식"}에는 같은 뜻의 줄이 없음`}>{x.why === "basis" ? "옛 기준에 없음" : "옛 양식에 없음"}</span>;
    if (x.st === "new_none") return <span className={c.coreNa} title={`${x.form_year}년부터 바뀐 양식·회계기준에는 이 줄이 없음`}>새 양식에 없음</span>;
    if (x.st === "absent") return <span className={c.coreNa} title="그 해 보고서에 같은 뜻의 줄이 없음">{x.why === "basis" ? "옛 기준에 없음" : "보고서에 없음"}</span>;
    if (x.v == null) return "-";
    const mark = (CORE_MARK[x.st] ?? "") + (x.later_v != null ? "‡" : "") + (x.old_basis || x.old_def || x.old_form ? "°" : "");
    const tip = [x.from, x.name && x.name !== x.from && `줄: ${x.name}`, x.old_form && `${x.old_form}년 양식 변경 전 값`,
      x.first_v != null && `처음 공시: ${fmtEok(x.first_v)}`, x.later_v != null && `${x.later_from ?? "다음 해 보고서"}: ${fmtEok(x.later_v)}`,
      x.st === "derived" && x.verified === false && "회사가 합계 줄을 적은 해가 없어 식을 확인하지 못함"].filter(Boolean).join(" · ");
    return <span title={tip}>{fmtEok(x.v)}{mark && <sup className={c.coreMark}>{mark}</sup>}</span>;
  };
  const rows = core.items.filter((it) => it.sj === tab && !ys.every((y) => cellOf(it.key, y)?.st === "na"));
  const edge = (y: string) => tab === "IS" && core.form_year != null && Number(y) === core.form_year && ys[0] !== y;   // 양식 경계 세로선
  const seen = (f: (x: CoreCell) => boolean) => rows.some((it) => ys.some((y) => { const x = cellOf(it.key, y); return !!x && f(x); }));
  return (
    <Section title="핵심 재무제표" sub={`${core.basis}, 억원, 연간`} right={
      <div className={c.filters}>
        <div className={c.segment} role="tablist" aria-label="재무제표 종류">
          {([["IS", "손익계산서"], ["BS", "재무상태표"], ["CF", "현금흐름표"]] as const).map(([k, v]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{v}</button>
          ))}
        </div>
        <Segment label="기간" value={years} onChange={setYears} options={[[3, "3년"], [5, "5년"], [10, "10년"]]} />
      </div>}>
      <div ref={box} className={c.tableScroll}>
        <table className={c.finTable}>
          <thead><tr><th className={c.stick}>항목</th>{ys.map((y, i) => (
            <th key={y} className={[i === ys.length - 1 && c.latest, edge(y) && c.formEdge].filter(Boolean).join(" ") || undefined}
                title={edge(y) ? `${y}년부터 회사의 새 손익계산서 양식` : undefined}>{y}년</th>))}</tr></thead>
          <tbody>
            {rows.map((it) => (
              <tr key={it.key} className={it.parent ? c.subRow : undefined}>
                <th className={c.stick} scope="row">{it.label}</th>
                {ys.map((y, i) => <td key={y} className={[i === ys.length - 1 && c.latest, edge(y) && c.formEdge].filter(Boolean).join(" ") || undefined}>{show(it.key, y)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Notes items={[
        seen((x) => x.st === "derived") && "†: 구성 항목으로 계산한 값 (예: 순이자이익 = 이자수익 − 이자비용)",
        seen((x) => x.st === "restated") && "*: 다음 해 보고서에 적힌 비교 숫자 (그 해 보고서에 없거나, 같은 묶음을 한 보고서 값으로 맞춘 칸)",
        seen((x) => x.later_v != null) && "‡: 다음 해 보고서가 다시 작성한 값과 0.5% 넘게 다름 (보이는 값은 처음 공시 값, 마우스를 올리면 두 값)",
        seen((x) => !!(x.old_basis || x.old_def || x.old_form)) && "°: 회계기준·정의 또는 회사 손익계산서 양식이 바뀌기 전 값 (옛 양식의 같은 뜻 줄로 채움). 그 뒤 해와 바로 비교하지 않음",
        tab === "IS" && core.form_year != null && ys.some((y) => Number(y) === core.form_year) && `세로선: ${core.form_year}년부터 회사의 새 손익계산서 양식`,
        rows.some((it) => it.key === "net_op_rev") && "순영업이익: 영업이익 + 판매비와관리비",
        "출처: DART (2022년 이전 금융회사는 사업보고서 원문 재무제표)",
      ]} />
    </Section>
  );
}

/** 보험사: CSM 잔액(잔액 추이), 신계약·상각(기간 흐름), K-ICS(비율 추이)를 각각의 차트로 (회사 개별 기준) */
function Insurance({ co }: { co: Company }) {
  const all = co.insurance ?? [];
  const [years, setYears] = useState(3);
  const first = all.findIndex((r) => r.csm != null || r.csm_new != null);
  const rows = all.slice(Math.max(first, all.length - years * 4, 0));
  const periods = rows.map((r) => qLabel(r.period));
  const mk = (r: (typeof all)[number], k: string) => (r.derived?.includes(k) ? "derived" as const : null);
  const bal: Series = { key: "csm", label: "CSM 잔액", color: "#2a78d6", kind: "line", fmt: "won", values: rows.map((r) => r.csm ?? null),
    marks: rows.map((r) => (r.derived?.includes("csm_prior") ? "derived" as const : null)), src: rows.some((r) => r.src?.csm === "FISIS") ? "DART·FISIS" : "DART" };
  const flow: Series[] = [
    { key: "csm_new", label: "신계약 CSM", color: "#1baf7a", kind: "bar", fmt: "won", values: rows.map((r) => r.csm_new ?? null), marks: rows.map((r) => mk(r, "csm_new")), src: "DART" },
    { key: "csm_amort", label: "CSM 상각액", color: "#eb6834", kind: "bar", fmt: "won", values: rows.map((r) => r.csm_amort ?? null), marks: rows.map((r) => mk(r, "csm_amort")), src: "DART" },
  ];
  // K-ICS 는 금감원 값만 (2026-11-25 사용자: DART 공시 본문 표 값은 뺀다 — 표 모양이 회사마다 달라 빈 분기가 많았다)
  const fss = co.sector && !co.sector.error ? co.sector.series ?? [] : [];
  const kics: Series = { key: "kics", label: "지급여력비율(K-ICS)", color: "#6250d6", kind: "line", fmt: "pct", src: "FISIS",
    values: rows.map((r) => (fss.find((x) => x.period === r.period) as Record<string, number | null> | undefined)?.kics ?? null) };
  const hasFlow = flow.some((x) => x.values.some((v) => v != null));
  return (
    <Section title="보험 핵심 지표" sub="별도 기준, 분기 (K-ICS 는 금감원)" right={
      <div className={c.filters}><Segment label="기간" value={years} onChange={setYears} options={[[1, "1년"], [3, "3년"]]} /></div>}>
      <div className={c.insGrid}>
        <div><div className={c.chartUnit}>CSM 잔액</div><FinChart periods={periods} series={[bal]} height={200} /></div>
        {hasFlow && <div><div className={c.chartUnit}>신계약 CSM · CSM 상각액 (분기 흐름)</div><FinChart periods={periods} series={flow} height={200} /></div>}
        <div><div className={c.chartUnit}>지급여력비율 (K-ICS, 경과조치 적용 전, 금감원 개별)</div><FinChart periods={periods} series={[kics]} height={200} /></div>
      </div>
      <FinTable periods={periods} series={[bal, ...flow, kics]} />
      <Notes items={[
        `CSM 잔액: ${NOTE.csm}`, `신계약 CSM: ${NOTE.csm_new}`, `CSM 상각액: ${NOTE.csm_amort}`, `K-ICS = ${NOTE.kics}`,
        rows.some((r) => r.csm_new == null) && "'-': 분기 값 미공시",
        rows.some((r) => r.derived?.includes("csm_prior")) && "CSM 잔액 *: 그 분기 보고서 원본에 주석이 없어 다음 해 같은 분기 보고서에 적힌 전기 값",
      ]} />
    </Section>
  );
}

function Statements({ st }: { st: Company["statements"] }) {
  const [tab, setTab] = useState<"IS" | "BS" | "CF">("IS");
  const [mode, setMode] = useState<Mode>("annual");
  const [years, setYears] = useState(10);
  const [open, setOpen] = useState(false);
  const t: StatementTable | undefined = st.tables[tab];
  const box = useLatest(`${tab}${mode}`);
  if (!t) return null;
  const data = tableCols(t, tab, mode);
  const cols = Object.keys(data).slice(-(Math.min(years, mode === "half" ? 5 : 10) * PER_YEAR[mode]));   // 반기는 5년까지
  const key = (a: StatementTable["accounts"][number]) => `${a.id}|${a.name}`;
  const ORDER = ["revenue", "cogs", "gross", "sga", "ins_profit", "inv_profit", "div_rev", "op", "pretax", "ni", "ni_owner", "assets", "cur_assets", "inventory", "liab", "cur_liab", "equity", "equity_owner", "cfo", "cfi", "capex", "cff"];
  const shown = (a: StatementTable["accounts"][number]) => cols.some((col) => data[col]?.[key(a)] != null);   // 고른 기간에 값이 하나도 없는 줄은 숨긴다
  const main = t.accounts.filter((a) => a.key && shown(a)).sort((x, y) => ORDER.indexOf(x.key!) - ORDER.indexOf(y.key!));
  const rest = t.accounts.filter((a) => !a.key && shown(a));
  const marked = (list?: [string, string][]) => {
    const set = new Set((list ?? []).map(([p, acc]) => `${p}#${acc}`));
    return (col: string, acc: string) => (mode === "half"          // 반기 칸은 그 반기를 이루는 분기 중 하나라도
      ? [col, `${col.slice(0, 4)}.${col.endsWith("06") ? "03" : "09"}`].some((p) => set.has(`${p}#${acc}`))
      : set.has(`${col}#${acc}`));
  };
  const isPrior = marked(t.prior), isDoc = marked(t.doc);  // 금융회사 2023년 1·2분기: 손익·현금흐름 = 2024년 보고서 전기 칸, 재무상태 = 그 분기 원문
  const cell = (col: string, a: StatementTable["accounts"][number]) => {
    const v = data[col]?.[key(a)];
    if (v == null) return "-";
    const s = Math.abs(v) >= 1e8 ? `${Math.round(v / 1e8).toLocaleString("ko-KR")}억` : Math.round(v).toLocaleString("ko-KR");
    return isPrior(col, key(a)) ? `${s}*` : isDoc(col, key(a)) ? `${s}°` : s;
  };
  const anyPrior = cols.some((col) => t.accounts.some((a) => data[col]?.[key(a)] != null && isPrior(col, key(a))));
  const anyDoc = cols.some((col) => t.accounts.some((a) => data[col]?.[key(a)] != null && isDoc(col, key(a))));
  // 첫 해 분기가 3월부터가 아니면 (금융회사: DART 재무 API 가 2023년 3분기부터) 그 앞은 API 에 없다고 적는다
  const q0 = st.quarter[0]?.period, late = q0 && st.annual[0] && q0.slice(0, 4) === st.annual[0].period && !q0.endsWith(".03") ? q0 : null;
  const head = (col: string) => (mode === "annual" ? `${col}년` : mode === "half" ? halfLabel(col) : qLabel(col));
  const row = (a: StatementTable["accounts"][number], sub = false) => (
    <tr key={key(a)} className={sub ? c.subRow : undefined}>
      <th className={c.stick} scope="row">{a.name}</th>
      {cols.map((col, i) => <td key={col} className={i === cols.length - 1 ? c.latest : undefined}>{cell(col, a)}</td>)}
    </tr>
  );
  return (
    <Section title="재무제표" sub={`${st.fs ?? ""}, 억원`} right={
      <div className={c.filters}>
        <div className={c.segment} role="tablist" aria-label="재무제표 종류">
          {([["IS", "손익계산서"], ["BS", "재무상태표"], ["CF", "현금흐름표"]] as const).map(([k, v]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setOpen(false); }}>{v}</button>
          ))}
        </div>
        <Segment label="기간" value={years} onChange={setYears} options={FS_RANGE} />
        <Segment label="연간·반기·분기" value={mode} onChange={setMode} options={[["annual", "연간"], ["half", "반기"], ["quarter", "분기"]]} />
      </div>}>
      <div ref={box} className={c.tableScroll}>
        <table className={c.finTable}>
          <thead><tr><th className={c.stick}>항목</th>{cols.map((col, i) => <th key={col} className={i === cols.length - 1 ? c.latest : undefined}>{head(col)}</th>)}</tr></thead>
          <tbody>
            {main.map((a) => row(a))}
            {rest.length > 0 && (
              <tr className={c.expandRow}>
                <th className={c.stick}>
                  <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "세부 항목 접기" : `세부 항목 ${rest.length}개 펼치기`}</button>
                </th>
                <td colSpan={cols.length} />
              </tr>
            )}
            {open && rest.map((a) => row(a, true))}
          </tbody>
        </table>
      </div>
      <Notes items={[
        mode === "quarter" && tab === "IS" && "분기 손익: 3개월 값 (12월 = 연간 − 3분기 누적)",
        mode === "quarter" && tab === "CF" && "분기 현금흐름: 연초부터 누적",
        mode === "half" && "반기: 손익 = 두 분기 합, 재무상태 = 6·12월 말, 현금흐름 = 그 반기 값 (하반기 = 연간 − 상반기 누적)",
        anyPrior && "*: 다음 해 같은 분기 보고서에 적힌 비교(전기) 숫자 (그 분기는 DART 재무 API에 없음)",
        anyDoc && "°: 그 분기 보고서 원문 표에서 읽은 숫자 (DART 재무 API에 없음)",
        mode !== "annual" && late && `${late.slice(0, 4)}년 ${+late.slice(5, 7) / 3 - 1}분기까지: DART 재무 API에 없음`,
      ]} />
    </Section>
  );
}


/* ───────── 배당 ───────── */
function DividendTrend({ co }: { co: Company }) {
  const d = co.dividends;
  if (!d.length) return null;
  const periods = d.map((x) => `${x.year}년`);
  const series: Series[] = [
    { key: "dps", label: "주당 배당금", color: C1, kind: "bar", fmt: "krw", values: d.map((x) => x.dps ?? null) },
    { key: "yield", label: "시가배당률", color: OR, kind: "line", fmt: "pct", values: d.map((x) => x.yield ?? null) },
    { key: "payout", label: "배당성향(연결)", color: OR2, kind: "line", fmt: "pct", dashed: true, values: d.map((x) => x.payout ?? null) },
  ];
  return (
    <Section title="배당 추이" sub="보통주, 사업연도">
      {/* 지표마다 작은 차트를 나란히: 각자 범위에 맞는 축이라 변화가 보인다 (한 축에 3%와 40%를 같이 두면 평평해진다) */}
      <div className={c.divGrid}>
        {series.map((x) => (
          <div key={x.key}>
            <div className={c.chartUnit}>{x.label}</div>
            <FinChart periods={periods} series={[{ ...x, dashed: false }]} height={180} />
          </div>
        ))}
      </div>
      <FinTable periods={periods} series={series} />
      <Notes items={["시가배당률: 배당기준일 무렵 주가 기준 (회사 공시 값)"]} />
    </Section>
  );
}

/* ───────── 동종업계 비교 ───────── */
/** 순위 막대: 값이 큰 순. 이 회사는 진한 색 */
function RankList({ label, items, fmt }: { label: string; items: { id: string; name: string; value: number | null; me: boolean }[]; fmt: (v: number) => string }) {
  const ranked = [...items].sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity));
  const max = Math.max(...ranked.map((r) => Math.abs(r.value ?? 0)), 1);
  return (
    <div className={c.rankList} role="list" aria-label={`${label} 순위`}>
      {ranked.map((r, i) => (
        <div key={r.id} role="listitem" className={c.rankRow} data-me={r.me || undefined}>
          <span className={c.rankNo}>{r.value != null ? i + 1 : ""}</span>
          <span className={c.rankName}>{r.name}</span>
          <span className={c.rankTrack}>{r.value != null && <span className={c.rankBar} data-neg={r.value < 0 || undefined} style={{ width: `${(Math.abs(r.value) / max) * 100}%` }} />}</span>
          <span className={c.rankVal}>{r.value != null ? fmt(r.value) : "-"}</span>
        </div>
      ))}
    </div>
  );
}

type PeerKey = "assets" | "mktcap" | "per" | "pbr" | "roe" | "debt" | "ni4" | "csm" | "csm_new" | "kics";
const PEER_COLS: { key: PeerKey; label: string; fmt: (v: number) => string; ins?: boolean; fin?: boolean; note?: string }[] = [
  { key: "assets", label: "자산총계", fmt: (v) => fmtWon(v), fin: true, note: "최근 사업보고서 (DART)" },
  { key: "mktcap", label: "시가총액", fmt: (v) => fmtWon(v), note: "현재가 × KRX 상장주식수 (보통주, 자기주식 포함·우선주 제외). 장 마감 후 KRX 공식값과 같음" },
  { key: "per", label: "PER", fmt: (v) => `${v.toFixed(1)}배`, note: "현재 주가 ÷ EPS(회사 공시 기본주당이익 최근 4분기 합)" },
  { key: "pbr", label: "PBR", fmt: (v) => `${v.toFixed(2)}배`, note: "주가 ÷ 최근 분기말 주당순자산" },
  { key: "roe", label: "ROE", fmt: (v) => `${v.toFixed(1)}%`, note: "최근 4분기 지배주주 순이익 ÷ 평균 지배주주 자본" },
  { key: "ni4", label: "지배주주 순이익(최근 4분기)", fmt: (v) => fmtWon(v), note: "PER 과 같은 기준. 비지배지분이 없으면 전체 순이익, 있으면 전체 − 비지배지분" },
  { key: "debt", label: "부채비율", fmt: (v) => `${Math.round(v).toLocaleString("ko-KR")}%` },
  { key: "csm", label: "CSM 잔액", fmt: (v) => fmtWon(v), ins: true },
  { key: "csm_new", label: "신계약 CSM(분기)", fmt: (v) => fmtWon(v), ins: true },
  { key: "kics", label: "K-ICS", fmt: (v) => `${v.toFixed(1)}%`, ins: true },
];

/** 고른 토스 섹터의 경쟁사 표 (2026-11-25 사용자 'A'): 처음 섹터가 아니면 받아 온다 (처음 1~2분, 이후 7일 저장본) */
const sectorPeerCache = new Map<string, Promise<Company["peers"]>>();
function useSectorPeers(ticker: string, sector: string | null): { pe: Company["peers"]; loading: boolean } {
  const id = sector ? `${ticker}:${sector}` : "";
  const [got, setGot] = useState<{ id: string; pe: Company["peers"] } | null>(null);
  useEffect(() => {
    if (!sector) return;
    let off = false;
    if (!sectorPeerCache.has(id)) {
      sectorPeerCache.set(id, fetch(`/api/stocks/${ticker}/peers?sector=${encodeURIComponent(sector)}`).then((r) => r.json())
        .catch(() => ({ error: "받지 못함" })));
    }
    sectorPeerCache.get(id)!.then((pe) => { if (!off) setGot({ id, pe }); });
    return () => { off = true; };
  }, [ticker, sector, id]);
  return got?.id === id ? { pe: got.pe, loading: false } : { pe: null, loading: !!sector };
}

function PeerView({ co, tabs, top }: { co: Company; tabs?: ReactNode; top?: ReactNode }) {
  const [pick] = useSectorPick(co.ticker);
  const want = !co.peers?.fin && co.peers?.sector && pick && pick !== co.peers.sector.id ? pick : null;   // 섹터 버튼으로 고른 다른 섹터
  const other = useSectorPeers(co.ticker, want);
  const pe = want ? other.pe : co.peers;
  const px = useLastPrices((pe?.rows ?? []).flatMap((r) => (r.ticker && !r.error ? [r.ticker] : [])));
  const cols = PEER_COLS.filter((x) => (!x.ins || pe?.insurer) && (!x.fin || pe?.fin) && !(pe?.fin && x.key === "debt"));   // 금융회사는 부채비율 뺌 (2026-11-26 사용자)
  if (want && other.loading) {
    return <Section title="경쟁사">{top}<p className={c.empty}>고른 섹터의 경쟁사 재무를 받는 중입니다 (섹터마다 처음 한 번 1~2분, 이후 7일 동안 바로)…</p></Section>;
  }
  if (!pe || pe.error || !pe.rows?.length) {
    return <Section title="경쟁사">{top}<p className={c.empty}>{pe?.error ? "경쟁사 정보를 만들지 못했습니다." : "경쟁사 정보가 아직 없습니다."}</p></Section>;
  }
  if (pe.sector && pe.rows.filter((r) => !r.error && r.ticker !== co.ticker).length === 0) {
    return <Section title="경쟁사">{top}<p className={c.empty}>이 섹터에는 국내 상장 경쟁사가 없습니다 (이 회사뿐).</p></Section>;
  }
  // 시가총액 큰 순, 없으면 아래에서 자산총계 큰 순 (2026-11-26 사용자, 금융과 같게)
  const rows = pe.rows.filter((r) => !r.error).map((r) => withLastPrice(r, px, co.price?.date))
    .sort((a, b) => ((b.mktcap ?? -1) - (a.mktcap ?? -1)) || ((b.assets ?? -1) - (a.assets ?? -1)));
  return (
    <Section title="경쟁사" sub={pe.fin ? undefined
      : pe.sector ? undefined : `같은 업종 코드 ${rows.length}곳 (토스 섹터에 없음)`} right={tabs && <div className={c.filters}>{tabs}</div>}>
      {top}
      <div className={c.tableScroll}>
        <table className={c.finTable}>
          <thead>
            <tr><th className={c.stick}>회사</th><th title="현재가 (토스, 장중이면 지금 가격). 못 받으면 최근 KRX 종가">주가</th><th>등락률</th>
              {cols.map((x) => <th key={x.key} title={x.note}>{x.label}</th>)}<th>기준 분기</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker ?? r.corp_code ?? r.name} className={r.ticker === co.ticker ? c.meRow : undefined}>
                <th className={c.stick} scope="row">{r.name}<small className={c.tick}>{r.ticker ?? "비상장"}</small></th>
                <td>{r.close != null ? `${r.close.toLocaleString("ko-KR")}원` : "-"}</td>
                <td className={r.rate != null ? (r.rate > 0 ? c.pos : r.rate < 0 ? c.neg : undefined) : undefined}>{r.rate != null ? `${r.rate > 0 ? "+" : ""}${r.rate.toFixed(2)}%` : "-"}</td>
                {cols.map((x) => <td key={x.key}>{r[x.key] != null ? x.fmt(r[x.key]!) : "-"}</td>)}
                <td>{r.period ? qLabel(r.period) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/** 경쟁사 표 현재가 (2026-11-26 사용자: PER = 현재 주가 ÷ 공시 EPS, 기업개요와 같은 가격). 종가·등락률·PER·PBR 을 현재가로 다시 계산.
 *  못 받은 종목은 저장된 KRX 종가 그대로 */
const lastPxCache = new Map<string, { at: number; p: Promise<Record<string, { d: string; c: number; prev: number | null }>> }>();
function useLastPrices(tickers: string[]) {
  const key = [...new Set(tickers)].sort().join(",");
  const [got, setGot] = useState<{ key: string; v: Record<string, { d: string; c: number; prev: number | null }> } | null>(null);
  useEffect(() => {
    if (!key) return;
    let off = false;
    const h = lastPxCache.get(key);
    const p = h && Date.now() - h.at < 600_000 ? h.p
      : fetch(`/api/prices?tickers=${key}`).then((r) => r.json()).then((b) => b.prices ?? {}).catch(() => ({}));
    if (!h || h.p !== p) lastPxCache.set(key, { at: Date.now(), p });
    p.then((v) => { if (!off) setGot({ key, v }); });
    return () => { off = true; };
  }, [key]);
  return got?.key === key ? got.v : {};
}
function withLastPrice<T extends PeerRow>(r: T, px: Record<string, { d: string; c: number; prev: number | null }>, krxLast?: string): T {
  const x = r.ticker ? px[r.ticker] : undefined;
  if (!x) return r;
  const listed = r.mktcap && r.close ? r.mktcap / r.close : null;     // KRX 상장주식수 = KRX 시가총액 ÷ KRX 종가 (보통주, 자기주식 포함)
  // 등락 기준 = KRX 공식 전일 종가 (2026-11-26 사용자, 맨 위 시세와 같게). 토스 전일 값은 넥스트레이드 시간외까지 들어가 공식 종가와 다름
  const kd = (r.day ?? "").replace(/\D/g, "");
  // KRX 종가는 가장 최근 KRX 거래일(krxLast = 이 회사 시세 날짜) 값일 때만 기준으로 쓴다 (저장본이 더 오래되면 며칠치 등락이 되므로 토스 전일 값)
  const base = r.close && kd && kd === krxLast && kd < x.d ? r.close : r.close && kd === x.d && r.rate != null ? r.close / (1 + r.rate / 100) : x.prev;
  return { ...r, close: x.c, rate: base ? ((x.c - base) / base) * 100 : r.rate, mktcap: listed ? x.c * listed : r.mktcap,
    per: r.eps != null && r.eps > 0 ? x.c / r.eps : null, pbr: r.bps != null && r.bps > 0 ? x.c / r.bps : null };
}

/** 금융회사 경쟁사 (2026-11-26 사용자): 탭은 회사 범위만 ('주요 경쟁사' 자산 상위 / '업계 전체' 금감원 업권 모든 회사),
 *  칸은 두 탭 같게 = 주가·재무(DART·KRX) + 금감원 지표. 두 자료의 회사는 이름(→ 상장 종목코드)으로 짝을 맞춘다 */
type FinU = { id: string; name: string; tick: string; me: boolean; p?: PeerRow; f?: Sector["peers"][number] };
const fmtUnit = (unit: string) => (v: number) => (unit === "원" || unit === "잔액" ? fmtWon(v) : unit === "수" ? Math.round(v).toLocaleString("ko-KR") : `${v.toFixed(2)}%`);
function FinPeers({ co, s }: { co: Company; s: Sector | null }) {
  const pe = co.peers;
  const [tab, setTab] = useState<"main" | "all">("main");
  const fps: Sector["peers"] = s?.peers ?? [];
  const fKey = fps.map((r) => r.name).join("|");
  const [fMap, setFMap] = useState<Record<string, string | null>>({});
  useEffect(() => {
    if (!fKey) return;
    let off = false;
    fetch(`/api/peers/listed?names=${encodeURIComponent(fKey)}`).then((r) => r.json())
      .then((b) => { if (!off) setFMap(b.map ?? {}); }).catch(() => {});
    return () => { off = true; };
  }, [fKey]);
  const px = useLastPrices((pe?.rows ?? []).flatMap((r) => (r.ticker && !r.error ? [r.ticker] : [])));
  if (!pe || pe.error || !pe.rows?.length) {
    return <Section title="경쟁사"><p className={c.empty}>{pe?.error ? "경쟁사 정보를 만들지 못했습니다." : "경쟁사 정보가 아직 없습니다."}</p></Section>;
  }
  const nm = (n: string) => n.replace(/\s|주식회사|\(주\)|회사$/g, "").replace("금융지주", "지주");
  const rows = pe.rows.filter((r) => !r.error).map((r) => withLastPrice(r, px, co.price?.date));
  const same = (f: Sector["peers"][number], r: (typeof rows)[number]) => (!!r.ticker && fMap[f.name] === r.ticker) || nm(f.name) === nm(r.name);
  const cols = PEER_COLS.filter((x) => (!x.ins || pe.insurer) && x.key !== "debt");          // 부채비율 뺌 (2026-11-26 사용자)
  const fm = (s?.metrics ?? []).filter((m) => fps.some((r) => r[m.key] != null));
  const main: FinU[] = rows.map((r) => ({ id: r.ticker ?? r.corp_code ?? r.name, name: r.name, tick: r.ticker ?? "비상장", me: r.ticker === co.ticker,
    p: r, f: fps.find((f) => same(f, r)) }));
  const all: FinU[] = fps.map((f) => { const r = rows.find((x) => same(f, x));
    return { id: f.finance_cd, name: r?.name ?? f.name, tick: r?.ticker ?? fMap[f.name] ?? "비상장", me: f.me || (!!r && r.ticker === co.ticker), p: r, f }; });
  // 시가총액 큰 순 (2026-11-26 사용자). 시가총액 없는 회사(비상장 등)는 아래에서 자산총계 큰 순 → 두 탭 순서가 같다
  const list = [...(tab === "all" && fps.length ? all : main)]
    .sort((a, b) => ((b.p?.mktcap ?? -1) - (a.p?.mktcap ?? -1)) || ((b.p?.assets ?? -1) - (a.p?.assets ?? -1)));
  const tabs = fps.length ? (
    <div className={c.segment} role="group" aria-label="경쟁사 범위">
      <button type="button" aria-pressed={tab === "main"} onClick={() => setTab("main")}>주요 경쟁사 {main.length}곳</button>
      <button type="button" aria-pressed={tab === "all"} onClick={() => setTab("all")}>업계 전체 {all.length}곳</button>
    </div>
  ) : null;
  return (
    <Section title="경쟁사" right={tabs && <div className={c.filters}>{tabs}</div>}>
      <div className={c.tableScroll}>
        <table className={c.finTable}>
          <thead>
            {fm.length > 0 && <tr><th className={c.stick} /><th colSpan={cols.length + 3}>주가·재무 (DART·KRX)</th><th colSpan={fm.length}>금감원 지표 (개별)</th></tr>}
            <tr><th className={c.stick}>회사</th><th title="현재가 (토스, 장중이면 지금 가격). 못 받으면 최근 KRX 종가">주가</th><th>등락률</th>
              {cols.map((x) => <th key={x.key} title={x.note}>{x.label}</th>)}<th>기준 분기</th>
              {fm.map((m) => <th key={m.key} title={m.desc}>{m.label}</th>)}</tr>
          </thead>
          <tbody>
            {list.map((u) => (
              <tr key={u.id} className={u.me ? c.meRow : undefined}>
                <th className={c.stick} scope="row">{u.name}<small className={c.tick}>{u.tick}</small></th>
                <td>{u.p?.close != null ? `${u.p.close.toLocaleString("ko-KR")}원` : "-"}</td>
                <td className={u.p?.rate != null ? (u.p.rate > 0 ? c.pos : u.p.rate < 0 ? c.neg : undefined) : undefined}>{u.p?.rate != null ? `${u.p.rate > 0 ? "+" : ""}${u.p.rate.toFixed(2)}%` : "-"}</td>
                {cols.map((x) => { const v = u.p?.[x.key]; return <td key={x.key}>{v != null ? x.fmt(v) : "-"}</td>; })}
                <td>{u.p?.period ? qLabel(u.p.period) : "-"}</td>
                {fm.map((m) => { const v = u.f?.[m.key] as number | null | undefined; return <td key={m.key}>{v != null ? fmtUnit(m.unit)(v) : "-"}</td>; })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {fm.length > 0 && <Notes items={[`금감원 지표: 회사 개별(별도) 기준${s?.month ? `, ${qLabel(`${s.month.slice(0, 4)}.${s.month.slice(4)}`)} 기준` : ""}`]} />}
    </Section>
  );
}

/** 업계 전체 (금감원, 비상장 포함): 업종 지표를 회사별로 */
function SectorPeers({ s, tabs }: { s: Sector; tabs?: ReactNode }) {
  const [key, setKey] = useState(s.metrics[0]?.key ?? "");
  if (!s.peers?.some((r) => s.metrics.some((x) => r[x.key] != null))) return null;   // 업계 값이 없는 업권(자산운용: 회사 이름만)
  const m = s.metrics.find((x) => x.key === key) ?? s.metrics[0];
  const fmtOf = (unit: string) => (v: number) => (unit === "원" || unit === "잔액" ? fmtWon(v) : `${v.toFixed(2)}%`);
  return (
    <Section title={tabs ? "경쟁사" : `${s.sector} 업계 전체`} sub={tabs ? undefined : `비상장 포함 ${s.peers.length}곳, ${s.month ? qLabel(`${s.month.slice(0, 4)}.${s.month.slice(4)}`) : ""} 기준`} right={
      <div className={c.filters}>{tabs}<Select label="비교 지표" value={key} onChange={setKey} options={s.metrics.map((x) => [x.key, x.label])} /></div>}>
      <RankList label={m.label} items={s.peers.map((r) => ({ id: r.finance_cd, name: r.name, value: (r[m.key] as number | null | undefined) ?? null, me: r.me }))} fmt={fmtOf(m.unit)} />
      <div className={c.tableScroll}>
        <table className={c.finTable}>
          <thead><tr><th className={c.stick}>회사</th>{s.metrics.map((x) => <th key={x.key}>{x.label}</th>)}</tr></thead>
          <tbody>
            {s.peers.map((r) => (
              <tr key={r.finance_cd} className={r.me ? c.meRow : undefined}>
                <th className={c.stick} scope="row" title={r.full_name}>{r.name}</th>
                {s.metrics.map((x) => { const v = r[x.key] as number | null | undefined; return <td key={x.key}>{v != null ? fmtOf(x.unit)(v) : "-"}</td>; })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Notes items={["회사 개별(별도) 기준"]} />
    </Section>
  );
}

/** 동종사 추이 비교 (2026-11-22): 동종사를 체크하는 순간 그 회사 재무 추이를 받아(무료, 처음 1분 안팎·7일 저장) 같은 지표를 겹쳐 그린다.
 *  지표 값은 재무 화면과 같은 계산(catalog)을 회사마다 적용한 것. 기간 칸은 이 회사 기준 */
const PEER_COLORS = ["#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#eda100"];   // 색약 검사 통과 순서. 이 회사는 네이비 굵은 선
const PEER_MAX = 5;
/** 별도 기준 비교 (2026-11-24, 사용자): 상장 비교 회사는 그 회사의 별도 재무로 바꾼다. 금감원 회사는 원래 개별 기준, DART 비상장사는 연결 그대로 */
const sepCache = new Map<string, Promise<Company["statements"] | null>>();
function getSeparate(ticker: string) {
  if (!sepCache.has(ticker)) {
    sepCache.set(ticker, fetch(`/api/stocks/${ticker}/separate`).then((r) => r.json()).then((j) => j?.statements ?? null).catch(() => null));
  }
  return sepCache.get(ticker)!;
}
function useSeparatePeers(ready: PeerPick[], on: boolean): { ready: PeerPick[]; waiting: number } {
  const [got, setGot] = useState<Record<string, Company["statements"] | null>>({});
  const ids = ready.map((x) => x.id).join();
  useEffect(() => {
    if (!on) return;
    let off = false;
    ready.forEach((x) => {
      if (x.id.startsWith("T:") && "ticker" in x.d) getSeparate(x.d.ticker).then((st) => { if (!off) setGot((g) => ({ ...g, [x.id]: st })); });
    });
    return () => { off = true; };
  }, [on, ids]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!on) return { ready, waiting: 0 };
  const out = ready.flatMap((x): PeerPick[] => {
    if (!x.id.startsWith("T:") || !("ticker" in x.d)) return [x];
    const st = got[x.id];
    if (st === undefined) return [];
    return [st ? { ...x, d: { ...x.d, fs: st.fs, annual: st.annual, quarter: st.quarter, insurance: null } } : x];
  });
  return { ready: out, waiting: ready.length - out.length };
}

const asCompany = (p: PeerSeries): Company => ({
  statements: { fs: p.fs ?? null, annual: p.annual, quarter: p.quarter, tables: {}, sources: [] },
  insurance: p.insurance ?? null, sector: p.sector ?? null, financial: p.financial, valuation: p.valuation ?? null,
}) as unknown as Company;

function groupsOf(co: Company): Group[] {
  if (finPart(co)) return ["profit", "growth", "stability"];          // 금융회사: 매출·마진·유동성·재고·설비투자는 맞지 않는 잣대라 뺀다
  const has = (k: FinKey) => co.statements.quarter.some((p) => p[k] != null);
  const order: Group[] = ["sales", "profit", "growth", "stability", "liquidity", "inventory", "capex"];
  return order.filter((g) =>
    g === "liquidity" ? has("cur_assets") && has("cur_liab")
      : g === "sales" ? has("revenue")
          : g === "inventory" ? has("inventory") && has("cogs")
            : g === "capex" ? has("capex") && has("revenue") && !co.financial
              : true);
}
function byPeriod(co: Company, mode: Mode, years: number, key: string): Map<string, { v: number | null; m: "derived" | "missing" | null }> {
  const list = periodList(co.statements, mode);
  const keys = sliceRange(list, mode, years).map((p) => p.period);
  const m = catalog(co, groupsOf(co), mode, years).find((x) => x.key === key);
  return new Map(keys.map((k, i) => [k, { v: m?.values[i] ?? null, m: m?.marks?.[i] ?? null }]));
}

type PeerData = PeerSeries | FisisPeer | "loading" | { error: string };
const isReady = (d?: PeerData): d is PeerSeries | FisisPeer => !!d && d !== "loading" && ("ticker" in d || "finance_cd" in d);
/** 금감원 자료로 비교할 수 있는 지표: 업종 지표, 보험사 K-ICS·CSM 잔액 (회사 개별 기준) */
const fisisKey = (key: string) => (key.startsWith("fisis_") ? key.slice(6) : key === "kics" || key === "csm" ? key : null);
const ME_COLOR = "#1e3a5f";
const peerCache = new Map<string, PeerSeries | FisisPeer>();         // 메뉴를 옮겨도 다시 받지 않게 (파이썬도 7일 저장)
export interface PeerPick { id: string; slot: number; d: PeerSeries | FisisPeer }

/** 동종사 고르기 상태: 체크하는 순간 그 회사 자료를 받는다. id: "T:종목코드"(상장, DART) / "F:금감원 회사 코드"(업계 전체, 비상장 포함) */
const PEER_UNLISTED = false;                                // 같은 업종 코드 비상장사 후보: 전면 개편 1차에서 끔 (경쟁사 = 토스 섹터)
function usePeers(co: Company) {
  const [extra, setExtra] = useState<{ ticker: string; name: string }[]>([]);     // 직접 추가한 상장사 (2026-11-23)
  const [dartUnlisted, setDartUnlisted] = useState<{ corp_code: string; name: string }[]>([]);
  const induty = String(co.info?.induty_code ?? "");
  useEffect(() => {                                        // 금융사가 아니면 같은 업종 비상장 정기보고서 제출사 (DART)
    if (!PEER_UNLISTED || (co.sector && !co.sector.error)) return;
    let off = false;
    fetch(`/api/peers/unlisted?induty=${encodeURIComponent(induty)}`).then((r) => r.json())
      .then((b) => { if (!off) setDartUnlisted(Array.isArray(b.items) ? b.items : []); }).catch(() => {});
    return () => { off = true; };
  }, [induty, co.sector]);
  // 비교 회사 후보 = 고른 토스 섹터 종목, 시가총액 상위 15곳 (전면 개편 1차, 2026-11-25 사용자). 토스 섹터에 없으면 경쟁사 표 회사
  const ts = useCurrentSector(co.ticker, co.peers?.sector?.id);
  const finRows = co.peers?.fin ? (co.peers.rows ?? []).filter((r) => !r.error) : null;   // 금융: 금감원 업권 회사 (상장은 T:, 비상장은 D:)
  const tossPeers = finRows ? finRows.filter((r) => r.ticker && r.ticker !== co.ticker).map((r) => ({ ticker: r.ticker!, name: r.name, sub: `금감원 ${co.peers!.fin!.name}` }))
    : ts ? ts.members.filter((m) => m.ticker !== co.ticker).slice(0, 15).map((m) => ({ ticker: m.ticker, name: m.name, sub: `토스 ${ts.name}` }))
    : (co.peers?.rows ?? []).filter((r) => !r.error && r.ticker && r.ticker !== co.ticker).map((r) => ({ ticker: r.ticker!, name: r.name }));
  const listed: { ticker: string; name: string; sub?: string }[] = [...tossPeers,
    ...extra.filter((x) => x.ticker !== co.ticker && !tossPeers.some((r) => r.ticker === x.ticker))];
  const sec = co.sector && !co.sector.error ? co.sector : null;
  const unlisted = (sec?.peers ?? []).filter((r) => !r.me);
  // 금감원 회사 이름 → 상장 종목코드 (2026-11-26 사용자 B안): 찾으면 상장 종목으로, 못 찾으면 '비상장'
  const fKey = unlisted.map((r) => r.name).join("|");
  const [fMap, setFMap] = useState<Record<string, string | null>>({});
  useEffect(() => {
    if (!fKey) return;
    let off = false;
    fetch(`/api/peers/listed?names=${encodeURIComponent(fKey)}`).then((r) => r.json())
      .then((b) => { if (!off) setFMap(b.map ?? {}); }).catch(() => {});
    return () => { off = true; };
  }, [fKey]);
  const finUnlisted = (finRows ?? []).filter((r) => !r.ticker && r.corp_code).map((r) => ({ corp_code: r.corp_code!, name: r.name }));
  const [checked, setChecked] = useState<[string, number][]>([]);
  const [data, setData] = useState<Record<string, PeerData>>(() => Object.fromEntries(peerCache));
  const isOn = (id: string) => checked.some(([x]) => x === id);
  const toggle = (id: string) => {
    if (isOn(id)) { setChecked(checked.filter(([x]) => x !== id)); return; }
    if (checked.length >= PEER_MAX) return;
    const used = new Set(checked.map(([, k]) => k));
    setChecked([...checked, [id, PEER_COLORS.findIndex((_, k) => !used.has(k))]]);
    const have = data[id];
    if (have === "loading" || isReady(have)) return;                    // 받는 중이거나 이미 받은 회사 (실패한 회사는 다시 받는다)
    setData((d) => ({ ...d, [id]: "loading" }));
    const q = id.startsWith("T:") ? `ticker=${id.slice(2)}` : id.startsWith("D:") ? `corp=${id.slice(2)}` : `fisis=${sec?.part}:${id.slice(2)}`;
    const once = () => fetch(`/api/peer?${q}`).then((r) => r.json())
      .then((b) => (b.peer && !b.peer.error ? b.peer : Promise.reject(new Error(b.peer?.error ?? b.error ?? "받지 못함"))));
    once().catch(() => once())                                          // 실패하면 한 번 더 (동시에 받을 때 잠깐 실패할 수 있다)
      .then((peer) => { peerCache.set(id, peer); setData((d) => ({ ...d, [id]: peer })); })
      .catch((e: Error) => setData((d) => ({ ...d, [id]: { error: e.message || "받지 못함" } })));
  };
  const ready: PeerPick[] = checked.flatMap(([id, slot]) => { const d = data[id]; return isReady(d) ? [{ id, slot, d }] : []; });
  const status = (id: string, metricKeys: string[] = []) => {
    const d = data[id];
    if (!isOn(id) || !d) return null;
    if (d === "loading") return <em>받는 중</em>;
    if (!isReady(d)) return <em className={c.warnCell}>{d.error}</em>;
    if (!("ticker" in d) && !d.dart && !d.fs_quarter?.length && metricKeys.length && !metricKeys.some(fisisKey)) return <em>이 지표 없음</em>;
    return null;
  };
  const add = (x: { ticker: string; name: string }) => {          // 검색해서 고른 회사: 목록에 넣고 바로 체크
    if (x.ticker === co.ticker) return;
    if (!listed.some((r) => r.ticker === x.ticker)) setExtra((e) => [...e, x]);
    if (!isOn(`T:${x.ticker}`)) toggle(`T:${x.ticker}`);
  };
  return { listed, unlisted, fMap, dartUnlisted: [...finUnlisted, ...dartUnlisted], sec, checked, isOn, toggle, ready, status, add, any: true };
}
type Peers = ReturnType<typeof usePeers>;

/** 동종사 체크 목록. row: 가로 줄(추이 비교), pane: 세로 목록(지표 탐색 왼쪽 칸, 업계 전체는 접어 둔다) */
function PeerPicker({ co, p, me, metricKeys = [], pane }: { co: Company; p: Peers; me?: { on: boolean; toggle: () => void }; metricKeys?: string[]; pane?: boolean }) {
  const [openAll, setOpenAll] = useState(false);
  const item = (id: string, name: string, sub?: string) => {
    const slot = p.checked.find(([x]) => x === id)?.[1];
    return (
      <label key={id} className={pane ? c.peerPaneItem : c.peerItem} data-on={p.isOn(id) || undefined}>
        <input type="checkbox" checked={p.isOn(id)} disabled={!p.isOn(id) && p.checked.length >= PEER_MAX} onChange={() => p.toggle(id)}
               style={slot != null ? { accentColor: PEER_COLORS[slot] } : undefined} />
        <span>{name}</span>{sub && <small>{sub}</small>}{p.status(id, metricKeys)}
      </label>
    );
  };
  const meItem = me && (
    <label className={`${pane ? c.peerPaneItem : c.peerItem} ${c.peerMe}`} data-on={me.on || undefined}>
      <input type="checkbox" checked={me.on} onChange={me.toggle} style={{ accentColor: ME_COLOR }} /><span>{co.name}</span><small>{co.ticker}</small>
    </label>
  );
  // 상장·비상장을 나누지 않고 한 목록 (2026-11-24, 사용자). 상장사 먼저, 이름이 같은 회사는 한 번만
  const nm = (n: string) => n.replace(/\s|주식회사|\(주\)|회사$/g, "").replace("금융지주", "지주");   // '신한금융지주회사' = '신한지주'
  // 상장 = 종목코드만, 비상장 = '비상장'만 (2026-11-26 사용자). 금감원 회사는 이름으로 찾은 종목코드로 같은 회사·자기 자신을 뺀다
  const names = new Set([co.name, ...p.listed.map((r) => r.name)].map(nm));
  const tickers = new Set([co.ticker, ...p.listed.map((r) => r.ticker)]);
  const fRows: [string, string, string][] = [];
  for (const r of p.sec ? p.unlisted : []) {
    const t = p.fMap[r.name];
    if (t ? tickers.has(t) : names.has(nm(r.name))) continue;
    if (t) tickers.add(t);
    names.add(nm(r.name));
    fRows.push(t ? [`T:${t}`, r.name, t] : [`F:${r.finance_cd}`, r.name, "비상장"]);
  }
  const all: [string, string, string][] = [
    ...p.listed.map((r) => [`T:${r.ticker}`, r.name, r.ticker] as [string, string, string]),
    ...fRows.filter(([id]) => id.startsWith("T:")),
    ...fRows.filter(([id]) => id.startsWith("F:")),
    ...p.dartUnlisted.filter((r) => !names.has(nm(r.name))).map((r) => [`D:${r.corp_code}`, r.name, "비상장"] as [string, string, string]),
  ];
  const open = openAll;
  const list = (
    <>
      <button type="button" className={c.peerPaneMore} aria-expanded={open} onClick={() => setOpenAll((o) => !o)}>
        비교 회사 후보 {all.length}곳
      </button>
      {(open ? all : all.filter(([id]) => p.isOn(id))).map(([id, name, sub]) => item(id, name, sub))}
    </>
  );
  if (pane) {
    return (
      <div className={c.peerPane}>
        <div className={c.peerPaneHead}><span>비교 회사</span><small>{p.checked.length}/{PEER_MAX}</small></div>
        <PeerAdd onPick={p.add} />
        {list}
      </div>
    );
  }
  return (
    <div className={c.peerRows}>
      <div className={c.peerPick} role="group" aria-label="비교 회사">
        <span className={c.peerHead}>비교 회사</span>
        {meItem}
        <PeerAdd onPick={p.add} inline />
        {list}
      </div>
    </div>
  );
}

/** 동종사 한 곳의 한 지표 선 (재무 화면과 같은 계산을 그 회사 자료에 적용. 금감원 회사는 업종 지표·K-ICS·CSM 잔액만) */
function peerLine(x: PeerPick, m: { key: string; fmt: Series["fmt"]; kind?: Series["kind"]; src?: string; calc?: boolean }, mode: Mode, years: number,
                  rows: FinPeriod[]): Series {
  const color = PEER_COLORS[x.slot];
  if ("ticker" in x.d) {
    const v = byPeriod(asCompany(x.d), mode, years, m.key);
    return { key: x.id, label: x.d.name, color, kind: m.kind ?? "line", fmt: m.fmt, src: m.src, calc: m.calc,
      values: rows.map((p) => v.get(p.period)?.v ?? null), marks: rows.map((p) => v.get(p.period)?.m ?? null) };
  }
  const d = x.d, fk = fisisKey(m.key);
  // 금감원 업종 지표가 아닌 것: DART 정기보고서가 있으면 그 재무제표, 없으면 금감원 재무 요약(자산·자본·손익, 회사 개별 기준)
  const fsData = !fk ? (d.dart ?? (d.fs_quarter?.length
    ? { made_at: d.made_at, ticker: d.finance_cd, name: d.name, financial: true, fs: "FISIS", annual: d.fs_annual ?? [], quarter: d.fs_quarter } as PeerSeries : null)) : null;
  if (fsData) {
    const v = byPeriod(asCompany(fsData), mode, years, m.key);
    return { key: x.id, label: d.name, color, kind: m.kind ?? "line", fmt: m.fmt, src: d.dart ? "DART" : "FISIS", calc: m.calc,
      values: rows.map((p) => v.get(p.period)?.v ?? null), marks: rows.map((p) => v.get(p.period)?.m ?? null) };
  }
  const won = m.fmt === "won" && m.key.startsWith("fisis_");          // 손익 금액: 연간 = 12월 누적, 반기 = 누적 차감
  return { key: x.id, label: d.name, color, kind: m.kind ?? "line", fmt: m.fmt, src: "FISIS",
    values: rows.map((p) => (fk ? fisisValue(d.series as Record<string, unknown>[], p.period, fk, won, mode) : null)) };
}

/** 비교 회사 직접 추가: 회사명·종목코드로 상장사 검색 */
function PeerAdd({ onPick, inline }: { onPick: (x: { ticker: string; name: string }) => void; inline?: boolean }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<{ ticker: string; name: string }[]>([]);
  useEffect(() => {
    const t = q.trim();
    if (!t) return;
    let off = false;
    const timer = setTimeout(() => {
      fetch(`/api/stocks/search?q=${encodeURIComponent(t)}`).then((r) => r.json())
        .then((b) => { if (!off) setItems(Array.isArray(b.items) ? b.items.slice(0, 8) : []); }).catch(() => {});
    }, 180);
    return () => { off = true; clearTimeout(timer); };
  }, [q]);
  const shown = q.trim() ? items : [];
  return (
    <div className={inline ? c.peerAddInline : c.peerAdd}>
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="+ 회사 추가 (회사명, 종목코드)" aria-label="비교 회사 추가"
             onKeyDown={(e) => { if (e.key === "Enter" && shown[0]) { onPick(shown[0]); setQ(""); } }} />
      {shown.length > 0 && (
        <ul className={c.peerAddList} role="listbox">
          {shown.map((x) => (
            <li key={x.ticker}><button type="button" onClick={() => { onPick(x); setQ(""); }}>{x.name}<small>{x.ticker}</small></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PeerTrend({ co }: { co: Company }) {
  const p = usePeers(co);
  useFormulas();
  const [showMe, setShowMe] = useState(true);                         // 이 회사 선도 끄고 켤 수 있다
  const [mode, setMode] = useState<Mode>("quarter");
  const [daily, setDaily] = useState(false);
  const [years, setYears] = useState(3);
  const mine0 = catalog(co, groupsOf(co), mode, years).filter((m) => !m.cat.endsWith(ACCOUNT_CAT));
  const mine = daily ? mine0.filter((m) => DAILY_KEYS.has(m.key)) : mine0;       // 일간: 일별 지표만
  const [key, setKey] = useState(() => (isInsurer(co) ? "csm" : co.statements.quarter.some((x) => x.revenue != null) ? "revenue" : "ni"));
  const metric = mine.find((m) => m.key === key) ?? mine[0];
  if (!p.any || !metric) return null;

  const calc = (md: Mode, yr: number) => {                 // 화면과 엑셀 창(다른 기간·단위)이 같이 쓴다
    const rows = sliceRange(periodList(co.statements, md), md, yr);
    const own = byPeriod(co, md, yr, metric.key);
    const series: Series[] = showMe ? [{ key: co.ticker, label: co.name, color: ME_COLOR, kind: "line", fmt: metric.fmt, strong: true,
      values: rows.map((x) => own.get(x.period)?.v ?? null), marks: rows.map((x) => own.get(x.period)?.m ?? null), src: metric.src, calc: metric.calc }] : [];
    p.ready.forEach((x) => series.push(peerLine(x, { ...metric, kind: "line" }, md, yr, rows)));
    return { rows, series };
  };
  const { rows, series } = calc(mode, years);
  const periods = rows.map((x) => label(x, mode));
  return (
    <Section title="추이 비교" sub={`최대 ${PEER_MAX}곳`} right={
      <div className={c.filters}>
        <Select label="비교 지표" value={metric.key} onChange={setKey} options={mine.map((m) => [m.key, `${m.label} (${m.cat})`])} />
        <Segment label="기간" value={years} onChange={setYears} options={FS_RANGE} />
        <Segment<ModeD> label="연간·반기·분기·일간" value={daily ? "daily" : mode} onChange={(v) => { if (v === "daily") setDaily(true); else { setDaily(false); setMode(v); } }} options={MODE_OPTS} />
        {!daily && <ExportButton choose={{ mode, years }} make={(o) => { const md = o?.mode ?? mode, x2 = calc(md, o?.years ?? years); return {
          title: `${co.name} 동종사 비교 ${metric.label}`, sheet: "동종사 비교", ticker: co.ticker,
          basis: `${MODE_NAME[md]}. ${p.checked.some(([id]) => id.startsWith("F:")) ? "금감원 자료는 회사 개별(별도) 기준" : `${co.statements.fs ?? ""} 기준`}`,
          periods: x2.rows.map((x) => x.period), rows: toRows(x2.series, co.ticker),
          notes: metric.note ? [`${metric.label}: ${metric.note}`] : [],
          sources: ["OpenDART", ...(x2.series.some((x) => x.src?.includes("FISIS")) ? ["금융감독원 금융통계정보시스템"] : [])],
        }; }} />}
      </div>}>
      <PeerPicker co={co} p={p} me={{ on: showMe, toggle: () => setShowMe((v) => !v) }} metricKeys={[metric.key]} />
      {series.length === 0 ? <p className={c.empty}>비교할 회사를 고르세요.</p> : <>
        {daily
          ? <DailyMetrics keys={[metric.key]} subjects={subjectsOf(co, p.ready, showMe)} from={fromOf(rows[0]?.period, mode)} name={co.name} />
          : <><FinChart key={`${metric.key}|${series.map((x) => x.key).join()}`} periods={periods} series={series} height={260} />
            <FinTable periods={periods} series={series} focus={co.ticker} /></>}
      </>}
      <Notes items={[
        metric.note && `${metric.label}: ${metric.note}`,
        p.checked.some(([id]) => id.startsWith("F:")) && "업계 전체 회사: 사업 지표·K-ICS·CSM 잔액은 금감원(회사 개별 기준), 그 밖의 지표는 DART 정기보고서를 내는 회사만",
        mode === "quarter" && "*: 계산 값 (12월 = 연간 − 3분기 누적)",
      ]} />
    </Section>
  );
}

/* ───────── 차트 만들기 (2026-11-23): 지표·회사·함께 보기 조합을 카드로 저장. 숫자는 저장하지 않고 열 때마다 공시 자료로 다시 그린다 ───────── */
interface Card { id: string; title: string; keys: string[]; peers: string[]; together: boolean; mode: Mode; years: number; common?: boolean; daily?: boolean }
function useCards(url: string) {
  const [list, setList] = useState<Card[]>([]);
  useEffect(() => {                                         // 리포트(또는 검색한 종목)가 바뀌면 다시 읽는다
    let off = false;
    fetch(url).then((r) => r.json())
      .then((b) => { if (!off) setList(Array.isArray(b.charts) ? b.charts : []); }).catch(() => { if (!off) setList([]); });
    return () => { off = true; };
  }, [url]);
  const save = (next: Card[]) => {
    setList(next);
    fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ charts: next }) }).catch(() => {});
  };
  return {
    list,
    add: (c0: Omit<Card, "id">) => save([...list, { ...c0, id: `c${Date.now().toString(36)}` }]),
    remove: (id: string) => save(list.filter((x) => x.id !== id)),
    rename: (id: string, title: string) => save(list.map((x) => (x.id === id ? { ...x, title } : x))),
  };
}

/** 저장한 카드의 동종사 자료: 받아 둔 것은 그대로, 없는 것만 받는다 */
function usePeerData(ids: string[], co: Company): { ready: PeerPick[]; waiting: number } {
  const sec = co.sector && !co.sector.error ? co.sector : null;
  const [data, setData] = useState<Record<string, PeerSeries | FisisPeer | { error: string }>>(() => Object.fromEntries(peerCache));
  const asked = useRef(new Set<string>());
  const key = ids.join();
  useEffect(() => {                                         // 아직 없는 회사만 받는다
    const need = key.split(",").filter((id) => id && !peerCache.has(id) && !asked.current.has(id));
    need.forEach((id) => {
      asked.current.add(id);
      const q = id.startsWith("T:") ? `ticker=${id.slice(2)}` : id.startsWith("D:") ? `corp=${id.slice(2)}` : `fisis=${sec?.part}:${id.slice(2)}`;
      fetch(`/api/peer?${q}`).then((r) => r.json()).then((b) => {
        const ok = b.peer && !b.peer.error;
        if (ok) peerCache.set(id, b.peer);
        setData((d) => ({ ...d, [id]: ok ? b.peer : { error: "받지 못함" } }));
      }).catch(() => setData((d) => ({ ...d, [id]: { error: "받지 못함" } })));
    });
  }, [key, sec?.part]);
  const ready = ids.flatMap((id, slot) => { const d = data[id]; return d && isReady(d) ? [{ id, slot, d }] : []; });
  return { ready, waiting: ids.filter((id) => !data[id]).length };
}

const CARD_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#eda100"];
function ChartCard({ co, card, groups, onRemove, onRename }: {
  co: Company; card: Card; groups: Group[]; onRemove: () => void; onRename: (t: string) => void;
}) {
  const { ready, waiting } = usePeerData(card.peers, co);
  useFormulas();
  const comparing = ready.length > 0;
  const build = (md: Mode, yr: number) => {               // 화면과 엑셀 창(다른 기간·단위)이 같이 쓴다
    const rows = sliceRange(periodList(co.statements, md), md, yr);
    const all = catalog(co, groups, md, yr);
    const ms = card.keys.map((k) => all.find((m) => m.key === k)).filter((m): m is Metric => !!m);
    // 회사 비교: 이 회사 네이비 굵은 선 + 동종사 색, 두 번째 지표는 점선. 아니면 지표마다 색
    const lines: Series[] = comparing
      ? ms.flatMap((m, k) => [
          { ...m, key: `${m.key}|me`, label: `${co.name} ${m.label}`, color: ME_COLOR, strong: true, dashed: card.together && k === 1 && m.kind === "line" },
          ...ready.map((x) => ({ ...peerLine(x, m, md, yr, rows), key: `${m.key}|${x.id}`, label: `${x.d.name} ${m.label}`,
            dashed: card.together && k === 1 && m.kind === "line" })),
        ])
      : ms.map((m, k) => ({ ...m, color: CARD_PALETTE[k % CARD_PALETTE.length] }));
    return { rows, ms, lines };
  };
  const { rows, ms, lines } = build(card.mode, card.years);
  const periods = rows.map((p) => label(p, card.mode));
  const units = new Set(lines.map((x) => x.fmt)).size;
  const joined = card.together && units <= 2;
  const info = `${card.common ? "모든 회사, " : ""}${card.daily ? "일간" : MODE_NAME[card.mode]} ${card.years}년${card.peers.length ? `, 비교 ${card.peers.length}곳` : ""}${joined ? ", 함께 보기" : ""}`;
  return (
    <div className={c.card}>
      <div className={c.cardHead}>
        <input className={c.cardTitle} defaultValue={card.title} aria-label="차트 이름" onBlur={(e) => { const t = e.target.value.trim(); if (t && t !== card.title) onRename(t); }} />
        <small>{info}{waiting ? ", 비교 회사 받는 중" : ""}</small>
        <span className={c.cardActions}>
          {!card.daily && <ExportButton choose={{ mode: card.mode, years: card.years }} make={(o) => { const md = o?.mode ?? card.mode, b = build(md, o?.years ?? card.years); return {
            title: `${co.name} ${card.title}`, sheet: "차트", ticker: co.ticker, together: joined,
            basis: `${co.statements.fs ?? ""} 기준, ${MODE_NAME[md]}`,
            periods: b.rows.map((p) => p.period),
            rows: b.lines.map((x) => ({ label: x.label, legend: x.label, group: comparing && !joined ? x.key.split("|")[0] : undefined, fmt: x.fmt, kind: x.kind,
              values: x.values, calc: x.calc, src: x.src, strong: !!x.strong || !comparing, dashed: x.dashed })),
            notes: b.ms.filter((m) => m.note).map((m) => `${m.label}: ${m.note}`),
            sources: [...new Set([...new Set(b.lines.flatMap((x) => (x.src ?? "DART").split("·")))].map((x) => (x === "DART" ? "OpenDART" : x.startsWith("FISIS") ? "금융감독원 금융통계정보시스템" : x)))],
          }; }} />}
          <button type="button" className={c.cardRemove} onClick={onRemove} aria-label={`${card.title} 차트 지우기`}>지우기</button>
        </span>
      </div>
      {ms.length === 0 ? <p className={c.empty}>{card.common ? "이 회사에 없는 지표입니다." : "이 기간에 이 지표가 없습니다."}</p>
        : card.daily
          ? <DailyMetrics keys={ms.map((m) => m.key).filter((k) => DAILY_KEYS.has(k))} subjects={subjectsOf(co, ready)} from={fromOf(rows[0]?.period, card.mode)} name={co.name} />
        : joined || !comparing ? (joined ? <FinChart key={lines.map((x) => x.key).join()} periods={periods} series={lines} height={240} />
          : <FinCharts periods={periods} series={lines} height={220} />)
        : <div className={c.cmpGrid}>{ms.map((m) => {
            const ls = lines.filter((x) => x.key.startsWith(`${m.key}|`) && x.values.some((v) => v != null));
            return <div key={m.key}><div className={c.chartUnit}>{m.label}</div><FinChart key={ls.map((x) => x.key).join()} periods={periods} series={ls} height={200} /></div>;
          })}</div>}
    </div>
  );
}

/* ───────── 탭 전체 ───────── */
export function CompanyView({ reportId, company, refreshUrl, menu, onCompany }: {
  reportId?: string; company: Company | null | undefined; refreshUrl: string; menu: CompanyMenu; onCompany: (co: Company) => void;
}) {
  const co = company;
  const [failed, setFailed] = useState(false);
  // 하루 한 번: 저장된 시세를 오늘 확인하지 않았으면 탭을 열 때 새로 받는다 (KRX 의 빠진 거래일만, 무료, 몇 초)
  const today = new Date().toLocaleDateString("sv-SE");
  // 자료 형식이 바뀌었으면(옛 버전) 날짜와 상관없이 다시 만든다 (2026-11-24)
  const stale = !co || !!co.error || (co.v ?? 0) < COMPANY_V || (co.price_checked_at ?? co.made_at ?? "").slice(0, 10) !== today;
  useEffect(() => {
    if (!stale) return;
    let off = false;
    fetch(refreshUrl, { method: "POST" })
      .then((r) => r.json())
      .then((b) => { if (off) return; if (b.company) onCompany(b.company); else setFailed(true); })
      .catch(() => { if (!off) setFailed(true); });
    return () => { off = true; };
  }, [refreshUrl, stale]);   // eslint-disable-line react-hooks/exhaustive-deps
  const loading = stale && !failed;                   // 새 시세가 오면 stale 이 풀린다
  if (!co || co.error) {
    return (
      <div className={c.page}>
        <div className={c.notice}>
          {loading ? "기본 종목정보를 만드는 중입니다 (처음에는 1~2분)…" : "기본 종목정보를 만들지 못했습니다."}
        </div>
      </div>
    );
  }
  const sec = co.sector && !co.sector.error ? co.sector : null;
  const fisisUsed = (menu === "fin" && (!!sec?.series?.length || co.insurance?.some((r) => r.src?.csm === "FISIS" || r.src?.kics === "FISIS")))
    || (menu === "peers" && (!!sec?.peers?.length || co.peers?.rows?.some((r) => r.kics_src)));
  const sources = menu === "flows" ? ["토스증권"] : [
    "OpenDART",
    ...(menu === "overview" || menu === "peers" ? ["KRX"] : []),
    ...(menu === "overview" ? ["토스증권(주가 추이)"] : []),
    ...(fisisUsed ? ["금융감독원 금융통계정보시스템"] : []),
  ];
  return (
    <div className={c.page}>
      <Header co={co} refreshing={loading} failed={failed} />
      <div key={menu} className={c.content}>
        {menu === "overview" && <Overview co={co} />}
        {menu === "fin" && <Financial co={co} cardsUrl={reportId ? `/api/reports/${reportId}/charts` : `/api/stocks/${co.ticker}/charts`} />}
        {menu === "peers" && (co.peers?.fin
          ? <><SectorPerf ticker={co.ticker} part={sec?.part} name={co.name} /><FinPeers co={co} s={sec} /><PeerTrend co={co} /></>   // 금융: 업권 등락률 → 경쟁사(탭) → 추이 비교
          : <><PeerView co={co} top={<SectorBar ticker={co.ticker} preferId={co.peers?.sector?.id} name={co.name} />} /><PeerTrend co={co} />{sec && <SectorPeers s={sec} />}</>)}
        {menu === "flows" && <FlowsView ticker={co.ticker} />}
        <p className={c.source}>출처: {sources.join(", ")}</p>
      </div>
    </div>
  );
}


/* ───────── 리포트 없이 검색한 종목 (2026-11-23): 같은 기본 종목정보를 종목코드만으로. 처음 1~2분, 이후 저장 ───────── */
export function StockView({ ticker, name, menu }: { ticker: string; name: string; menu: CompanyMenu }) {   // 메뉴는 왼쪽 목록에서 고른다
  const [co, setCo] = useState<{ ticker: string; data: Company | null; error?: string } | null>(null);
  useEffect(() => {
    let off = false;
    fetch(`/api/stocks/${ticker}`).then((r) => r.json())
      .then((b) => { if (!off) setCo({ ticker, data: b.company && !b.company.error ? b.company : null, error: b.error ?? b.company?.error }); })
      .catch(() => { if (!off) setCo({ ticker, data: null, error: "종목정보를 만들지 못했습니다." }); });
    return () => { off = true; };
  }, [ticker]);
  const mine = co?.ticker === ticker ? co : null;
  return (
    <div className={c.stockPage}>
      {!mine ? <div className={c.page}><div className={c.notice}>{name} 기본 종목정보를 만드는 중입니다 (처음에는 1~2분)…</div></div>
        : !mine.data ? <div className={c.page}><div className={c.notice}>{mine.error ?? "종목정보를 만들지 못했습니다."}</div></div>
          : <CompanyView company={mine.data} refreshUrl={`/api/stocks/${ticker}`} menu={menu}
                         onCompany={(d) => setCo({ ticker, data: d })} />}
    </div>
  );
}
