"use client";

// 시장 화면 (2026-11-24, 사용자 4단계): 토스증권. 코스피·코스닥, 국채 금리 곡선·추이, 원·달러 환율,
// 시장 투자자별 순매수 대금(일별·월별), 섹터 등락률 순위. 10분 저장
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DayChart } from "./flows-view";
import { FinChart, FinTable, type Series } from "./fin-chart";
import { downloadXlsx, toRows, type XlsxPayload } from "./explorer";
import c from "./company.module.css";

interface Bar { d: string; o: number; h: number; l: number; c: number }
interface Amt { d: string; ind: number | null; frn: number | null; inst: number | null; corp: number | null; pension: number | null }
interface Rank { id: string; name: string; rate: number | null; mktcap: number | null }
interface Market {
  now: Record<string, number | null>; series: Record<string, Bar[]>;
  fx: { rate: number | null; mid: number | null; change: string | null; at: string | null };
  flows: Record<string, { "1d": Amt[]; "1mo": Amt[] }>; sector_ranks: Record<string, Rank[]>;
  sector_perf?: Record<string, { name: string } & Record<string, number | null | string>>;   // 섹터 × 기간 (순위에서 잘린 칸은 섹터 상세로 채움)
  fetched_at?: string; error?: string;
}

const C1 = "#2a78d6", C2 = "#1baf7a", OR = "#eb6834", OR2 = "#6250d6";
const BONDS: [string, string][] = [["KR_BOND_2Y", "2년"], ["KR_BOND_3Y", "3년"], ["KR_BOND_5Y", "5년"], ["KR_BOND_10Y", "10년"], ["KR_BOND_20Y", "20년"], ["KR_BOND_30Y", "30년"]];
const RANGES: [string, number][] = [["1개월", 21], ["3개월", 63], ["6개월", 126], ["1년", 250], ["전체", 100000]];   // 전체: 지수 2014-07부터, 국채 2019부터
const DUR: [string, string][] = [["1d", "1일"], ["1w", "1주"], ["1mo", "1개월"], ["3mo", "3개월"], ["6mo", "6개월"], ["1y", "1년"]];
const pt = (v: number) => v.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctTxt = (v: number) => `${v.toFixed(3)}%`;
const signed = (v: number | null | undefined, digits = 2, unit = "%") =>
  v == null ? "-" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}${unit}`;
const tone = (v: number | null | undefined) => (v == null ? undefined : v > 0 ? c.pos : v < 0 ? c.neg : undefined);
const dot = (d: string) => `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}`;

/** n 거래일 전 값 (없으면 null) */
const ago = (s: Bar[] | undefined, n: number) => (s && s.length > n ? s[s.length - 1 - n].c : null);

function Seg<T extends string | number>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className={c.segment} role="group" aria-label={label}>
      {options.map(([v, t]) => <button key={String(v)} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>{t}</button>)}
    </div>
  );
}

export function MarketView() {
  const [m, setM] = useState<Market | null>(null);
  const [idx, setIdx] = useState<"KOSPI" | "KOSDAQ">("KOSPI");
  const [range, setRange] = useState(250);
  const [bondRange, setBondRange] = useState(250);          // 국채 금리 추이 기간 (지수와 따로)
  const [flowMk, setFlowMk] = useState<"KOSPI" | "KOSDAQ">("KOSPI");
  const [flowIv, setFlowIv] = useState<"1d" | "1mo">("1d");
  const [dur, setDur] = useState("1d");
  const [allSecs, setAllSecs] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const closeExport = useCallback(() => setExportOpen(false), []);         // 창의 키 처리(useEffect)가 매번 다시 붙지 않게
  useEffect(() => {
    let off = false;
    fetch("/api/market").then((r) => r.json())
      .then((b: Market) => { if (!off) setM(b); })
      .catch(() => { if (!off) setM({ error: "시장 자료를 받지 못했습니다." } as Market); });
    return () => { off = true; };
  }, []);
  const idxRows = useMemo(() => (m?.series?.[idx] ?? []).slice(-range), [m, idx, range]);
  const bondDays = useMemo(() => [...new Set(BONDS.flatMap(([sym]) => (m?.series?.[sym] ?? []).map((b) => b.d)))].sort().map((d) => ({ d })), [m]);
  const bondRows = useMemo(() => {
    const by = new Map<string, Record<string, number | string>>();
    for (const [sym] of BONDS) for (const b of m?.series?.[sym] ?? []) by.set(b.d, { ...(by.get(b.d) ?? { d: b.d }), [sym]: b.c });
    return [...by.values()].sort((a, b) => String(a.d).localeCompare(String(b.d))).slice(-bondRange) as ({ d: string } & Record<string, number>)[];
  }, [m, bondRange]);
  if (!m) return <div className={c.page}><div className={c.notice}>시장 자료를 받는 중입니다 (10초 정도)…</div></div>;
  if (m.error) return <div className={c.page}><div className={c.notice}>{m.error}</div></div>;

  const tile = (label: string, sym: string, fmt: (v: number) => string, chgDigits = 2) => {
    const s = m.series[sym], now = m.now[sym] ?? (s?.length ? s[s.length - 1].c : null), prev = ago(s, 1);
    const isBond = sym.startsWith("KR_BOND");
    const chg = now != null && prev != null ? (isBond ? (now - prev) * 100 : (now / prev - 1) * 100) : null;
    return (
      <div key={sym}><dt>{label}</dt><dd>{now != null ? fmt(now) : "-"}</dd>
        <small className={tone(chg)}>{isBond ? signed(chg, 1, "bp") : signed(chg, chgDigits)}</small></div>
    );
  };
  // 국채 금리 곡선: 현재 / 1개월(21거래일) 전 / 1년(250거래일) 전
  const curve = (n: number) => BONDS.map(([sym]) => (n === 0 ? (m.now[sym] ?? ago(m.series[sym], 0)) : ago(m.series[sym], n)));
  const curveSeries: Series[] = [
    { key: "now", label: "현재", color: C1, kind: "line", fmt: "pct", values: curve(0) },
    { key: "m1", label: "1개월 전", color: OR, kind: "line", fmt: "pct", values: curve(21), dashed: true },
    { key: "y1", label: "1년 전", color: OR2, kind: "line", fmt: "pct", values: curve(250), dashed: true },
  ];
  const fl = (m.flows[flowMk]?.[flowIv] ?? []).slice(flowIv === "1d" ? -20 : -12);
  const flPeriods = fl.map((r) => (flowIv === "1d" ? `${r.d.slice(4, 6)}.${r.d.slice(6)}` : `${r.d.slice(2, 4)}.${r.d.slice(4, 6)}`));
  const flSeries: Series[] = [
    { key: "ind", label: "개인", color: C1, kind: "bar", fmt: "won", signed: true, values: fl.map((r) => r.ind) },
    { key: "frn", label: "외국인", color: OR, kind: "bar", fmt: "won", signed: true, values: fl.map((r) => r.frn) },
    { key: "inst", label: "기관", color: C2, kind: "bar", fmt: "won", signed: true, values: fl.map((r) => r.inst) },
  ];
  const tableSeries: Series[] = [...flSeries,
    { key: "pension", label: "연기금 (기관 중)", color: OR2, kind: "bar", fmt: "won", signed: true, values: fl.map((r) => r.pension) },
    { key: "corp", label: "기타법인", color: OR2, kind: "bar", fmt: "won", signed: true, values: fl.map((r) => r.corp) }];
  // 섹터 × 기간 표 (2026-11-25 사용자 'A'): 섹터마다 모든 기간을 한 줄에, 칸 색 = 오름(빨강)·내림(파랑) 정도 (그 기간 안에서 비교)
  const secs = new Map<string, { name: string; r: Record<string, number | null> }>();
  if (m.sector_perf) {
    for (const [id, e] of Object.entries(m.sector_perf)) secs.set(id, { name: e.name, r: Object.fromEntries(DUR.map(([k]) => [k, (e[k] as number | null) ?? null])) });
  } else {
    for (const [k] of DUR) for (const x of m.sector_ranks[k] ?? []) {
      const e = secs.get(x.id) ?? { name: x.name, r: {} };
      e.r[k] = x.rate;
      secs.set(x.id, e);
    }
  }
  const secRows = [...secs.entries()].filter(([, e]) => e.r[dur] != null).sort((a, b) => (b[1].r[dur] ?? 0) - (a[1].r[dur] ?? 0));
  const colMax = Object.fromEntries(DUR.map(([k]) => [k, Math.max(...secRows.map(([, e]) => Math.abs(e.r[k] ?? 0)), 0.01)]));
  const heat = (v: number | null | undefined, k: string) => {
    if (v == null) return undefined;
    const a = Math.min(Math.abs(v) / colMax[k], 1) * 0.35;
    return { background: v > 0 ? `rgba(224, 49, 49, ${a})` : v < 0 ? `rgba(42, 120, 214, ${a})` : undefined };
  };
  const shownSecs = allSecs ? secRows : secRows.slice(0, 20);
  const last = m.series.KOSPI?.[m.series.KOSPI.length - 1]?.d;
  // 엑셀 (2026-11-26 사용자): 지표 탐색과 같은 양식, 시트 여러 장. 누르면 작은 창에서 넣을 자료·기간·날짜 간격을 고른다
  const iso = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
  const makeExport = (o: ExportOpts): XlsxPayload => {
    const inRange = <T extends { d: string }>(rows: T[]) => {
      if (o.range === "custom") return rows.filter((r) => r.d >= o.from.replaceAll("-", "") && r.d <= o.to.replaceAll("-", ""));
      return rows.slice(-Number(o.range));
    };
    const step = <T extends { d: string }>(rows: T[]) => {
      if (o.step === "d") return rows;
      const key = (d: string) => (o.step === "m" ? d.slice(0, 6) : weekKey(d));
      return rows.filter((r, i) => i === rows.length - 1 || key(rows[i + 1].d) !== key(r.d));     // 그 주·그 달 마지막 거래일
    };
    const stepName = o.step === "d" ? "" : o.step === "w" ? ", 주말 값" : ", 월말 값";
    const ix = step(inRange((m.series.KOSPI ?? []).map((b) => ({ d: b.d }))));
    const at = (sym: string) => { const by = new Map((m.series[sym] ?? []).map((b) => [b.d, b.c])); return (rows: { d: string }[]) => rows.map((r) => by.get(r.d) ?? null); };
    const bd = step(inRange(bondDays));
    const fl2 = (m.flows[o.flowMk]?.[o.flowIv] ?? []).slice(o.flowIv === "1d" ? -20 : -12);
    const sheets: XlsxPayload[] = [];
    if (o.pick.idx) sheets.push({ title: "코스피·코스닥 지수", sheet: "지수", basis: `종가${stepName}`, periods: ix.map((r) => iso(r.d)),
      rows: [{ label: "코스피", group: "k", fmt: "x", kind: "line", values: at("KOSPI")(ix) }, { label: "코스닥", group: "q", fmt: "x", kind: "line", values: at("KOSDAQ")(ix) }] });
    if (o.pick.curve) sheets.push({ title: "국채 금리 곡선", sheet: "금리 곡선", basis: "만기별, 현재 / 21거래일 전 / 250거래일 전 종가", periods: BONDS.map(([, t]) => t), rows: toRows(curveSeries) });
    if (o.pick.bond) sheets.push({ title: "국채 3년·10년 금리 추이", sheet: "금리 추이", basis: `종가${stepName}`, periods: bd.map((r) => iso(r.d)),
      rows: [{ label: "국채 3년", fmt: "pct", kind: "line", values: at("KR_BOND_3Y")(bd) }, { label: "국채 10년", fmt: "pct", kind: "line", values: at("KR_BOND_10Y")(bd) }] });
    if (o.pick.flow) sheets.push({ title: `투자자별 순매수 대금 (${o.flowMk === "KOSPI" ? "코스피" : "코스닥"}, ${o.flowIv === "1d" ? "일별 20일" : "월별 12개월"})`, sheet: "순매수",
      together: true, basis: "매수 금액 − 매도 금액", periods: fl2.map((r) => (o.flowIv === "1d" ? iso(r.d) : `${r.d.slice(2, 4)}.${r.d.slice(4, 6)}월`)),
      rows: ([["개인", "ind"], ["외국인", "frn"], ["기관", "inst"], ["연기금 (기관 중)", "pension"], ["기타법인", "corp"]] as const).map(([label, k], i) =>
        ({ label, fmt: "won" as const, kind: i < 3 ? "bar" as const : "line" as const, values: fl2.map((r) => r[k]) })) });
    if (o.pick.sector) sheets.push({ title: "섹터 등락률", sheet: "섹터 등락률", basis: `토스 섹터, ${DUR.find(([k]) => k === dur)?.[1]} 순`, no_chart: true, periods: DUR.map(([, t]) => t),
      rows: secRows.map(([, e]) => ({ label: e.name, fmt: "pct" as const, kind: "line" as const, values: DUR.map(([k]) => e.r[k] ?? null) })) });
    return { title: `시장 ${last ? dot(last) : ""}`.trim(), periods: [], rows: [], sheets: sheets.map((x) => ({ ...x, sources: ["토스증권"] })) };
  };
  const firstDay = m.series.KOSPI?.[0]?.d ?? "20140701";
  return (
    <div className={c.page}>
      <div className={c.content}>
        <div className={c.sectionHead}><h3>시장<small>{last ? `${dot(last)} 기준` : ""}</small></h3><button type="button" className={c.exportBtn} onClick={() => setExportOpen(true)}>엑셀로 내보내기</button></div>
        {exportOpen && <ExportDialog first={firstDay} last={last ?? firstDay} init={{ range: String(range), flowMk, flowIv }}
                                     onClose={closeExport} make={makeExport} />}
        <dl className={c.mktTiles}>
          {tile("코스피", "KOSPI", pt)}
          {tile("코스닥", "KOSDAQ", pt)}
          <div><dt>원·달러 환율</dt><dd>{m.fx.rate != null ? `${m.fx.rate.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원` : "-"}</dd>
            <small>매매기준율 {m.fx.mid != null ? m.fx.mid.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : "-"}</small></div>
          {tile("국채 3년", "KR_BOND_3Y", pctTxt)}
          {tile("국채 10년", "KR_BOND_10Y", pctTxt)}
        </dl>

        <section className={c.section}>
          <div className={c.sectionHead}>
            <h3>지수</h3>
            <div className={c.filters}>
              <Seg label="지수" value={idx} onChange={setIdx} options={[["KOSPI", "코스피"], ["KOSDAQ", "코스닥"]]} />
              <Seg label="기간" value={range} onChange={setRange} options={RANGES.map(([t, n]) => [n, t] as [number, string])} />
            </div>
          </div>
          <DayChart rows={idxRows} fmt="x" format={pt} height={280} lines={[{ label: idx === "KOSPI" ? "코스피" : "코스닥", color: C1, get: (r: Bar) => r.c }]} />
        </section>

        <section className={c.section}>
          <div className={c.sectionHead}>
            <h3>국채 금리</h3>
            <div className={c.filters}>
              <Seg label="금리 추이 기간" value={bondRange} onChange={setBondRange} options={RANGES.map(([t, n]) => [n, t] as [number, string])} />
            </div>
          </div>
          <div className={c.cmpGrid}>
            <div><div className={c.chartUnit}>금리 곡선<small> % (만기별)</small></div>
              <FinChart periods={BONDS.map(([, t]) => t)} series={curveSeries} height={240} /></div>
            <div><div className={c.chartUnit}>3년·10년 추이<small> %</small></div>
              <DayChart rows={bondRows} fmt="pct" format={pctTxt} height={234} gapTop={20} lines={[
                { label: "3년", color: C1, get: (r) => r.KR_BOND_3Y }, { label: "10년", color: OR, get: (r) => r.KR_BOND_10Y }]} /></div>
          </div>
        </section>

        <section className={c.section}>
          <div className={c.sectionHead}>
            <h3>투자자별 순매수 대금</h3>
            <div className={c.filters}>
              <Seg label="시장" value={flowMk} onChange={setFlowMk} options={[["KOSPI", "코스피"], ["KOSDAQ", "코스닥"]]} />
              <Seg label="단위" value={flowIv} onChange={setFlowIv} options={[["1d", "일별 (20일)"], ["1mo", "월별 (12개월)"]]} />
            </div>
          </div>
          {fl.length ? <>
            <FinChart key={`${flowMk}${flowIv}`} periods={flPeriods} series={flSeries} height={240} />
            <FinTable periods={flPeriods} series={tableSeries} />
          </> : <p className={c.notes}>자료 없음</p>}
        </section>

        <section className={c.section}>
          <div className={c.sectionHead}><h3>섹터 등락률</h3></div>
          <div className={c.tableScroll}>
            <table className={c.finTable}>
              <thead><tr><th className={c.stick}>섹터</th>
                {DUR.map(([k, t]) => (
                  <th key={k} aria-sort={k === dur ? "descending" : undefined}>
                    <button type="button" className={c.sortBtn} aria-pressed={k === dur} onClick={() => setDur(k)}>{t}{k === dur ? " ▼" : ""}</button>
                  </th>))}
              </tr></thead>
              <tbody>
                {shownSecs.map(([id, e]) => (
                  <tr key={id}>
                    <th className={c.stick} scope="row">{e.name}</th>
                    {DUR.map(([k]) => <td key={k} className={tone(e.r[k])} style={heat(e.r[k], k)}>{signed(e.r[k])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {secRows.length > 20 && (
            <button type="button" className={c.peerPaneMore} aria-expanded={allSecs} onClick={() => setAllSecs((x) => !x)}>
              {allSecs ? "상위 20개만" : `${secRows.length}개 모두 보기`}
            </button>
          )}
        </section>

        <p className={c.source}>출처: 토스증권</p>
      </div>
    </div>
  );
}

/* ───────── 엑셀 내보내기 창 (2026-11-26 사용자: 어떤 자료를, 며칠부터 며칠까지, 어떤 간격으로) ───────── */
interface ExportOpts {
  pick: { idx: boolean; curve: boolean; bond: boolean; flow: boolean; sector: boolean };
  range: string; from: string; to: string; step: "d" | "w" | "m";
  flowMk: "KOSPI" | "KOSDAQ"; flowIv: "1d" | "1mo";
}
/** 주 구분 열쇠: 그 주 월요일 날짜 */
function weekKey(d: string): string {
  const t = new Date(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6)));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));
  return t.toISOString().slice(0, 10);
}
const EX_RANGES: [string, string][] = [["21", "1개월"], ["63", "3개월"], ["126", "6개월"], ["250", "1년"], ["750", "3년"], ["100000", "전체"], ["custom", "직접 입력"]];
const PICKS: [keyof ExportOpts["pick"], string][] = [["idx", "지수 (코스피·코스닥)"], ["curve", "국채 금리 곡선"], ["bond", "국채 3년·10년 추이"], ["flow", "투자자별 순매수"], ["sector", "섹터 등락률"]];

function ExportDialog({ first, last, init, onClose, make }: { first: string; last: string; init: { range: string; flowMk: "KOSPI" | "KOSDAQ"; flowIv: "1d" | "1mo" };
  onClose: () => void; make: (o: ExportOpts) => XlsxPayload }) {
  const iso = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
  const [o, setO] = useState<ExportOpts>({ pick: { idx: false, curve: false, bond: false, flow: false, sector: false },
    range: EX_RANGES.some(([v]) => v === init.range) ? init.range : "250", from: iso(first), to: iso(last), step: "d", flowMk: init.flowMk, flowIv: init.flowIv });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>("input, button")?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const none = !Object.values(o.pick).some(Boolean);
  const badDates = o.range === "custom" && (!o.from || !o.to || o.from > o.to);
  const go = async () => {
    setBusy(true);
    const e = await downloadXlsx(make(o));
    setBusy(false);
    if (e) setErr(e); else onClose();
  };
  const set = (x: Partial<ExportOpts>) => setO((v) => ({ ...v, ...x }));
  return (
    <div className={c.modalBack} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className={c.modal} role="dialog" aria-modal="true" aria-labelledby="mx-title">
        <h4 id="mx-title">엑셀로 내보내기</h4>
        <fieldset><legend>넣을 자료</legend>
          {PICKS.map(([k, t]) => (
            <label key={k} className={c.modalCheck}>
              <input type="checkbox" checked={o.pick[k]} onChange={(e) => set({ pick: { ...o.pick, [k]: e.target.checked } })} />{t}
              {k === "flow" && o.pick.flow && <span className={c.modalInline}>
                <select aria-label="순매수 시장" value={o.flowMk} onChange={(e) => set({ flowMk: e.target.value as ExportOpts["flowMk"] })}><option value="KOSPI">코스피</option><option value="KOSDAQ">코스닥</option></select>
                <select aria-label="순매수 단위" value={o.flowIv} onChange={(e) => set({ flowIv: e.target.value as ExportOpts["flowIv"] })}><option value="1d">일별 20일</option><option value="1mo">월별 12개월</option></select>
              </span>}
            </label>
          ))}
        </fieldset>
        <fieldset disabled={!o.pick.idx && !o.pick.bond}><legend>기간 (지수·금리 추이)</legend>
          <div className={c.modalRadios}>
            {EX_RANGES.map(([v, t]) => (
              <label key={v}><input type="radio" name="mx-range" checked={o.range === v} onChange={() => set({ range: v })} />{t}</label>
            ))}
          </div>
          {o.range === "custom" && <div className={c.modalDates}>
            <input type="date" aria-label="시작일" min={iso(first)} max={iso(last)} value={o.from} onChange={(e) => set({ from: e.target.value })} />
            <span>~</span>
            <input type="date" aria-label="끝일" min={iso(first)} max={iso(last)} value={o.to} onChange={(e) => set({ to: e.target.value })} />
          </div>}
          <div className={c.modalRadios}>
            <span>날짜 간격</span>
            {([["d", "일별"], ["w", "주말"], ["m", "월말"]] as const).map(([v, t]) => (
              <label key={v}><input type="radio" name="mx-step" checked={o.step === v} onChange={() => set({ step: v })} />{t}</label>
            ))}
          </div>
          <small className={c.modalHint}>받은 자료: 지수 {iso(first).slice(0, 7).replace("-", ".")}부터, 국채 2019.01부터</small>
        </fieldset>
        {err && <p className={c.warnCell}>{err}</p>}
        <div className={c.modalBtns}>
          <button type="button" className={c.modalCancel} onClick={onClose}>취소</button>
          <button type="button" className={c.exportBtn} disabled={busy || none || badDates} onClick={go}>{busy ? "만드는 중" : "내려받기"}</button>
        </div>
      </div>
    </div>
  );
}
