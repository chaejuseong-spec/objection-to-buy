"use client";

// 주가 추이 (2026-11-23, 기본 종목정보 > 기업개요 맨 위): 증권사 차트처럼 휠로 확대·축소, 끌어서 이동, 십자선.
// 그리는 도구: TradingView Lightweight Charts (Apache-2.0, 무료). 자료: 토스증권 수정주가 일봉(1995년~), 못 받으면 KRX 일별 시세(액면분할 보정)
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, CrosshairMode, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type IPriceLine, type ISeriesApi, type MouseEventParams, type SeriesType, type Time,
} from "lightweight-charts";
import c from "./company.module.css";
import { downloadXlsx, type XlsxPayload } from "./explorer";

interface Row { d: string; o: number; h: number; l: number; c: number; v: number | null }
interface History { rows: Row[]; adjusted: boolean; first: string | null; source?: string; error?: string }

const UP = "#d92d20", DOWN = "#1f62d6", LINE = "#1e3a5f";            // 한국식: 오르면 빨강, 내리면 파랑
const RANGES: [string, number | null][] = [["1개월", 21], ["3개월", 63], ["6개월", 126], ["1년", 250], ["전체", null]];
export const day = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
// 이동평균선 (2026-11-24, 사용자): 종가 n일 단순 평균. 색은 차트 색과 겹치지 않게
const MAS: [number, string][] = [[5, "#f08c00"], [20, "#2f9e44"], [60, "#d6336c"], [120, "#212529"]];   // 주황·초록·자홍·검정 (캔들 빨강·파랑과도 구분)
// 그리기: 추세선(두 점), 가로선(한 점). 종목마다 이 브라우저에 저장
type Pt = { time: string; price: number };
type Drawing = { kind: "trend"; a: Pt; b: Pt } | { kind: "hline"; price: number };
const DRAW_KEY = (t: string) => `ws-drawings-${t}`;
/* 주가 자료: 차트와 투자 지표(현재 주가)가 같이 쓴다. 10분 지나면 다시 받는다 (서버도 토스 일봉을 10분마다 새로 받음) */
const histCache = new Map<string, { at: number; p: Promise<History> }>();
export function getHistory(ticker: string): Promise<History> {
  const h = histCache.get(ticker);
  if (h && Date.now() - h.at < 600_000) return h.p;
  const p = fetch(`/api/stocks/${ticker}/history`).then((r) => r.json() as Promise<History>)
    .catch(() => ({ rows: [], adjusted: false, first: null, error: "주가 자료를 받지 못했습니다." }));
  histCache.set(ticker, { at: Date.now(), p });
  return p;
}
/** 일별 수정주가 (오래된 날부터). 받는 중이면 null */
export function useHistoryRows(ticker: string): { d: string; c: number }[] | null {
  const [got, setGot] = useState<{ t: string; rows: { d: string; c: number }[] } | null>(null);
  useEffect(() => {
    let off = false;
    getHistory(ticker).then((b) => { if (!off) setGot({ t: ticker, rows: (b.rows ?? []).map((r) => ({ d: r.d, c: r.c })) }); });
    return () => { off = true; };
  }, [ticker]);
  return got?.t === ticker ? got.rows : null;
}
/** 최근 주가 (오늘 장중이면 지금 가격): { d: 'YYYYMMDD', c: 종가·현재가 } */
export function useLatestPrice(ticker: string): { d: string; c: number } | null {
  const [got, setGot] = useState<{ t: string; v: { d: string; c: number } | null } | null>(null);
  useEffect(() => {
    let off = false;
    getHistory(ticker).then((b) => { const r = b.rows?.[b.rows.length - 1]; if (!off) setGot({ t: ticker, v: r ? { d: r.d, c: r.c } : null }); });
    return () => { off = true; };
  }, [ticker]);
  return got?.t === ticker ? got.v : null;
}
const DRAW_COLOR = "#c2410c";
function sma(rows: Row[], n: number) {
  const out: { time: Time; value: number }[] = [];
  let sum = 0;
  rows.forEach((r, i) => {
    sum += r.c;
    if (i >= n) sum -= rows[i - n].c;
    if (i >= n - 1) out.push({ time: day(r.d) as Time, value: sum / n });
  });
  return out;
}
const won = (v: number) => `${Math.round(v).toLocaleString("ko-KR")}`;

