"use client";

// 수급 (2026-11-24, 사용자 3단계): 토스증권 일별 자료 1년. 투자자별 매매동향·프로그램매매·공매도·신용·대차, 매수 유의사항.
// 하루 단위 자료라 주가 차트와 같은 도구(TradingView Lightweight Charts)로 그린다: 휠 확대·끌어서 이동·십자선.
// 한 차트에는 단위가 같은 값만 (축 하나). 2개 넘는 선은 범례를 늘 보인다.
import { useEffect, useMemo, useRef, useState } from "react";
import { ColorType, createChart, CrosshairMode, LineSeries, type IChartApi, type MouseEventParams, type Time } from "lightweight-charts";
import c from "./company.module.css";
import { FinTable, fmtVal, type Fmt, type Series } from "./fin-chart";

interface FlowRow {
  d: string;
  ind?: number | null; frn?: number | null; inst?: number | null; corp?: number | null;
  brk?: Record<string, number | null> | null; fhold?: number | null;
  arb?: number | null; nonarb?: number | null;
  short_vol?: number | null; short_amt?: number | null; short_rate?: number | null;
  margin_bal?: number | null; margin_rate?: number | null; sloan_bal?: number | null;
  lend_bal?: number | null; lend_amt?: number | null;
}
interface Flows { rows: FlowRow[]; warnings: { type: string; exchange: string | null; start: string | null; end: string | null }[]; fetched_at?: string; error?: string }

// 색: 차트 공통 색(색약 검사 통과 조합)과 같은 순서
const C1 = "#2a78d6", C2 = "#1baf7a", OR = "#eb6834", OR2 = "#6250d6";
const RANGES: [string, number][] = [["1개월", 21], ["3개월", 63], ["6개월", 126], ["1년", 250]];
const WARN: Record<string, string> = {
  LIQUIDATION_TRADING: "정리매매", OVERHEATED: "단기과열종목", INVESTMENT_WARNING: "투자경고종목", INVESTMENT_RISK: "투자위험종목",
  VI_STATIC_AND_DYNAMIC: "VI 발동(정적·동적)", VI_STATIC: "VI 발동(정적)", VI_DYNAMIC: "VI 발동(동적)", STOCK_WARRANTS: "신주인수권",
};
const BRK: [string, string][] = [["financialInvestment", "금융투자"], ["insurance", "보험"], ["trust", "투신"], ["privateEquityFund", "사모"],
  ["bank", "은행"], ["otherFinancialInstitution", "기타금융"], ["pensionFund", "연기금"]];
const iso = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
const dot = (d: string) => `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}`;

/** 축 눈금: 주·원은 만·억·조로 줄인다 */
function short(v: number, f: Fmt): string {
  if (f === "pct") return `${+v.toFixed(Math.abs(v) < 10 ? 2 : 1)}%`;
  const a = Math.abs(v), u = f === "won" ? "원" : "주";
  if (a >= 1e12) return `${+(v / 1e12).toFixed(2)}조${u}`;
  if (a >= 1e8) return `${+(v / 1e8).toFixed(1)}억${u}`;
  if (a >= 1e4) return `${+(v / 1e4).toFixed(1)}만${u}`;
  return `${Math.round(v).toLocaleString("ko-KR")}${u}`;
}

export interface Line<R = FlowRow> { label: string; color: string; get: (r: R) => number | null | undefined }

