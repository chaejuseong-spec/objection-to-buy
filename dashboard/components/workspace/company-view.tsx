"use client";

// 기본 종목정보 탭 (2026-11-22): 회사의 '지금' 재무·실적·배당·시세. 리포트 검증과 별개의 참고 화면.
// 데이터: result.company (파이썬 tribunal/external/company.py). DART·KRX·금감원 FISIS 값 그대로 + 코드가 계산한 비율. 없는 값은 '-'.
// 하위 메뉴는 왼쪽 목록의 '기본 종목정보' 아래에 들여 보인다 (Workspace 가 상태를 가진다). 출처는 화면 맨 아래 한 줄.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Company, FinKey, FinPeriod, FisisPeer, PeerSeries, Sector, StatementTable } from "@/lib/types";
import { FinChart, FinCharts, FinTable, fmtWon, useLatest, type Series } from "./fin-chart";
import { ExportButton, MetricExplorer, toRows, type Metric } from "./explorer";
import { PriceChart } from "./price-chart";
import { InfoTip } from "./primitives";
import c from "./company.module.css";

// 차트 색 (2026-11-22, 색약 검사 통과 조합): 막대 파랑·초록, 선 주황·보라
const C1 = "#2a78d6", C2 = "#1baf7a", OR = "#eb6834", OR2 = "#6250d6";
export type CompanyMenu = "overview" | "fin" | "peers";
export function companyMenus(co?: Company | null): { id: CompanyMenu; label: string }[] {
  if (!co || co.error) return [];
  return [
    { id: "overview", label: "기업개요" }, { id: "fin", label: "재무" }, { id: "peers", label: "동종업계 비교" },
  ];
}
type Mode = "annual" | "quarter";

/* ───────── 계산 (모두 공시 값으로 코드가 계산. 하나라도 없으면 null) ───────── */
const ratio = (a?: number | null, b?: number | null) => (a != null && b != null && b !== 0 ? (a / b) * 100 : null);
function growth(cur?: number | null, prev?: number | null) {
  return cur != null && prev != null && prev > 0 ? ((cur - prev) / prev) * 100 : null;   // 기준이 0 이하이면 증가율을 정하지 않는다
}
const label = (p: FinPeriod, mode: Mode) => (mode === "annual" ? `${p.period}년` : `${p.period.slice(2, 4)}년 ${+p.period.slice(5)}월`);
const qLabel = (period: string) => `${period.slice(2, 4)}년 ${+period.slice(5)}월`;