export function PriceChart({ ticker, name }: { ticker: string; name?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const [hist, setHist] = useState<History | null>(null);
  const [kind, setKind] = useState<"candle" | "line">("candle");
  const [range, setRange] = useState("6개월");
  const [hover, setHover] = useState<{ r: Row; x: number; w: number } | null>(null);   // 마우스를 올린 날 (상자 위치)
  const main = useRef<ISeriesApi<SeriesType> | null>(null);
  const [ma, setMa] = useState<number[]>([20, 60]);
  const [tool, setTool] = useState<"trend" | "hline" | null>(null);
  const [pending, setPending] = useState<Pt | null>(null);          // 추세선 첫 점
  // 종목의 그림: 종목이 바뀌면 그리는 중에 바로 그 종목 것으로 (effect 안에서 상태를 바꾸지 않는다)
  const loadDraws = (t: string): Drawing[] => {
    try { return typeof window === "undefined" ? [] : JSON.parse(localStorage.getItem(DRAW_KEY(t)) ?? "[]"); } catch { return []; }
  };
  const [drawSt, setDrawSt] = useState(() => ({ t: ticker, list: loadDraws(ticker) }));
  if (drawSt.t !== ticker) setDrawSt({ t: ticker, list: loadDraws(ticker) });
  const draws = drawSt.list;
  const [ver, setVer] = useState(0);
  const [exp, setExp] = useState(false);
  const closeExp = useCallback(() => setExp(false), []);                                 // 차트를 새로 만들 때마다 올려 선들을 다시 붙인다
  const saveDraws = (next: Drawing[]) => {
    setDrawSt({ t: ticker, list: next });
    try { localStorage.setItem(DRAW_KEY(ticker), JSON.stringify(next)); } catch { /* 저장 못 해도 이번 화면에서는 보인다 */ }
  };

  useEffect(() => {                                     // 종목이 바뀌면 자료를 새로 받는다
    let off = false;
    getHistory(ticker).then((b) => { if (!off) setHist(b); });
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
      const cs = ch.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
      cs.setData(rows.map((r) => ({ time: day(r.d) as Time, open: r.o, high: r.h, low: r.l, close: r.c })));
      main.current = cs as ISeriesApi<SeriesType>;
    } else {
      const ls = ch.addSeries(LineSeries, { color: LINE, lineWidth: 2 });
      ls.setData(rows.map((r) => ({ time: day(r.d) as Time, value: r.c })));
      main.current = ls as ISeriesApi<SeriesType>;
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
    setVer((v) => v + 1);
    return () => { ch.unsubscribeCrosshairMove(move); chart.current = null; main.current = null; ch.remove(); };
  }, [rows, kind]);

  useEffect(() => {                                     // 이동평균선
    const ch = chart.current;
    if (!ch || !rows.length) return;
    const lines = ma.map((n) => {
      const ls = ch.addSeries(LineSeries, { color: MAS.find(([k]) => k === n)?.[1] ?? "#888", lineWidth: 2, lastValueVisible: false,
        priceLineVisible: false, crosshairMarkerVisible: false });
      ls.setData(sma(rows, n));
      return ls;
    });
    return () => { if (chart.current !== ch) return; lines.forEach((l) => { try { ch.removeSeries(l); } catch { /* 차트가 먼저 없어짐 */ } }); };
  }, [ma, rows, ver]);

  useEffect(() => {                                     // 그린 선 (추세선·가로선)
    const ch = chart.current, ms = main.current;
    if (!ch || !ms) return;
    const series: ISeriesApi<"Line">[] = [];
    const prices: IPriceLine[] = [];
    for (const d of draws) {
      if (d.kind === "hline") {
        prices.push(ms.createPriceLine({ price: d.price, color: DRAW_COLOR, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" }));
      } else if (d.a.time !== d.b.time) {
        const [p, q] = d.a.time < d.b.time ? [d.a, d.b] : [d.b, d.a];
        const ls = ch.addSeries(LineSeries, { color: DRAW_COLOR, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        ls.setData([{ time: p.time as Time, value: p.price }, { time: q.time as Time, value: q.price }]);
        series.push(ls);
      }
    }
    return () => {
      if (chart.current !== ch) return;                  // 차트를 새로 만들며 이미 지웠다
      prices.forEach((l) => { try { ms.removePriceLine(l); } catch { /* 없음 */ } });
      series.forEach((l) => { try { ch.removeSeries(l); } catch { /* 없음 */ } });
    };
  }, [draws, ver]);

  useEffect(() => {                                     // 그리기 도구: 차트를 눌러 점 찍기
    const ch = chart.current, ms = main.current;
    if (!ch || !ms || !tool) return;
    const click = (p: MouseEventParams) => {
      if (!p.point || !p.time) return;
      const price = ms.coordinateToPrice(p.point.y);
      if (price == null) return;
      const pt = { time: String(p.time), price: Math.round(price) };
      if (tool === "hline") { saveDraws([...draws, { kind: "hline", price: pt.price }]); setTool(null); return; }
      if (!pending) { setPending(pt); return; }
      saveDraws([...draws, { kind: "trend", a: pending, b: pt }]);
      setPending(null);
      setTool(null);
    };
    ch.subscribeClick(click);
    return () => { if (chart.current === ch) ch.unsubscribeClick(click); };
  }, [tool, pending, draws, ver]);   // eslint-disable-line react-hooks/exhaustive-deps

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
  // 엑셀 (2026-11-26 사용자): 창에서 고른 기간·날짜 간격. 시트 1 = 종가 차트, 시트 2 = 시가·고가·저가·종가·거래량 표
  const exportPrices = (o: PxOpts): XlsxPayload => {
    const inRange = o.range === "custom" ? rows.filter((x) => x.d >= o.from.replaceAll("-", "") && x.d <= o.to.replaceAll("-", ""))
      : o.range === "all" ? rows : rows.slice(-Number(o.range));
    const key = (d: string) => (o.step === "m" ? d.slice(0, 6) : weekKey(d));
    const sel = o.step === "d" ? inRange : inRange.filter((x, i) => i === inRange.length - 1 || key(inRange[i + 1].d) !== key(x.d));   // 그 주·그 달 마지막 거래일
    const periods = sel.map((x) => day(x.d));
    const basis = `${o.step === "d" ? "일별" : o.step === "w" ? "주말 값" : "월말 값"}, ${hist.source === "토스증권" ? "수정주가" : hist.adjusted ? "액면분할 보정" : "원 주가"}`;
    const src = [hist.source === "토스증권" ? "토스증권" : "KRX"];
    const title = `${name ?? ticker} 주가 ${sel.length ? `${day(sel[0].d)}~${day(sel[sel.length - 1].d)}` : ""}`.trim();
    return { title, periods: [], rows: [], sheets: [
      { title, sheet: "종가", ticker, basis, periods, sources: src, rows: [{ label: "종가", fmt: "krw", kind: "line", values: sel.map((x) => x.c) }] },
      { title: `${name ?? ticker} 일별 시세`, sheet: "일별 시세", ticker, basis, periods, sources: src, no_chart: true, rows: [
        { label: "시가", fmt: "krw", kind: "line", values: sel.map((x) => x.o) }, { label: "고가", fmt: "krw", kind: "line", values: sel.map((x) => x.h) },
        { label: "저가", fmt: "krw", kind: "line", values: sel.map((x) => x.l) }, { label: "종가", fmt: "krw", kind: "line", values: sel.map((x) => x.c) },
        { label: "거래량", fmt: "qty", kind: "line", values: sel.map((x) => x.v) }] },
    ] };
  };
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
        <h3>주가 추이</h3>
        <div className={c.filters}>
          <div className={c.segment} role="group" aria-label="기간">
            {RANGES.map(([k]) => <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>{k}</button>)}
          </div>
          <div className={c.segment} role="group" aria-label="차트 종류">
            <button type="button" aria-pressed={kind === "candle"} onClick={() => setKind("candle")}>캔들</button>
            <button type="button" aria-pressed={kind === "line"} onClick={() => setKind("line")}>선</button>
          </div>
        <div className={c.segment} role="group" aria-label="이동평균선">
          {MAS.map(([n, col]) => (
            <button key={n} type="button" aria-pressed={ma.includes(n)} onClick={() => setMa((m) => (m.includes(n) ? m.filter((x) => x !== n) : [...m, n]))}>
              <i className={c.maDot} style={{ background: col }} />{n}일
            </button>
          ))}
        </div>
        <div className={c.segment} role="group" aria-label="그리기">
          <button type="button" aria-pressed={tool === "trend"} onClick={() => { setTool(tool === "trend" ? null : "trend"); setPending(null); }}>추세선</button>
          <button type="button" aria-pressed={tool === "hline"} onClick={() => { setTool(tool === "hline" ? null : "hline"); setPending(null); }}>가로선</button>
          <button type="button" disabled={!draws.length} onClick={() => saveDraws(draws.slice(0, -1))}>되돌리기</button>
          <button type="button" disabled={!draws.length} onClick={() => saveDraws([])}>모두 지우기</button>
        </div>
          <button type="button" className={c.exportBtn} onClick={() => setExp(true)}>엑셀로 내보내기</button>
        </div>
      </div>
      {exp && <PriceExport first={rows[0].d} last={rows[rows.length - 1].d} onClose={closeExp} make={exportPrices} />}
      <div className={c.priceTools}>
        {tool && <small className={c.drawHint}>{tool === "hline" ? "차트에서 가격을 한 번 누르세요" : pending ? "두 번째 점을 누르세요" : "첫 번째 점을 누르세요"}</small>}
      </div>
      <div className={c.priceLegend} aria-live="polite">
        <span>{`${r.d.slice(0, 4)}.${r.d.slice(4, 6)}.${r.d.slice(6)}`}</span>
        <span>시가 <b>{won(r.o)}</b></span><span>고가 <b>{won(r.h)}</b></span><span>저가 <b>{won(r.l)}</b></span>
        <span>종가 <b>{won(r.c)}</b></span>
        {pct(chg)}
        {r.v != null && <span>거래량 <b>{Math.round(r.v).toLocaleString("ko-KR")}</b></span>}
      </div>
      <div className={c.priceWrap}>
        <div ref={box} className={c.priceBox} data-drawing={tool || undefined} />
        {tip}
      </div>
    </section>
  );
}

/* 주가 엑셀 창 (2026-11-26 사용자: 기간을 직접 고른다) */
export type PxOpts = { range: string; from: string; to: string; step: "d" | "w" | "m" };
const PX_RANGES: [string, string][] = [["21", "1개월"], ["63", "3개월"], ["126", "6개월"], ["250", "1년"], ["750", "3년"], ["1250", "5년"], ["all", "전체"], ["custom", "직접 입력"]];
export function weekKey(d: string) {                          // 그 주 월요일 날짜 (주말 값 = 그 주 마지막 거래일)
  const x = new Date(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8));
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return `${x.getFullYear()}${x.getMonth()}${x.getDate()}`;
}
export function PriceExport({ first, last, onClose, make, title = "주가 엑셀로 내보내기" }: { first: string; last: string; onClose: () => void; make: (o: PxOpts) => XlsxPayload; title?: string }) {
  const [o, setO] = useState<PxOpts>({ range: "250", from: day(first), to: day(last), step: "d" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>("input")?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const set = (x: Partial<PxOpts>) => setO((v) => ({ ...v, ...x }));
  const bad = o.range === "custom" && (!o.from || !o.to || o.from > o.to);
  const go = async () => {
    setBusy(true);
    const e = await downloadXlsx(make(o));
    setBusy(false);
    if (e) setErr(e); else onClose();
  };
  return (
    <div className={c.modalBack} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className={c.modal} role="dialog" aria-modal="true" aria-labelledby="px-title">
        <h4 id="px-title">{title}</h4>
        <fieldset><legend>기간</legend>
          <div className={c.modalRadios}>
            {PX_RANGES.map(([v, t]) => <label key={v}><input type="radio" name="px-range" checked={o.range === v} onChange={() => set({ range: v })} />{t}</label>)}
          </div>
          {o.range === "custom" && <div className={c.modalDates}>
            <input type="date" aria-label="시작일" min={day(first)} max={day(last)} value={o.from} onChange={(e) => set({ from: e.target.value })} />
            <span>~</span>
            <input type="date" aria-label="끝일" min={day(first)} max={day(last)} value={o.to} onChange={(e) => set({ to: e.target.value })} />
          </div>}
        </fieldset>
        <fieldset><legend>날짜 간격</legend>
          <div className={c.modalRadios}>
            {([["d", "일별"], ["w", "주말"], ["m", "월말"]] as const).map(([v, t]) => <label key={v}><input type="radio" name="px-step" checked={o.step === v} onChange={() => set({ step: v })} />{t}</label>)}
          </div>
          <small className={c.modalHint}>받은 자료: {day(first).replaceAll("-", ".")} ~ {day(last).replaceAll("-", ".")}</small>
        </fieldset>
        {err && <p className={c.warnCell}>{err}</p>}
        <div className={c.modalBtns}>
          <button type="button" className={c.modalCancel} onClick={onClose}>취소</button>
          <button type="button" className={c.exportBtn} disabled={busy || bad} onClick={go}>{busy ? "만드는 중" : "내려받기"}</button>
        </div>
      </div>
    </div>
  );
}
