"use client";

// 지표 탐색 (2026-11-22, 기본 종목정보 > 재무): 왼쪽에서 지표를 찾아 체크하면 오른쪽에 차트·표가 그려진다.
// 단위가 다른 지표는 위아래 차트로 나뉜다(FinCharts). 값은 모두 부모가 공시 데이터로 만든 것 그대로 (여기서 숫자를 만들지 않는다, 증감만 계산)
import { useMemo, useState, type ReactNode } from "react";
import { FinChart, FinCharts, FinTable, fmtVal, UNIT, type Series } from "./fin-chart";
import c from "./company.module.css";

export interface Metric extends Omit<Series, "color"> {
  cat: string;                 // 목록 무리 (수익성, 보험, 손익계산서 계정 …)
  note?: string;               // 정의·계산식 한 줄
}
export interface Cat { name: string; open: boolean }

// 색약 검사 통과 순서 (dataviz validate_palette). 지표를 고를 때 빈 자리 색을 받고, 고른 동안 바뀌지 않는다
const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#eda100"];
const MAX = 6;

/** 엑셀 내보내기 (2026-11-22): 화면의 숫자를 그대로 보내 서식 규칙대로 만든 파일을 받는다 */
export interface XlsxRow { label: string; group?: string; legend?: string; dashed?: boolean; fmt: Series["fmt"]; kind: Series["kind"]; values: (number | null)[]; calc?: boolean; src?: string; strong?: boolean }
export interface XlsxPayload { title: string; ticker?: string; together?: boolean; sheet?: string; basis?: string; periods: string[]; rows: XlsxRow[]; notes?: string[]; sources?: string[] }
export async function downloadXlsx(payload: XlsxPayload): Promise<string | null> {
  try {
    const r = await fetch("/api/export/xlsx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!r.ok) return ((await r.json().catch(() => null)) as { error?: string } | null)?.error ?? "엑셀 파일을 만들지 못했습니다.";
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${payload.title.replace(/[\\/:*?"<>|]/g, "_")}.xlsx`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return null;
  } catch {
    return "엑셀 파일을 만들지 못했습니다.";
  }
}
export const toRows = (series: Series[], strongKey?: string): XlsxRow[] =>
  series.map((s) => ({ label: s.label, fmt: s.fmt, kind: s.kind, values: s.values, calc: s.calc, src: s.src, strong: s.key === strongKey, dashed: s.dashed }));

/** 내보내기 버튼: 누르는 동안 '만드는 중', 실패하면 이유 */
export function ExportButton({ make }: { make: () => XlsxPayload | null }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    const p = make();
    if (!p) return;
    setBusy(true);
    setErr(await downloadXlsx(p));
    setBusy(false);
  };
  return (
    <span className={c.exportWrap}>
      <button type="button" className={c.exportBtn} onClick={run} disabled={busy}>{busy ? "만드는 중" : "엑셀로 내보내기"}</button>
      {err && <small className={c.warnCell}>{err}</small>}
    </span>
  );
}

export function MetricExplorer({ metrics, cats, periods, defaults, controls, back, onExport, me, peerPane, compare, onSave }: {
  metrics: Metric[]; cats: Cat[]; periods: string[]; defaults: string[]; controls: ReactNode;
  onExport?: (series: Series[], focus?: string, cmpRows?: XlsxRow[], together?: boolean) => XlsxPayload | null;
  me?: { name: string; color: string };                // 동종사 비교 때 이 회사 선
  peerPane?: ReactNode;                                 // 왼쪽 칸 맨 위: 비교 회사 체크
  compare?: (m: Metric) => Series[];                    // 고른 동종사의 그 지표 선들
  onSave?: (keys: string[], together: boolean) => void;  // 차트 만들기: 지금 조합을 카드로 저장
  back: number;                // 전년 같은 기간이 몇 칸 앞인가 (분기 4, 연간 1)
}) {
  const [picked, setPicked] = useState<[string, number][]>(() => defaults.slice(0, MAX).map((k, i) => [k, i]));
  const [focus, setFocus] = useState<string | undefined>(defaults[0]);
  const [q, setQ] = useState("");
  const [fold, setFold] = useState<Record<string, boolean>>({});   // 직접 열고 닫은 묶음 (없으면: 고른 지표가 있을 때만 열림)
  const [together, setTogether] = useState(false);              // 함께 보기: 한 차트에 (단위 2가지까지 좌우 축)
  const byKey = useMemo(() => new Map(metrics.map((m) => [m.key, m])), [metrics]);
  const live = picked.filter(([k]) => byKey.has(k));           // 연간·분기를 바꾸면 그 모드에 없는 지표는 잠시 빠진다
  const series: Series[] = live.map(([k, slot]) => ({ ...byKey.get(k)!, color: PALETTE[slot] }));
  const isOn = (k: string) => picked.some(([x]) => x === k);
  const toggle = (k: string) => {
    if (isOn(k)) {
      const rest = picked.filter(([x]) => x !== k);
      setPicked(rest);
      if (focus === k) setFocus(rest[0]?.[0]);
      return;
    }
    if (picked.length >= MAX) return;
    const used = new Set(picked.map(([, s]) => s));
    setPicked([...picked, [k, PALETTE.findIndex((_, i) => !used.has(i))]]);
    setFocus(k);
  };
  const focusOn = (k: string) => { if (!isOn(k)) toggle(k); else setFocus(k); };

  const query = q.trim().toLowerCase();
  const shown = metrics.filter((m) => !query || m.label.toLowerCase().includes(query) || m.cat.toLowerCase().includes(query));
  const f = byKey.get(focus ?? "") && live.some(([k]) => k === focus) ? byKey.get(focus!)! : (live[0] ? byKey.get(live[0][0]) : undefined);
  // 동종사를 고르면: 지표마다 차트 하나, 그 안에 이 회사(네이비 굵은 선)와 동종사 선. 회사 색은 모든 차트에서 같다
  const cmp = compare && me ? series.map((s) => ({ s, peers: compare(byKey.get(s.key)!) })) : null;
  const comparing = !!cmp && cmp.some((x) => x.peers.length > 0);
  const meLine = (s: Series): Series => ({ ...s, key: `${s.key}|me`, label: me!.name, color: me!.color, strong: true });
  const cmpTable: Series[] = comparing ? cmp!.flatMap(({ s, peers }) => [
    { ...meLine(s), label: `${s.label} · ${me!.name}` },
    ...peers.map((x) => ({ ...x, key: `${s.key}|${x.key}`, label: `${s.label} · ${x.label}` })),
  ]) : [];
  // 함께 보기 가능 조건: 단위 2가지 이하, 동종사 비교 중이면 지표 2개 이하
  const units = new Set(series.map((x) => x.fmt)).size;
  const canTogether = series.length > 1 && units <= 2 && (!comparing || series.length <= 2);
  const why = units > 2 ? "단위가 3가지 이상이라 함께 볼 수 없습니다 (축은 왼쪽·오른쪽 2개까지)"
    : comparing && series.length > 2 ? "동종사 비교 중에는 지표 2개까지 함께 볼 수 있습니다" : series.length < 2 ? "지표를 2개 이상 고르세요" : "";
  const joined = together && canTogether;
  // 동종사 비교 + 함께 보기: 회사 색은 그대로, 두 번째 지표는 점선
  const joinedCmp: Series[] = joined && comparing ? cmp!.flatMap(({ s, peers }, k) =>
    [meLine(s), ...peers.filter((x) => x.values.some((v) => v != null))].map((x) => ({
      ...x, key: `${s.key}|${x.key}`, label: `${x.label} ${s.label}`, dashed: k === 1 && s.kind === "line" }))) : [];
  const cmpRows = (): XlsxRow[] => cmp!.flatMap(({ s, peers }, k) => [meLine(s), ...peers.map((x) => ({ ...x, key: `${s.key}|${x.key}` }))].map((x) => ({
    label: `${s.label} · ${x.label}`, legend: joined ? `${x.label} ${s.label}` : x.label, group: s.key, fmt: x.fmt, kind: x.kind, values: x.values,
    calc: x.calc, src: x.src, strong: !!x.strong, dashed: joined && k === 1 && s.kind === "line" })));

  return (
    <div className={c.explorer}>
      <aside className={c.metricPane} aria-label="지표 목록">
        {peerPane}
        <input className={c.metricSearch} type="search" placeholder="지표 검색 (예: CSM, 부채, ROE)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="지표 검색" />
        <div className={c.metricCount}>{picked.length}/{MAX}개 선택{picked.length > 0 && <button type="button" onClick={() => { setPicked([]); setFocus(undefined); }}>모두 해제</button>}</div>
        <div className={c.metricList}>
          {cats.map((cat) => {
            const items = shown.filter((m) => m.cat === cat.name);
            if (!items.length) return null;
            const open = query ? true : (fold[cat.name] ?? (cat.open || items.some((m) => isOn(m.key))));
            return (
              <div key={cat.name} className={c.metricCat}>
                <button type="button" className={c.metricCatHead} aria-expanded={open} onClick={() => setFold((s) => ({ ...s, [cat.name]: !open }))}>
                  <span>{cat.name}</span><small>{items.length}</small>
                </button>
                {open && items.map((m) => {
                  const on = isOn(m.key);
                  const slot = picked.find(([k]) => k === m.key)?.[1];
                  const latest = [...m.values].reverse().find((v) => v != null);
                  return (
                    <div key={m.key} className={c.metricItem} data-on={on || undefined} data-focus={f?.key === m.key || undefined}>
                      <input type="checkbox" checked={on} disabled={!on && picked.length >= MAX} onChange={() => toggle(m.key)} aria-label={`${m.label} 차트에 넣기`}
                             style={slot != null ? { accentColor: PALETTE[slot] } : undefined} />
                      <button type="button" onClick={() => focusOn(m.key)} title={m.note}>
                        <span>{m.label}</span><small>{latest != null ? fmtVal(latest, m.fmt) : "-"}</small>
                      </button>
                    </div>
                  );
                })}
              </div>
            );
          })}
          {shown.length === 0 && <p className={c.empty}>찾는 지표가 없습니다.</p>}
        </div>
      </aside>

      <div className={c.metricMain}>
        <div className={c.explorerBar}>{onExport && series.length > 0 && <ExportButton make={() => onExport(series, f?.key, comparing ? cmpRows() : undefined, joined)} />}
          {onSave && series.length > 0 && <button type="button" className={c.exportBtn} onClick={() => onSave(series.map((x) => x.key), joined)}>차트로 저장</button>}
          <button type="button" className={c.togetherBtn} aria-pressed={joined} disabled={!canTogether} title={why || "한 차트에 함께 보기 (단위가 다르면 왼쪽·오른쪽 축)"}
                  onClick={() => setTogether((v) => !v)}>함께 보기</button>
          {controls}</div>
        {f && <Summary m={f} periods={periods} back={back} />}
        {series.length === 0 || periods.length === 0
          ? <p className={c.empty}>{periods.length === 0 ? "이 기간의 공시 데이터가 없습니다." : "왼쪽에서 지표를 고르세요."}</p>
          : joined ? <>
            <FinChart key={`j|${(comparing ? joinedCmp : series).map((x) => x.key).join()}`} periods={periods} series={comparing ? joinedCmp : series} height={280} />
            <FinTable periods={periods} series={comparing ? cmpTable : series} focus={comparing ? (f ? `${f.key}|me` : undefined) : f?.key} onFocus={comparing ? undefined : setFocus} />
          </> : comparing ? <>
            <div className={c.cmpGrid}>
              {cmp!.map(({ s, peers }) => {
                const lines = [meLine(s), ...peers.filter((x) => x.values.some((v) => v != null)).map((x) => ({ ...x, key: `${s.key}|${x.key}` }))];
                return (
                  <div key={s.key}>
                    <div className={c.chartUnit}>{s.label}<small> {UNIT[s.fmt]}</small></div>
                    <FinChart key={lines.map((x) => x.key).join()} periods={periods} series={lines} height={200} />
                  </div>
                );
              })}
            </div>
            <FinTable periods={periods} series={cmpTable} focus={f ? `${f.key}|me` : undefined} />
          </> : <>
            <FinCharts periods={periods} series={series} />
            <FinTable periods={periods} series={series} focus={f?.key} onFocus={setFocus} />
          </>}
        {series.some((s) => byKey.get(s.key)?.note) && (
          <ul className={c.notes}>
            {series.map((s) => byKey.get(s.key)?.note && <li key={s.key}>{s.label}: {byKey.get(s.key)!.note}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

/** 고른 지표 요약: 최근 값, 직전 기간 대비, 전년 같은 기간 대비 (표에 있는 값으로만) */
function Summary({ m, periods, back }: { m: Metric; periods: string[]; back: number }) {
  const i = m.values.map((v, k) => (v != null ? k : -1)).filter((k) => k >= 0).pop();
  if (i == null) return null;
  const v = m.values[i]!;
  const diff = (j: number) => {
    const p = j >= 0 ? m.values[j] : null;
    if (p == null) return "-";
    if (m.fmt === "pct") { const d = v - p; return `${d > 0 ? "+" : ""}${d.toFixed(2)}%p`; }
    if (p <= 0) return fmtVal(v - p, m.fmt);
    const r = ((v - p) / p) * 100;
    return `${r > 0 ? "+" : ""}${r.toFixed(1)}%`;
  };
  const tone = (s: string) => (s.startsWith("+") ? c.pos : s.startsWith("-") && s !== "-" ? c.neg : undefined);
  const d1 = diff(i - 1), dy = diff(i - back);
  return (
    <dl className={c.summary}>
      <div className={c.summaryMain}><dt>{m.label}{m.calc && <small className={c.calcTag}>계산</small>}</dt><dd>{fmtVal(v, m.fmt)}{m.marks?.[i] === "derived" ? "*" : ""}<small>{periods[i]}</small></dd></div>
      <div><dt>직전 기간 대비</dt><dd className={tone(d1)}>{d1}</dd></div>
      {back > 1 && <div><dt>전년 같은 분기 대비</dt><dd className={tone(dy)}>{dy}</dd></div>}
      {m.src && <div><dt>출처</dt><dd className={c.summarySrc}>{m.src}</dd></div>}
    </dl>
  );
}