function sliceRange<T>(list: T[], mode: Mode, years: number) {
  return list.slice(-(mode === "annual" ? years : years * 4));
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

type Group = "stability" | "liquidity" | "profit" | "growth" | "sales" | "margin" | "inventory" | "capex";
const GROUPS: Record<Group, string> = {
  stability: "안정성", liquidity: "유동성", profit: "수익성", growth: "성장성", sales: "매출·영업이익",
  margin: "마진 구조", inventory: "재고", capex: "설비투자",
};

function groupSeries(g: Group, all: FinPeriod[], mode: Mode, years: number): Series[] {
  const rows = sliceRange(all, mode, years);
  const an = mode === "quarter" ? 4 : 1;
  const days = mode === "quarter" ? 91 : 365;
  switch (g) {
    case "stability":
      return [money("equity", "자본총계", C1, rows), money("liab", "부채총계", C2, rows),
        { key: "debt", label: "부채비율", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.liab, p.equity)), marks: rows.map((p) => mark(p, "liab", "equity")) }];
    case "liquidity":
      return [money("cur_assets", "유동자산", C1, rows), money("cur_liab", "유동부채", C2, rows),
        { key: "cur", label: "유동비율", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.cur_assets, p.cur_liab)) }];
    case "profit": {
      // 기간마다: 지배주주 순이익·자본(기초·기말)이 다 있으면 지배주주 기준, 아니면 전체 순이익 ÷ 전체 자본 (지배주주 칸이 없는 재무제표)
      const roe = (p: FinPeriod) => {
        const q = prevOf(all, p, mode, false);
        const own = p.ni_owner != null && p.equity_owner != null && q?.equity_owner != null;
        const n = own ? p.ni_owner! : p.ni, e = own ? p.equity_owner! : p.equity, e0 = own ? q!.equity_owner! : q?.equity;
        return n != null && e != null && e0 != null ? (n * an) / ((e + e0) / 2) * 100 : null;
      };
      return [money("op", "영업이익", C1, rows), money("ni", "당기순이익", C2, rows),
        { key: "roe", label: mode === "quarter" ? "ROE(연환산)" : "ROE", color: OR, kind: "line", fmt: "pct",
          values: rows.map(roe), marks: rows.map((p) => mark(p, "ni_owner", "ni")) }];
    }
    case "growth":
      return [money("op", "영업이익", C1, rows), money("ni", "당기순이익", C2, rows),
        { key: "op_g", label: mode === "quarter" ? "영업이익 증가율(전년 동기)" : "영업이익 증가율", color: OR, kind: "line", fmt: "pct", signed: true, values: rows.map((p) => growth(p.op, prevOf(all, p, mode)?.op)) },
        { key: "ni_g", label: mode === "quarter" ? "순이익 증가율(전년 동기)" : "순이익 증가율", color: OR2, kind: "line", fmt: "pct", signed: true, dashed: true, values: rows.map((p) => growth(p.ni, prevOf(all, p, mode)?.ni)) }];
    case "sales":
      return [money("revenue", "매출액", C1, rows), money("op", "영업이익", C2, rows),
        { key: "opm", label: "영업이익률", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.op, p.revenue)) }];
    case "margin":
      return [money("gross", "매출총이익", C1, rows), money("sga", "판매비와관리비", C2, rows),
        { key: "gpm", label: "매출총이익률", color: OR, kind: "line", fmt: "pct", values: rows.map((p) => ratio(p.gross, p.revenue)) },
        { key: "sgar", label: "판관비율", color: OR2, kind: "line", fmt: "pct", dashed: true, values: rows.map((p) => ratio(p.sga, p.revenue)) }];
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
  const up = (p?.change ?? 0) > 0, down = (p?.change ?? 0) < 0;
  const day = p ? `${p.date.slice(0, 4)}.${p.date.slice(4, 6)}.${p.date.slice(6)}` : "";
  return (
    <header className={c.header}>
      <div className={c.headLeft}>
        <h2 className={c.headName}>{co.name}<span>{co.ticker}</span>{p?.market && <span>{p.market}</span>}</h2>
        {p?.close != null ? (
          <div className={c.headPrice}>
            <b>{p.close.toLocaleString("ko-KR")}원</b>
            <span className={up ? c.pos : down ? c.neg : undefined}>
              {up ? "+" : ""}{(p.change ?? 0).toLocaleString("ko-KR")}원 ({up ? "+" : ""}{(p.rate ?? 0).toFixed(2)}%)
            </span>
            <small>{day} 종가{refreshing ? ", 최신 시세 확인 중" : failed ? ", 최신 시세를 받지 못함" : ""}</small>
          </div>
        ) : <div className={c.headPrice}><small>시세 없음</small></div>}
      </div>
      <dl className={c.headStats}>
        <div><dt>1일 범위</dt><dd>{p?.low != null && p.high != null ? `${p.low.toLocaleString("ko-KR")} ~ ${p.high.toLocaleString("ko-KR")}원` : "-"}</dd></div>
        <div><dt>52주 범위</dt><dd>{co.range52 ? `${co.range52.low.toLocaleString("ko-KR")} ~ ${co.range52.high.toLocaleString("ko-KR")}원` : "-"}</dd></div>
        <div><dt>시가총액</dt><dd>{fmtWon(p?.mktcap)}</dd></div>
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
  const rows: [string, ReactNode][] = [
    ["기업명", i.corp_name ?? co.name],
    ["홈페이지", i.hm_url ? <a href={/^https?:/.test(i.hm_url) ? i.hm_url : `https://${i.hm_url}`} target="_blank" rel="noreferrer">{i.hm_url}</a> : "-"],
    ["결산월", i.acc_mt ? `${+i.acc_mt}월` : "-"], ["상장주식수", p?.shares != null ? `${p.shares.toLocaleString("ko-KR")}주` : "-"],
    ["시가총액", fmtWon(p?.mktcap)],
  ];
  const ind: [string, string, string][] = [
    ["PER", m.per != null ? `${m.per.toFixed(1)}배` : "-", "주가 ÷ 최근 4분기 주당순이익"],
    ["PBR", m.pbr != null ? `${m.pbr.toFixed(2)}배` : "-", "주가 ÷ 최근 분기말 주당순자산"],
    ["EPS", m.eps != null ? `${Math.round(m.eps).toLocaleString("ko-KR")}원` : "-", "최근 4분기 지배주주 순이익 ÷ 상장주식수"],
    ["BPS", m.bps != null ? `${Math.round(m.bps).toLocaleString("ko-KR")}원` : "-", "최근 분기말 지배주주 자본 ÷ 상장주식수"],
    ["ROE", m.roe != null ? `${m.roe.toFixed(1)}%` : "-", "최근 4분기 지배주주 순이익 ÷ 평균 지배주주 자본"],
  ];
  const dv = co.dividends.length ? co.dividends[co.dividends.length - 1] : null;      // 최근 사업연도 배당 (회사 공시)
  if (dv) ind.push(
    ["주당배당금", dv.dps != null ? `${dv.dps.toLocaleString("ko-KR")}원` : "-", `${dv.year}년 보통주 1주당 현금배당 (회사 공시)`],
    ["시가배당률", dv.yield != null ? `${dv.yield.toFixed(1)}%` : "-", `${dv.year}년 주당배당금 ÷ 배당기준일 무렵 주가 (회사 공시)`],
    ["배당성향", dv.payout != null ? `${dv.payout.toFixed(1)}%` : "-", `${dv.year}년 현금배당 총액 ÷ 연결 당기순이익 (회사 공시)`],
  );
  return (
    <>
      <PriceChart ticker={co.ticker} />
      <Section title="기업 정보">
        <dl className={c.infoRow}>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </Section>
      {co.sales_mix && <SalesMix mix={co.sales_mix} />}
      <Section title="투자 지표" sub={`최근 종가 기준${dv ? `, 배당은 ${dv.year}년` : ""}`}>
        <dl className={c.metricRow}>
          {ind.map(([k, v, t]) => <div key={k}><dt>{k}<InfoTip text={t} /></dt><dd>{v}</dd></div>)}
        </dl>
      </Section>
      <DividendTrend co={co} />
    </>
  );
}

