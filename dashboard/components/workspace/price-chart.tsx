"use client";

// 주가 추이 (2026-11-23, 기본 종목정보 > 기업개요 맨 위): 증권사 차트처럼 휠로 확대·축소, 끌어서 이동, 십자선.
// 그리는 도구: TradingView Lightweight Charts (Apache-2.0, 무료). 자료: 받아 둔 KRX 일별 시세(액면분할 보정)
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, CrosshairMode, HistogramSeries, LineSeries,
  type IChartApi, type MouseEventParams, type Time,
} from "lightweight-charts";
import c from "./company.module.css";

interface Row { d: string; o: number; h: number; l: number; c: number; v: number | null }
interface History { rows: Row[]; adjusted: boolean; first: string | null; error?: string }

const UP = "#d92d20", DOWN = "#1f62d6", LINE = "#1e3a5f";            // 한국식: 오르면 빨강, 내리면 파랑
const RANGES: [string, number | null][] = [["1개월", 21], ["3개월", 63], ["6개월", 126], ["1년", 250], ["전체", null]];
const day = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
const won = (v: number) => `${Math.round(v).toLocaleString("ko-KR")}`;

export function PriceChart({ ticker }: { ticker: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const [hist, setHist] = useState<History | null>(null);
  const [kind, setKind] = useState<"candle" | "line">("candle");
  const [range, setRange] = useState("6개월");
  const [hover, setHover] = useState<{ r: Row; x: number; w: number } | null>(null);   // 마우스를 올린 날 (상자 위치)

  useEffect(() => {                                     // 종목이 바뀌면 자료를 새로 받는다
    let off = false;
    fetch(`/api/stocks/${ticker}/history`).then((r) => r.json())
      .then((b: History) => { if (!off) setHist(b); })
      .catch(() => { if (!off) setHist({ rows: [], adjusted: false, first: null, error: "주가 자료를 받지 못했습니다." }); });
    return () => { off = true; };
  }, [ticker]);

  const rows = useMemo(() => hist?.rows ?? [], [hist]);
  useEffect(() => {                                     // 차트 만들기 (자료·종류가 바뀌면 다시)
    const el = box.current;
    if (!el || !rows.length) return;
    const ch = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#5c626b", fontSize: 11,
        fontFamily: "Pretendard Variable, Pretendard, 'Malgun Gothic', sans-serif", attributionLogo: false },
      grid: { vertLines: { color: "#f1f2f4" }, horzLines: { color: "#f1f2f4" } },
      rightPriceScale: { borderColor: "#e2e4e6", scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: "#e2e4e6", rightOffset: 3, minBarSpacing: 1.5 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR", priceFormatter: won, dateFormat: "yyyy.MM.dd" },
    });
    if (kind === "candle") {
      ch.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN })
        .setData(rows.map((r) => ({ time: day(r.d) as Time, open: r.o, high: r.h, low: r.l, close: r.c })));
    } else {
      ch.addSeries(LineSeries, { color: LINE, lineWidth: 2 }).setData(rows.map((r) => ({ time: day(r.d) as Time, value: r.c })));
    }
    const vol = ch.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(rows.map((r, i) => ({ time: day(r.d) as Time, value: r.v ?? 0,
      color: (i > 0 && r.c < rows[i - 1].c) ? "rgba(31,98,214,0.35)" : "rgba(217,45,32,0.35)" })));
    const byTime = new Map(rows.map((r) => [day(r.d), r]));
    const move = (p: MouseEventParams) => {
      const hit = p.time && p.point ? byTime.get(String(p.time)) : undefined;
      setHover(hit && p.point ? { r: hit, x: p.point.x, w: el.clientWidth } : null);
    };
    ch.subscribeCrosshairMove(move);
    chart.current = ch;
    return () => { ch.unsubscribeCrosshairMove(move); ch.remove(); chart.current = null; };
  }, [rows, kind]);

  useEffect(() => {                                     // 기간 버튼: 최근 n 거래일을 보이게 (휠·끌기로 더 조정)
    const ch = chart.current;
    if (!ch || !rows.length) return;
    const n = RANGES.find(([k]) => k === range)?.[1] ?? null;
    if (n == null || n >= rows.length) ch.timeScale().fitContent();
    else ch.timeScale().setVisibleLogicalRange({ from: rows.length - n, to: rows.length + 2 });
  }, [range, rows, kind]);

  if (!hist) return <section className={c.section}><div className={c.priceBox}><p className={c.empty}>주가 자료를 받는 중…</p></div></section>;
  if (hist.error || !rows.length) return null;
  const last = rows[rows.length - 1];
  const change = (x: Row) => { const i = rows.indexOf(x); const prev = i > 0 ? rows[i - 1].c : null; return prev ? ((x.c - prev) / prev) * 100 : null; };
  const r = last;
  const chg = change(r);
  const pct = (v: number | null) => v == null ? null : <span className={v > 0 ? c.pos : v < 0 ? c.neg : undefined}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>;
  const tip = hover && (() => {                          // 다른 차트처럼 마우스 옆 작은 상자 (오른쪽 끝에 가까우면 왼쪽으로)
    const h = hover.r, ch = change(h), left = hover.x + 16 + 190 > hover.w - 60 ? hover.x - 16 - 190 : hover.x + 16;
    return (
      <div className={`${c.tooltip} ${c.priceTip}`} style={{ left: Math.max(left, 0) }}>
        <strong>{`${h.d.slice(0, 4)}.${h.d.slice(4, 6)}.${h.d.slice(6)}`}</strong>
        <div className={c.tipRow}><span>시가</span><b>{won(h.o)}</b></div>
        <div className={c.tipRow}><span>고가</span><b>{won(h.h)}</b></div>
        <div className={c.tipRow}><span>저가</span><b>{won(h.l)}</b></div>
        <div className={c.tipRow}><span>종가</span><b>{won(h.c)}</b></div>
        {ch != null && <div className={c.tipRow}><span>전일 대비</span>{pct(ch)}</div>}
        {h.v != null && <div className={c.tipRow}><span>거래량</span><b>{Math.round(h.v).toLocaleString("ko-KR")}</b></div>}
      </div>
    );
  })();
  return (
    <section className={c.section}>
      <div className={c.sectionHead}>
        <h3>주가 추이<small>{rows.length.toLocaleString("ko-KR")}거래일{hist.adjusted ? ", 액면분할 보정" : ""}</small></h3>
        <div className={c.filters}>
          <div className={c.segment} role="group" aria-label="기간">
            {RANGES.map(([k]) => <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>{k}</button>)}
          </div>
          <div className={c.segment} role="group" aria-label="차트 종류">
            <button type="button" aria-pressed={kind === "candle"} onClick={() => setKind("candle")}>캔들</button>
            <button type="button" aria-pressed={kind === "line"} onClick={() => setKind("line")}>선</button>
          </div>
        </div>
      </div>
      <div className={c.priceLegend} aria-live="polite">
        <span>{`${r.d.slice(0, 4)}.${r.d.slice(4, 6)}.${r.d.slice(6)}`}</span>
        <span>시가 <b>{won(r.o)}</b></span><span>고가 <b>{won(r.h)}</b></span><span>저가 <b>{won(r.l)}</b></span>
        <span>종가 <b>{won(r.c)}</b></span>
        {pct(chg)}
        {r.v != null && <span>거래량 <b>{Math.round(r.v).toLocaleString("ko-KR")}</b></span>}
      </div>
      <div className={c.priceWrap}>
        <div ref={box} className={c.priceBox} />
        {tip}
      </div>
    </section>
  );
}