/** 하루 단위 선 차트. cum 이면 고른 기간 첫날부터 누적 (빈 날은 더하지 않음) */
export function DayChart<R extends { d: string }>({ rows, lines, fmt, cum, height = 220, format, gapTop = 0, info, tip }: { rows: R[]; lines: Line<R>[]; fmt: Fmt; cum?: boolean; height?: number; format?: (v: number) => string; gapTop?: number; info?: (d: string) => string | null;
  tip?: (d: string) => [string, string][] | null }) {   // tip: 주가 차트처럼 마우스 옆 작은 상자 (위 범례는 최근 값 그대로)
  const show = format ?? ((v: number) => short(v, fmt));
  const lineKey = lines.map((l) => `${l.label}|${l.color}`).join();      // 화면이 다시 그려져도(마우스 이동) 차트를 새로 만들지 않게
  const box = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [at, setAt] = useState<{ x: number; w: number } | null>(null);
  const data = useMemo(() => lines.map((ln) => {
    let s = 0;
    const out: { time: Time; value: number; d: string }[] = [];
    for (const r of rows) {
      const v = ln.get(r);
      if (v == null || !Number.isFinite(v)) continue;
      s = cum ? s + v : v;
      out.push({ time: iso(r.d) as Time, value: s, d: r.d });
    }
    return out;
  }), [rows, lineKey, cum, fmt]);   // eslint-disable-line react-hooks/exhaustive-deps -- lines 는 매번 새 배열이라 lineKey 로
  useEffect(() => {
    const el = box.current;
    if (!el || !data.some((x) => x.length)) return;
    const ch: IChartApi = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#5c626b", fontSize: 11,
        fontFamily: "Pretendard Variable, Pretendard, 'Malgun Gothic', sans-serif", attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: "#f1f2f4" } },
      rightPriceScale: { borderColor: "#e2e4e6" },
      timeScale: { borderColor: "#e2e4e6", rightOffset: 2, minBarSpacing: 0.05 },   // 전체 기간(지수 3천 거래일)도 한 화면에
      crosshair: { mode: CrosshairMode.Magnet },
      localization: { locale: "ko-KR", priceFormatter: show, dateFormat: "yyyy.MM.dd" },
    });
    data.forEach((pts, i) => {
      const s = ch.addSeries(LineSeries, { color: lines[i].color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      s.setData(pts.map(({ time, value }) => ({ time, value })));
    });
    ch.timeScale().fitContent();
    const move = (p: MouseEventParams) => {
      setHover(p.time ? String(p.time).replaceAll("-", "") : null);
      setAt(p.time && p.point ? { x: p.point.x, w: el.clientWidth } : null);
    };
    ch.subscribeCrosshairMove(move);
    return () => { ch.unsubscribeCrosshairMove(move); ch.remove(); };
  }, [data]);   // eslint-disable-line react-hooks/exhaustive-deps -- 선 색·숫자 모양은 lineKey·fmt 로 data 에 묶여 있다
  if (!data.some((x) => x.length)) return <p className={c.notes}>이 기간 자료 없음</p>;
  return (
    <>
      <div className={c.priceLegend} aria-live="polite">
        {lines.map((ln, i) => {
          const pts = data[i];
          const pt = (hover && !tip ? pts.find((p) => p.d === hover) : pts[pts.length - 1]) ?? null;
          return <span key={ln.label}><i className={c.maDot} style={{ background: ln.color }} />{ln.label} <b>{pt ? show(pt.value) : "-"}</b></span>;
        })}
        <span>{hover && !tip ? dot(hover) : data[0].length ? dot(data[0][data[0].length - 1].d) : ""}</span>
        {info && (() => { const d = (tip ? null : hover) ?? data[0][data[0].length - 1]?.d; const x = d ? info(d) : null; return x ? <span>{x}</span> : null; })()}
      </div>
      <div style={{ position: "relative" }}>
        <div ref={box} className={c.flowBox} style={{ height, marginTop: gapTop || undefined }} />   {/* gapTop: 옆 차트와 위아래 맞춤 */}
        {tip && hover && at && (() => {
          const rowsT = tip(hover);
          if (!rowsT) return null;
          const left = at.x + 16 + 190 > at.w - 50 ? at.x - 16 - 190 : at.x + 16;     // 오른쪽 끝에 가까우면 왼쪽으로
          return (
            <div className={`${c.tooltip} ${c.priceTip}`} style={{ left: Math.max(left, 0) }}>
              <strong>{dot(hover)}</strong>
              {rowsT.map(([k, v]) => <div key={k} className={c.tipRow}><span>{k}</span><b>{v}</b></div>)}
            </div>
          );
        })()}
      </div>
    </>
  );
}