/* ───────── 재무 ───────── */
// 지표 탐색 목록 (2026-11-22): 화면에 이미 있던 값만 모은다. 비율·증가율은 공시 값을 코드가 나눈 것('계산' 표시)
const FLOW = new Set<string>(["revenue", "op", "ni", "gross", "sga", "capex", "cogs", "pretax", "ni_owner", "cfo", "cfi", "cff"]);
const NOTE: Record<string, string> = {
  debt: "부채총계 ÷ 자본총계", cur: "유동자산 ÷ 유동부채", roe: "지배주주 순이익 ÷ 평균 지배주주 자본 (분기는 ×4 연환산, 지배주주 구분이 없는 재무제표는 순이익 ÷ 평균 자본)",
  op_g: "전년 같은 기간 대비, 기준이 적자면 표시 안 함", ni_g: "전년 같은 기간 대비, 기준이 적자면 표시 안 함",
  opm: "영업이익 ÷ 매출액", gpm: "매출총이익 ÷ 매출액", sgar: "판매비와관리비 ÷ 매출액",
  dio: "재고자산 ÷ 매출원가 × 기간 일수", capex: "현금흐름표의 유형자산 취득", capexr: "설비투자 ÷ 매출액",
  csm: "보유 계약에서 앞으로 인식할 미실현 이익", csm_new: "그 분기 새 계약으로 추가 적립된 CSM",
  csm_amort: "CSM 중 그 분기 보험이익으로 인식된 금액", kics: "가용자본 ÷ 요구자본 (법정 최소 100%)",
  ins_profit: "손익계산서의 보험서비스결과 (보험수익 − 보험서비스비용 등)", inv_profit: "손익계산서의 투자손익 (투자수익 − 투자비용)",
  div_rev: "손익계산서의 배당금수익 (보유 주식에서 받은 배당)",
};
const EXTRA: [FinKey, string][] = [["assets", "자산총계"], ["pretax", "법인세차감전순이익"], ["ni_owner", "지배주주 순이익"], ["equity_owner", "지배주주 자본"],
  ["cogs", "매출원가"], ["cfo", "영업활동 현금흐름"], ["cfi", "투자활동 현금흐름"], ["cff", "재무활동 현금흐름"]];
const ACCOUNT_CAT = "계정";

const isInsurer = (co: Company) => !!co.insurance?.some((r) => r.csm != null || r.kics != null);

function catalog(co: Company, groups: Group[], mode: Mode, years: number): Metric[] {
  const st = co.statements;
  const list = mode === "annual" ? st.annual : st.quarter;
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
      ["kics", "지급여력비율(K-ICS)", "line", "pct", false]] as const).forEach(([key, name, kind, fmt, flow]) => {
      if (flow && mode === "annual") return;
      push({ key, label: name, kind, fmt, cat: "보험 (CSM·K-ICS)", src, note: NOTE[key],
        values: keys.map((k) => at(k)?.[key] ?? null), marks: keys.map((k) => (at(k)?.derived?.includes(key) ? "derived" : null)) });
    });
  }
  // 보험 손익 (DART 손익계산서, 연결이 있으면 연결): 보험손익·투자손익·배당금수익
  const basis = st.fs === "별도" ? "별도" : "연결";
  ([["ins_profit", "보험손익"], ["inv_profit", "투자손익"], ["div_rev", "배당금수익"]] as const).forEach(([k, name]) => {
    push({ key: k, label: `${name} (${basis})`, fmt: "won", kind: "bar", cat: `보험 손익 (${basis})`, src: "DART",
      values: rows.map((p) => p[k] ?? null), marks: rows.map((p) => mark(p, k)), note: NOTE[k] });
  });
  // 금융회사: 금감원 업종 지표
  const s = co.sector && !co.sector.error ? co.sector : null;
  if (s?.series?.length) {
    const at = (k: string) => s.series.find((r) => r.period === yearEnd(k));
    s.metrics.forEach((m) => {
      if (m.key === "kics" && isInsurer(co)) return;
      const won = m.unit === "원";                          // 손익 금액: 분기 값, 연간은 12월 누적
      push({ key: `fisis_${m.key}`, label: won ? `${m.label} (개별)` : m.label, kind: won ? "bar" : "line", fmt: won ? "won" : "pct",
        cat: won ? "보험 손익 (개별)" : `업종 지표 (${s.sector})`, src: "FISIS",
        values: keys.map((k) => (at(k)?.[won && mode === "annual" ? `${m.key}_ytd` : m.key] as number | null | undefined) ?? null) });
    });
  }
  // 재무 묶음 (안정성·수익성 …)
  for (const g of groups) {
    for (const x of groupSeries(g, list, mode, years)) {
      push({ key: x.key, label: x.label, fmt: x.fmt, values: x.values, marks: x.marks, signed: x.signed,
        kind: x.fmt === "won" && FLOW.has(x.key) ? "bar" : "line", cat: GROUPS[g], calc: x.fmt !== "won", src: x.fmt === "won" ? "DART" : undefined, note: NOTE[x.key] });
    }
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
    t.accounts.filter((a) => !a.key).forEach((a) => {
      const acc = `${a.id}|${a.name}`;
      push({ key: `${tab}:${acc}`, label: tab === "CF" && mode === "quarter" ? `${a.name} (연초 누적)` : a.name, fmt: "won",
        kind: tab === "BS" ? "line" : "bar", cat: `${name} ${ACCOUNT_CAT}`, src: "DART", values: keys.map((k) => t[mode][k]?.[acc] ?? null) });
    });
  });
  return out;
}

function Financial({ co, cardsUrl }: { co: Company; cardsUrl: string }) {
  const st = co.statements;
  const peers = usePeers(co);
  const cards = useCards(cardsUrl);
  const has = (k: FinKey) => st.quarter.some((p) => p[k] != null);
  const order: Group[] = ["sales", "profit", "growth", "margin", "stability", "liquidity", "inventory", "capex"];
  const groups = order.filter((g) =>
    g === "liquidity" ? has("cur_assets") && has("cur_liab")
      : g === "sales" ? has("revenue")
        : g === "margin" ? has("gross") && has("revenue")
          : g === "inventory" ? has("inventory") && has("cogs")
            : g === "capex" ? has("capex") && has("revenue") && !co.financial
              : true);
  const [mode, setMode] = useState<Mode>("quarter");
  const [years, setYears] = useState(3);
  const list = mode === "annual" ? st.annual : st.quarter;
  const periods = sliceRange(list, mode, years).map((p) => label(p, mode));
  const metrics = catalog(co, groups, mode, years);
  const catNames = [...new Set(metrics.map((m) => m.cat))];
  const iCon = catNames.indexOf("보험 손익 (연결)"), iSep = catNames.indexOf("보험 손익 (개별)");
  if (iCon >= 0 && iSep > iCon) { catNames.splice(iSep, 1); catNames.splice(iCon, 0, "보험 손익 (개별)"); }   // 개별 다음 연결
  const cats = catNames.map((name) => ({ name, open: false }));          // 처음엔 모두 닫힘, 고른 지표가 있는 묶음만 열림
  const insurer = isInsurer(co);
  const want = insurer ? ["ni", "roe"]
    : co.financial && co.sector?.series?.length ? [`fisis_${co.sector.metrics[0]?.key}`, "ni"]
      : ["revenue", "op", "opm"];
  const defaults = want.filter((k) => metrics.some((m) => m.key === k));
  const controls = (
    <div className={c.filters}>
      <Segment label="기간" value={years} onChange={setYears} options={[[1, "1년"], [3, "3년"], [5, "5년"]]} />
      <Segment label="연간·분기" value={mode} onChange={setMode} options={[["quarter", "분기"], ["annual", "연간"]]} />
    </div>
  );
  return (
    <>
      {insurer && <Insurance co={co} />}
      <Section title="지표 탐색" sub={st.fs ? `${st.fs} 기준` : undefined}>
        <MetricExplorer key={co.ticker} metrics={metrics} cats={cats} periods={periods} controls={controls} back={mode === "quarter" ? 4 : 1}
                        me={{ name: co.name, color: ME_COLOR }}
                        peerPane={peers.any ? <PeerPicker co={co} p={peers} pane /> : undefined}
                        compare={peers.ready.length ? (m) => peers.ready.map((x) => peerLine(x, m, mode, years, sliceRange(list, mode, years))) : undefined}
                        onSave={(keys, together) => cards.add({ keys, together, mode, years, peers: peers.checked.map(([id]) => id),
                          title: keys.map((k) => metrics.find((m) => m.key === k)?.label ?? k).join(" · ") })}
                        onExport={(series, focus, cmp, together) => ({ together,
                          title: `${co.name} 재무 지표`, sheet: "재무 지표", ticker: co.ticker,
                          basis: `${st.fs ?? ""} 기준, ${mode === "quarter" ? "분기" : "연간"}${mode === "annual" && (insurer || co.sector) ? " (보험·업종 지표는 12월 말 값)" : ""}`,
                          periods: sliceRange(list, mode, years).map((p) => p.period),
                          rows: cmp ?? toRows(focus ? [...series.filter((x) => x.key === focus), ...series.filter((x) => x.key !== focus)] : series, focus),
                          notes: series.map((x) => metrics.find((m) => m.key === x.key)?.note && `${x.label}: ${metrics.find((m) => m.key === x.key)!.note}`).filter(Boolean) as string[],
                          sources: [...new Set([...series, ...(cmp ?? [])].flatMap((x) => (x.src ?? "DART").split("·")))].map((x) => (x === "DART" ? "OpenDART" : x === "FISIS" ? "금융감독원 금융통계정보시스템" : x)),
                        })}
                        defaults={defaults.length ? defaults : metrics.slice(0, 2).map((m) => m.key)} />
        <Notes items={[
          mode === "quarter" && "*: 계산 값 (12월 = 연간 − 3분기 누적, 분기 현금흐름 = 누적 차감)",
          metrics.some((m) => m.marks?.some((x) => x === "missing")) && "'-': 공시 API 값 오류로 표시 안 함",
          mode === "annual" && (insurer || co.sector) && "연간 화면의 보험·업종 지표: 12월 말 값",
        ]} />
      </Section>
      {cards.list.length > 0 && (
        <Section title="내 차트" sub={`${cards.list.length}개`}>
          <div className={c.cardGrid}>
            {cards.list.map((cd) => <ChartCard key={cd.id} co={co} card={cd} groups={groups}
                                               onRemove={() => cards.remove(cd.id)} onRename={(t) => cards.rename(cd.id, t)} />)}
          </div>
        </Section>
      )}
      <Statements st={st} />
    </>
  );
}