export function FlowsView({ ticker }: { ticker: string }) {
  const [res, setRes] = useState<{ ticker: string; body: Flows } | null>(null);
  const [range, setRange] = useState(63);
  useEffect(() => {
    let off = false;
    fetch(`/api/stocks/${ticker}/flows`).then((r) => r.json())
      .then((b: Flows) => { if (!off) setRes({ ticker, body: b }); })
      .catch(() => { if (!off) setRes({ ticker, body: { rows: [], warnings: [], error: "수급 자료를 받지 못했습니다." } }); });
    return () => { off = true; };
  }, [ticker]);
  const body = res?.ticker === ticker ? res.body : null;
  const rows = useMemo(() => (body?.rows ?? []).slice(-range), [body, range]);
  const brk = useMemo(() => BRK.map(([k, name]) => {
    const vs = rows.map((r) => r.brk?.[k]).filter((v): v is number => v != null);
    return { name, v: vs.length ? vs.reduce((a, b) => a + b, 0) : null };
  }), [rows]);
  if (!body) return <div className={c.notice}>수급 자료를 받는 중입니다 (처음에는 30초 정도)…</div>;
  if (body.error || !body.rows?.length) return <div className={c.notice}>{body.error || "수급 자료가 없습니다."}</div>;
  const last = rows[rows.length - 1];
  const recent = rows.slice(-20);
  const periods = recent.map((r) => `${r.d.slice(4, 6)}.${r.d.slice(6)}`);
  const ser = (key: string, label: string, fmt: Fmt, get: (r: FlowRow) => number | null | undefined, signed = false): Series =>
    ({ key, label, color: C1, kind: "line", fmt, signed, src: "토스증권", values: recent.map((r) => get(r) ?? null) });
  const table: Series[] = [
    ser("ind", "개인 순매수", "qty", (r) => r.ind, true), ser("frn", "외국인 순매수", "qty", (r) => r.frn, true),
    ser("inst", "기관 순매수", "qty", (r) => r.inst, true), ser("corp", "기타법인 순매수", "qty", (r) => r.corp, true),
    ser("fhold", "외국인 보유율", "pct", (r) => r.fhold), ser("prog", "프로그램 순매수", "qty", (r) => (r.arb == null && r.nonarb == null ? null : (r.arb ?? 0) + (r.nonarb ?? 0)), true),
    ser("short", "공매도 수량", "qty", (r) => r.short_vol), ser("short_rate", "공매도 비중", "pct", (r) => r.short_rate),
    ser("margin", "신용융자 잔고", "qty", (r) => r.margin_bal), ser("lend", "대차 잔고", "qty", (r) => r.lend_bal),
  ];
  return (
    <>
      <section className={c.section}>
        <div className={c.sectionHead}>
          <h3>투자자별 누적 순매수<small>{dot(rows[0].d)} ~ {dot(last.d)}, {rows.length}거래일</small></h3>
          <div className={c.filters}>
            <div className={c.segment} role="group" aria-label="기간">
              {RANGES.map(([k, n]) => <button key={k} type="button" aria-pressed={range === n} onClick={() => setRange(n)}>{k}</button>)}
            </div>
          </div>
        </div>
        {body.warnings.length > 0 && (
          <div className={c.warnRow}>
            {body.warnings.map((w, i) => (
              <span key={i} className={c.warnBadge}>
                {WARN[w.type] ?? w.type}{w.exchange ? ` (${w.exchange})` : ""}
                {(w.start || w.end) && <small>{w.start ?? ""} ~ {w.end ?? "진행 중"}</small>}
              </span>
            ))}
          </div>
        )}
        <DayChart rows={rows} fmt="qty" cum height={280} lines={[
          { label: "개인", color: C1, get: (r) => r.ind }, { label: "외국인", color: OR, get: (r) => r.frn },
          { label: "기관", color: C2, get: (r) => r.inst }, { label: "기타법인", color: OR2, get: (r) => r.corp },
        ]} />
        <div className={c.tableScroll}>
          <table className={c.finTable}>
            <thead><tr><th className={c.stick}>기관 세부 (기간 합계)</th>{brk.map((b) => <th key={b.name}>{b.name}</th>)}</tr></thead>
            <tbody><tr><td className={c.stick}>순매수</td>{brk.map((b) => (
              <td key={b.name} className={b.v != null && b.v > 0 ? c.pos : b.v != null && b.v < 0 ? c.neg : undefined}>{fmtVal(b.v, "qty")}</td>))}</tr></tbody>
          </table>
        </div>
      </section>
      <section className={c.section}>
        <div className={c.sectionHead}><h3>보유·대차·신용·공매도</h3></div>
        <div className={c.cmpGrid}>
          <div><div className={c.chartUnit}>외국인 보유율<small> %</small></div><DayChart rows={rows} fmt="pct" lines={[{ label: "외국인 보유율", color: OR, get: (r) => r.fhold }]} /></div>
          <div><div className={c.chartUnit}>공매도 거래량 비중<small> %</small></div><DayChart rows={rows} fmt="pct" lines={[{ label: "공매도 비중", color: C1, get: (r) => r.short_rate }]} /></div>
          <div><div className={c.chartUnit}>신용융자 잔고<small> 주</small></div><DayChart rows={rows} fmt="qty" lines={[{ label: "신용융자 잔고", color: C2, get: (r) => r.margin_bal }]} /></div>
          <div><div className={c.chartUnit}>대차 잔고 금액<small> 원</small></div><DayChart rows={rows} fmt="won" lines={[{ label: "대차 잔고 금액", color: OR2, get: (r) => r.lend_amt }]} /></div>
          <div><div className={c.chartUnit}>프로그램 누적 순매수<small> 주</small></div><DayChart rows={rows} fmt="qty" cum lines={[
            { label: "차익", color: C1, get: (r) => r.arb }, { label: "비차익", color: OR, get: (r) => r.nonarb }]} /></div>
        </div>
      </section>
      <section className={c.section}>
        <div className={c.sectionHead}><h3>최근 20거래일</h3></div>
        <FinTable periods={periods} series={table} />
        <ul className={c.notes}>
          <li>순매수: 매수 수량 − 매도 수량 (주). 누적은 고른 기간 첫날부터 더한 값</li>
          <li>외국인: 등록 외국인. 보유율 = 외국인 보유 수량 ÷ 상장주식수</li>
          <li>기관 세부: 금융투자·보험·투신·사모·은행·기타금융·연기금</li>
          <li>공매도 비중: 그날 전체 거래량 중 공매도 거래량</li>
          <li>신용융자 잔고: 돈을 빌려 사고 아직 갚지 않은 주식 수</li>
          <li>대차: 기관끼리 주식을 빌려준 것 (공매도 재료). 신용대주(개인이 빌려 판 주식)와 다름</li>
          <li>프로그램매매: 차익 = 선물과 현물 가격 차이를 노린 매매, 비차익 = 여러 종목을 한꺼번에 사고파는 매매</li>
          <li>당일 기록은 장중 잠정치 (개인·기타법인·기관 세부는 저녁에 채워짐)</li>
        </ul>
      </section>
    </>
  );
}