/** 보험사: CSM 잔액(잔액 추이), 신계약·상각(기간 흐름), K-ICS(비율 추이)를 각각의 차트로 (회사 개별 기준) */
function Insurance({ co }: { co: Company }) {
  const all = co.insurance ?? [];
  const [years, setYears] = useState(3);
  const first = all.findIndex((r) => r.csm != null || r.csm_new != null || r.kics != null);
  const rows = all.slice(Math.max(first, all.length - years * 4, 0));
  const periods = rows.map((r) => qLabel(r.period));
  const mk = (r: (typeof all)[number], k: string) => (r.derived?.includes(k) ? "derived" as const : null);
  const bal: Series = { key: "csm", label: "CSM 잔액", color: "#2a78d6", kind: "line", fmt: "won", values: rows.map((r) => r.csm ?? null), src: "DART·FISIS" };
  const flow: Series[] = [
    { key: "csm_new", label: "신계약 CSM", color: "#1baf7a", kind: "bar", fmt: "won", values: rows.map((r) => r.csm_new ?? null), marks: rows.map((r) => mk(r, "csm_new")), src: "DART" },
    { key: "csm_amort", label: "CSM 상각액", color: "#eb6834", kind: "bar", fmt: "won", values: rows.map((r) => r.csm_amort ?? null), marks: rows.map((r) => mk(r, "csm_amort")), src: "DART" },
  ];
  const kics: Series = { key: "kics", label: "지급여력비율(K-ICS)", color: "#6250d6", kind: "line", fmt: "pct", values: rows.map((r) => r.kics ?? null), src: "DART·FISIS" };
  const hasFlow = flow.some((x) => x.values.some((v) => v != null));
  return (
    <Section title="보험 핵심 지표" sub="별도 기준, 분기" right={
      <div className={c.filters}><Segment label="기간" value={years} onChange={setYears} options={[[1, "1년"], [3, "3년"]]} /></div>}>
      <div className={c.insGrid}>
        <div><div className={c.chartUnit}>CSM 잔액</div><FinChart periods={periods} series={[bal]} height={200} /></div>
        {hasFlow && <div><div className={c.chartUnit}>신계약 CSM · CSM 상각액 (분기 흐름)</div><FinChart periods={periods} series={flow} height={200} /></div>}
        <div><div className={c.chartUnit}>지급여력비율 (K-ICS, 경과조치 적용 전)</div><FinChart periods={periods} series={[kics]} height={200} /></div>
      </div>
      <FinTable periods={periods} series={[bal, ...flow, kics]} />
      <Notes items={[
        `CSM 잔액: ${NOTE.csm}`, `신계약 CSM: ${NOTE.csm_new}`, `CSM 상각액: ${NOTE.csm_amort}`, `K-ICS = ${NOTE.kics}`,
        rows.some((r) => r.csm_new == null) && "'-': 분기 값 미공시",
      ]} />
    </Section>
  );
}

function Statements({ st }: { st: Company["statements"] }) {
  const [tab, setTab] = useState<"IS" | "BS" | "CF">("IS");
  const [mode, setMode] = useState<Mode>("annual");
  const [open, setOpen] = useState(false);
  const t: StatementTable | undefined = st.tables[tab];
  const box = useLatest(`${tab}${mode}`);
  if (!t) return null;
  const cols = Object.keys(t[mode]).slice(-8);
  const key = (a: StatementTable["accounts"][number]) => `${a.id}|${a.name}`;
  const ORDER = ["revenue", "cogs", "gross", "sga", "ins_profit", "inv_profit", "div_rev", "op", "pretax", "ni", "ni_owner", "assets", "cur_assets", "inventory", "liab", "cur_liab", "equity", "equity_owner", "cfo", "cfi", "capex", "cff"];
  const main = t.accounts.filter((a) => a.key).sort((x, y) => ORDER.indexOf(x.key!) - ORDER.indexOf(y.key!));
  const rest = t.accounts.filter((a) => !a.key);
  const cell = (col: string, a: StatementTable["accounts"][number]) => {
    const v = t[mode][col]?.[key(a)];
    if (v == null) return "-";
    return Math.abs(v) >= 1e8 ? `${Math.round(v / 1e8).toLocaleString("ko-KR")}억` : Math.round(v).toLocaleString("ko-KR");
  };
  const head = (col: string) => (mode === "annual" ? `${col}년` : qLabel(col));
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
        <Segment label="연간·분기" value={mode} onChange={setMode} options={[["annual", "연간"], ["quarter", "분기"]]} />
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

type PeerKey = "mktcap" | "per" | "pbr" | "roe" | "debt" | "ni4" | "csm" | "csm_new" | "kics";
const PEER_COLS: { key: PeerKey; label: string; fmt: (v: number) => string; ins?: boolean; note?: string }[] = [
  { key: "mktcap", label: "시가총액", fmt: (v) => fmtWon(v) },
  { key: "per", label: "PER", fmt: (v) => `${v.toFixed(1)}배`, note: "주가 ÷ 최근 4분기 주당순이익" },
  { key: "pbr", label: "PBR", fmt: (v) => `${v.toFixed(2)}배`, note: "주가 ÷ 최근 분기말 주당순자산" },
  { key: "roe", label: "ROE", fmt: (v) => `${v.toFixed(1)}%`, note: "최근 4분기 지배주주 순이익 ÷ 평균 지배주주 자본" },
  { key: "ni4", label: "순이익(최근 4분기)", fmt: (v) => fmtWon(v) },
  { key: "debt", label: "부채비율", fmt: (v) => `${Math.round(v).toLocaleString("ko-KR")}%` },
  { key: "csm", label: "CSM 잔액", fmt: (v) => fmtWon(v), ins: true },
  { key: "csm_new", label: "신계약 CSM(분기)", fmt: (v) => fmtWon(v), ins: true },
  { key: "kics", label: "K-ICS", fmt: (v) => `${v.toFixed(1)}%`, ins: true },
];

function PeerView({ co }: { co: Company }) {
  const pe = co.peers;
  const cols = PEER_COLS.filter((x) => !x.ins || pe?.insurer);
  const [key, setKey] = useState<PeerKey>("mktcap");
  if (!pe || pe.error || !pe.rows?.length) {
    return <Section title="상장 경쟁사"><p className={c.empty}>{pe?.error ? "경쟁사 정보를 만들지 못했습니다." : "경쟁사 정보가 아직 없습니다."}</p></Section>;
  }
  const rows = pe.rows.filter((r) => !r.error);
  const col = cols.find((x) => x.key === key) ?? cols[0];
  return (
    <Section title="상장 경쟁사" sub={`같은 업종 코드 ${rows.length}곳`} right={
      <div className={c.filters}><Select label="비교 지표" value={key} onChange={setKey} options={cols.map((x) => [x.key, x.label])} /></div>}>
      <RankList label={col.label} items={rows.map((r) => ({ id: r.ticker, name: r.name, value: r[col.key] ?? null, me: r.ticker === co.ticker }))} fmt={col.fmt} />
      <div className={c.tableScroll}>
        <table className={c.finTable}>
          <thead>
            <tr><th className={c.stick}>회사</th><th>종가</th><th>등락률</th>
              {cols.map((x) => <th key={x.key} title={x.note}>{x.label}</th>)}<th>기준 분기</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className={r.ticker === co.ticker ? c.meRow : undefined}>
                <th className={c.stick} scope="row">{r.name}<small className={c.tick}>{r.ticker}</small></th>
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

/** 업계 전체 (금감원, 비상장 포함): 업종 지표를 회사별로 */
function SectorPeers({ s }: { s: Sector }) {
  const [key, setKey] = useState(s.metrics[0]?.key ?? "");
  if (!s.peers?.length) return null;
  const m = s.metrics.find((x) => x.key === key) ?? s.metrics[0];
  const fmtOf = (unit: string) => (v: number) => (unit === "원" ? fmtWon(v) : `${v.toFixed(2)}%`);
  return (
    <Section title={`${s.sector} 업계 전체`} sub={`비상장 포함 ${s.peers.length}곳, ${s.month ? qLabel(`${s.month.slice(0, 4)}.${s.month.slice(4)}`) : ""} 기준`} right={
      <div className={c.filters}><Select label="비교 지표" value={key} onChange={setKey} options={s.metrics.map((x) => [x.key, x.label])} /></div>}>
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
const asCompany = (p: PeerSeries): Company => ({
  statements: { fs: p.fs ?? null, annual: p.annual, quarter: p.quarter, tables: {}, sources: [] },
  insurance: p.insurance ?? null, sector: p.sector ?? null, financial: p.financial,
}) as unknown as Company;

function groupsOf(co: Company): Group[] {
  const has = (k: FinKey) => co.statements.quarter.some((p) => p[k] != null);
  const order: Group[] = ["sales", "profit", "growth", "margin", "stability", "liquidity", "inventory", "capex"];
  return order.filter((g) =>
    g === "liquidity" ? has("cur_assets") && has("cur_liab")
      : g === "sales" ? has("revenue")
        : g === "margin" ? has("gross") && has("revenue")
          : g === "inventory" ? has("inventory") && has("cogs")
            : g === "capex" ? has("capex") && has("revenue") && !co.financial
              : true);
}
function byPeriod(co: Company, mode: Mode, years: number, key: string): Map<string, { v: number | null; m: "derived" | "missing" | null }> {
  const list = mode === "annual" ? co.statements.annual : co.statements.quarter;
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
function usePeers(co: Company) {
  const [extra, setExtra] = useState<{ ticker: string; name: string }[]>([]);     // 직접 추가한 상장사 (2026-11-23)
  const [dartUnlisted, setDartUnlisted] = useState<{ corp_code: string; name: string }[]>([]);
  const induty = String(co.info?.induty_code ?? "");
  useEffect(() => {                                        // 금융사가 아니면 같은 업종 비상장 정기보고서 제출사 (DART)
    if (co.sector && !co.sector.error) return;
    let off = false;
    fetch(`/api/peers/unlisted?induty=${encodeURIComponent(induty)}`).then((r) => r.json())
      .then((b) => { if (!off) setDartUnlisted(Array.isArray(b.items) ? b.items : []); }).catch(() => {});
    return () => { off = true; };
  }, [induty, co.sector]);
  const auto = (co.peers?.rows ?? []).filter((r) => !r.error && r.ticker !== co.ticker);
  const listed: { ticker: string; name: string }[] = [...auto, ...extra.filter((x) => x.ticker !== co.ticker && !auto.some((r) => r.ticker === x.ticker))];
  const sec = co.sector && !co.sector.error ? co.sector : null;
  const unlisted = (sec?.peers ?? []).filter((r) => !r.me);
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
  return { listed, unlisted, dartUnlisted, sec, checked, isOn, toggle, ready, status, add, any: true };
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
  if (pane) {
    return (
      <div className={c.peerPane}>
        <div className={c.peerPaneHead}><span>비교 회사</span><small>{p.checked.length}/{PEER_MAX}</small></div>
        <PeerAdd onPick={p.add} />
        {p.listed.map((r) => item(`T:${r.ticker}`, r.name, r.ticker))}
        {p.unlisted.length > 0 && p.sec && <>
          <button type="button" className={c.peerPaneMore} aria-expanded={openAll} onClick={() => setOpenAll((o) => !o)}>
            {p.sec.sector} 업계 전체 (금감원, 비상장 포함) {p.unlisted.length}곳
          </button>
          {(openAll ? p.unlisted : p.unlisted.filter((r) => p.isOn(`F:${r.finance_cd}`))).map((r) => item(`F:${r.finance_cd}`, r.name))}
        </>}
        {p.dartUnlisted.length > 0 && <>
          <button type="button" className={c.peerPaneMore} aria-expanded={openAll} onClick={() => setOpenAll((o) => !o)}>
            같은 업종 비상장 (DART 정기보고서) {p.dartUnlisted.length}곳
          </button>
          {(openAll ? p.dartUnlisted : p.dartUnlisted.filter((r) => p.isOn(`D:${r.corp_code}`))).map((r) => item(`D:${r.corp_code}`, r.name))}
        </>}
      </div>
    );
  }
  return (
    <div className={c.peerRows}>
      <div className={c.peerPick} role="group" aria-label="상장 경쟁사">
        <span className={c.peerHead}>상장 경쟁사 (DART)</span>
        {meItem}
        <PeerAdd onPick={p.add} inline />
        {p.listed.map((r) => item(`T:${r.ticker}`, r.name, r.ticker))}
      </div>
      {p.unlisted.length > 0 && p.sec && (
        <div className={c.peerPick} role="group" aria-label="업계 전체">
          <span className={c.peerHead}>{p.sec.sector} 업계 전체 (금감원, 비상장 포함)</span>
          {p.unlisted.map((r) => item(`F:${r.finance_cd}`, r.name))}
        </div>
      )}
      {p.dartUnlisted.length > 0 && (
        <div className={c.peerPick} role="group" aria-label="같은 업종 비상장">
          <span className={c.peerHead}>같은 업종 비상장 (DART 정기보고서)</span>
          {p.dartUnlisted.map((r) => item(`D:${r.corp_code}`, r.name))}
        </div>
      )}
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
  const at = (k: string) => d.series.find((r) => r.period === (mode === "annual" ? `${k}.12` : k));
  const col = fk && m.fmt === "won" && m.key.startsWith("fisis_") && mode === "annual" ? `${fk}_ytd` : fk;   // 손익 금액의 연간 = 12월 누적
  return { key: x.id, label: d.name, color, kind: m.kind ?? "line", fmt: m.fmt, src: "FISIS",
    values: rows.map((p) => (col ? (at(p.period)?.[col] as number | null | undefined) ?? null : null)) };
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
  const [showMe, setShowMe] = useState(true);                         // 이 회사 선도 끄고 켤 수 있다
  const [mode, setMode] = useState<Mode>("quarter");
  const [years, setYears] = useState(3);
  const mine = catalog(co, groupsOf(co), mode, years).filter((m) => !m.cat.endsWith(ACCOUNT_CAT));
  const [key, setKey] = useState(() => (isInsurer(co) ? "csm" : co.statements.quarter.some((x) => x.revenue != null) ? "revenue" : "ni"));
  const metric = mine.find((m) => m.key === key) ?? mine[0];
  if (!p.any || !metric) return null;

  const list = mode === "annual" ? co.statements.annual : co.statements.quarter;
  const rows = sliceRange(list, mode, years);
  const periods = rows.map((x) => label(x, mode));
  const own = byPeriod(co, mode, years, metric.key);
  const series: Series[] = showMe ? [{ key: co.ticker, label: co.name, color: ME_COLOR, kind: "line", fmt: metric.fmt, strong: true,
    values: rows.map((x) => own.get(x.period)?.v ?? null), marks: rows.map((x) => own.get(x.period)?.m ?? null), src: metric.src, calc: metric.calc }] : [];
  p.ready.forEach((x) => series.push(peerLine(x, { ...metric, kind: "line" }, mode, years, rows)));
  return (
    <Section title="추이 비교" sub={`최대 ${PEER_MAX}곳`} right={
      <div className={c.filters}>
        <Select label="비교 지표" value={metric.key} onChange={setKey} options={mine.map((m) => [m.key, `${m.label} (${m.cat})`])} />
        <Segment label="기간" value={years} onChange={setYears} options={[[1, "1년"], [3, "3년"], [5, "5년"]]} />
        <Segment label="연간·분기" value={mode} onChange={setMode} options={[["quarter", "분기"], ["annual", "연간"]]} />
        <ExportButton make={() => ({
          title: `${co.name} 동종사 비교 ${metric.label}`, sheet: "동종사 비교", ticker: co.ticker,
          basis: `${mode === "quarter" ? "분기" : "연간"}. ${p.checked.some(([id]) => id.startsWith("F:")) ? "금감원 자료는 회사 개별(별도) 기준" : `${co.statements.fs ?? ""} 기준`}`,
          periods: rows.map((x) => x.period), rows: toRows(series, co.ticker),
          notes: metric.note ? [`${metric.label}: ${metric.note}`] : [],
          sources: ["OpenDART", ...(series.some((x) => x.src?.includes("FISIS")) ? ["금융감독원 금융통계정보시스템"] : [])],
        })} />
      </div>}>
      <PeerPicker co={co} p={p} me={{ on: showMe, toggle: () => setShowMe((v) => !v) }} metricKeys={[metric.key]} />
      {series.length === 0 ? <p className={c.empty}>비교할 회사를 고르세요.</p> : <>
        <FinChart key={`${metric.key}|${series.map((x) => x.key).join()}`} periods={periods} series={series} height={260} />
        <FinTable periods={periods} series={series} focus={co.ticker} />
      </>}
      <Notes items={[
        metric.note && `${metric.label}: ${metric.note}`,
        p.checked.some(([id]) => id.startsWith("F:")) && "업계 전체 회사: 업종 지표·K-ICS·CSM 잔액은 금감원(회사 개별 기준), 그 밖의 지표는 DART 정기보고서를 내는 회사만",
        mode === "quarter" && "*: 계산 값 (12월 = 연간 − 3분기 누적)",
      ]} />
    </Section>
  );
}

/* ───────── 차트 만들기 (2026-11-23): 지표·회사·함께 보기 조합을 카드로 저장. 숫자는 저장하지 않고 열 때마다 공시 자료로 다시 그린다 ───────── */
interface Card { id: string; title: string; keys: string[]; peers: string[]; together: boolean; mode: Mode; years: number }
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
  const list = card.mode === "annual" ? co.statements.annual : co.statements.quarter;
  const rows = sliceRange(list, card.mode, card.years);
  const periods = rows.map((p) => label(p, card.mode));
  const all = catalog(co, groups, card.mode, card.years);
  const ms = card.keys.map((k) => all.find((m) => m.key === k)).filter((m): m is Metric => !!m);
  const comparing = ready.length > 0;
  // 회사 비교: 이 회사 네이비 굵은 선 + 동종사 색, 두 번째 지표는 점선. 아니면 지표마다 색
  const lines: Series[] = comparing
    ? ms.flatMap((m, k) => [
        { ...m, key: `${m.key}|me`, label: `${co.name} ${m.label}`, color: ME_COLOR, strong: true, dashed: card.together && k === 1 && m.kind === "line" },
        ...ready.map((x) => ({ ...peerLine(x, m, card.mode, card.years, rows), key: `${m.key}|${x.id}`, label: `${x.d.name} ${m.label}`,
          dashed: card.together && k === 1 && m.kind === "line" })),
      ])
    : ms.map((m, k) => ({ ...m, color: CARD_PALETTE[k % CARD_PALETTE.length] }));
  const units = new Set(lines.map((x) => x.fmt)).size;
  const joined = card.together && units <= 2;
  const info = `${card.mode === "quarter" ? "분기" : "연간"} ${card.years}년${card.peers.length ? `, 비교 ${card.peers.length}곳` : ""}${joined ? ", 함께 보기" : ""}`;
  return (
    <div className={c.card}>
      <div className={c.cardHead}>
        <input className={c.cardTitle} defaultValue={card.title} aria-label="차트 이름" onBlur={(e) => { const t = e.target.value.trim(); if (t && t !== card.title) onRename(t); }} />
        <small>{info}{waiting ? ", 비교 회사 받는 중" : ""}</small>
        <span className={c.cardActions}>
          <ExportButton make={() => ({
            title: `${co.name} ${card.title}`, sheet: "차트", ticker: co.ticker, together: joined,
            basis: `${co.statements.fs ?? ""} 기준, ${card.mode === "quarter" ? "분기" : "연간"}`,
            periods: rows.map((p) => p.period),
            rows: lines.map((x) => ({ label: x.label, legend: x.label, group: comparing && !joined ? x.key.split("|")[0] : undefined, fmt: x.fmt, kind: x.kind,
              values: x.values, calc: x.calc, src: x.src, strong: !!x.strong || !comparing, dashed: x.dashed })),
            notes: ms.filter((m) => m.note).map((m) => `${m.label}: ${m.note}`),
            sources: [...new Set(lines.flatMap((x) => (x.src ?? "DART").split("·")))].map((x) => (x === "DART" ? "OpenDART" : x === "FISIS" ? "금융감독원 금융통계정보시스템" : x)),
          })} />
          <button type="button" className={c.cardRemove} onClick={onRemove} aria-label={`${card.title} 차트 지우기`}>지우기</button>
        </span>
      </div>
      {ms.length === 0 ? <p className={c.empty}>이 기간에 이 지표가 없습니다.</p>
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
  const stale = !co || !!co.error || (co.price_checked_at ?? co.made_at ?? "").slice(0, 10) !== today;
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
  const sources = [
    "OpenDART",
    ...(menu === "overview" || menu === "peers" ? ["KRX"] : []),
    ...(fisisUsed ? ["금융감독원 금융통계정보시스템"] : []),
  ];
  return (
    <div className={c.page}>
      <Header co={co} refreshing={loading} failed={failed} />
      <div key={menu} className={c.content}>
        {menu === "overview" && <Overview co={co} />}
        {menu === "fin" && <Financial co={co} cardsUrl={reportId ? `/api/reports/${reportId}/charts` : `/api/stocks/${co.ticker}/charts`} />}
        {menu === "peers" && <><PeerView co={co} /><PeerTrend co={co} />{sec && <SectorPeers s={sec} />}</>}
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
