"use client";

// 토스 섹터 (2026-11-24, 사용자 4단계): 토스증권 자체 산업 분류. 한 종목이 여러 섹터에 속한다(삼성전자 6곳).
// 전면 개편 1차 (2026-11-25 사용자): 경쟁사 = 토스 섹터 종목. 처음 고르는 섹터 = 3곳 이상 중 가장 작은 섹터(경쟁사 표와 같은 섹터).
// 섹터를 바꾸면 '비교 회사 후보'도 그 섹터 종목으로 바뀐다 (useSectorPick 으로 같이 쓴다)
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { fmtWon } from "./fin-chart";
import c from "./company.module.css";

export interface TossSectorMember { ticker: string; name: string; mktcap: number | null; amount: number | null }
export interface TossSector { id: string; name: string; path: string[]; summary: string | null; perf: Record<string, number | null>;
  members: TossSectorMember[]; rank: number | null; count: number; extra?: boolean }
export interface TossSectorView { ticker: string; sectors: TossSector[]; index_at?: string; error?: string }

const cache = new Map<string, TossSectorView>();
const waiting = new Map<string, Promise<TossSectorView>>();
/** 한 종목의 섹터 자료 (화면 안 여러 곳이 같이 쓰므로 한 번만 받는다) */
const viewSubs = new Set<() => void>();              // 섹터를 직접 추가·빼면 같은 종목을 보는 곳이 모두 다시 그린다
export function useTossSector(ticker: string): TossSectorView | null {
  const [got, setGot] = useState<TossSectorView | null>(null);
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    viewSubs.add(f);
    return () => { viewSubs.delete(f); };
  }, []);
  useEffect(() => {
    if (cache.has(ticker)) return;
    let off = false;
    let p = waiting.get(ticker);
    if (!p) {
      p = fetch(`/api/stocks/${ticker}/sector`).then((r) => r.json())
        .then((b: TossSectorView) => { if (!b.error) cache.set(ticker, b); return b; })
        .catch(() => ({ ticker, sectors: [], error: "섹터 자료를 받지 못했습니다." }))
        .finally(() => waiting.delete(ticker));
      waiting.set(ticker, p);
    }
    p.then((b) => { if (!off) setGot(b); });
    return () => { off = true; };
  }, [ticker]);
  return cache.get(ticker) ?? (got?.ticker === ticker || got?.error ? got : null);
}

/** 처음 보여 줄 섹터: 경쟁사 표를 만든 섹터(preferId). 없으면 파이썬 peers.main_sector 와 같은 규칙:
 *  구성 종목 3곳 이상 중 가장 작은 섹터 (같으면 이 회사 비중이 작은 섹터), 3곳 이상이 없으면 가장 많은 섹터 */
export function mainSector(v: TossSectorView | null, preferId?: string | null): TossSector | null {
  const list = v?.sectors ?? [];
  if (!list.length) return null;
  const hit = list.find((s) => s.id === preferId);
  if (hit) return hit;
  const share = (s: TossSector) => {
    const tot = s.members.reduce((a, m) => a + (m.mktcap ?? 0), 0);
    return tot ? (s.members.find((m) => m.ticker === v!.ticker)?.mktcap ?? 0) / tot : 1;
  };
  const big = list.filter((s) => s.count >= 3);
  return big.length ? big.reduce((a, b) => (b.count < a.count || (b.count === a.count && share(b) < share(a)) ? b : a))
    : list.reduce((a, b) => (b.count > a.count ? b : a));
}

/* 화면 안에서 고른 섹터 (종목마다). 섹터 표와 비교 회사 후보가 같이 쓴다 */
const picks = new Map<string, string>();
const subs = new Set<() => void>();
export function useSectorPick(ticker: string): [string | null, (id: string) => void] {
  const id = useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => picks.get(ticker) ?? null, () => null);
  return [id, (x: string) => { picks.set(ticker, x); subs.forEach((f) => f()); }];
}
/** 지금 고른 섹터 (고른 적 없으면 처음 섹터) */
export function useCurrentSector(ticker: string, preferId?: string | null): TossSector | null {
  const v = useTossSector(ticker);
  const [pick] = useSectorPick(ticker);
  return v?.sectors.find((x) => x.id === pick) ?? mainSector(v, preferId);
}

const DUR: [string, string][] = [["1d", "1일"], ["1w", "1주"], ["1mo", "1개월"], ["3mo", "3개월"], ["6mo", "6개월"], ["1y", "1년"]];
const SHOW = 15;

/** 금융회사 (2026-11-26 사용자): 섹터 패널 대신 업권에 맞는 토스 섹터의 기간 등락률만. 금감원 업권 → 토스 섹터 이름 (앞에 있는 것 먼저) */
const PART_SECTOR: Record<string, string[]> = {
  L: ["금융그룹", "지주사"], A: ["은행", "시중은행", "지역은행"], F: ["증권"], W: ["증권"], C: ["카드"],
  H: ["생명보험", "보험"], I: ["손해보험", "보험"], E: ["저축은행"], M: ["부동산신탁"],
};
/* 이 종목 같은 기간 등락률 (2026-11-26 사용자): 주가 추이와 같은 일별 종가(수정주가)로 코드가 계산.
   1일 = 직전 거래일 대비, 나머지 = 그 기간 전 날짜(휴장이면 그 앞 거래일) 종가 대비 */
type DayRow = { d: string; c: number };
const retCache = new Map<string, Promise<Record<string, number | null>>>();
function stockReturns(rows: DayRow[]): Record<string, number | null> {
  const n = rows.length;
  if (n < 2) return {};
  const last = rows[n - 1];
  const ld = new Date(+last.d.slice(0, 4), +last.d.slice(4, 6) - 1, +last.d.slice(6, 8));
  const at = (f: (x: Date) => void) => {
    const x = new Date(ld);
    f(x);
    const key = `${x.getFullYear()}${String(x.getMonth() + 1).padStart(2, "0")}${String(x.getDate()).padStart(2, "0")}`;
    for (let i = n - 1; i >= 0; i--) if (rows[i].d <= key) return rows[i];
    return null;                                          // 상장 전: 계산하지 않는다
  };
  const r = (b: DayRow | null) => (b && b.c ? (last.c / b.c - 1) * 100 : null);
  const mb = (x: Date, k: number) => {                   // k달 전 같은 날 (그 달에 없는 날이면 그 달 말일: 3월 31일 → 2월 28일)
    const dd = x.getDate();
    x.setDate(1);
    x.setMonth(x.getMonth() - k);
    x.setDate(Math.min(dd, new Date(x.getFullYear(), x.getMonth() + 1, 0).getDate()));
  };
  return {
    "1d": r(rows[n - 2]), "1w": r(at((x) => x.setDate(x.getDate() - 7))), "1mo": r(at((x) => mb(x, 1))),
    "3mo": r(at((x) => mb(x, 3))), "6mo": r(at((x) => mb(x, 6))), "1y": r(at((x) => mb(x, 12))),
  };
}
function useStockReturns(ticker: string): Record<string, number | null> | null {
  const [got, setGot] = useState<{ t: string; v: Record<string, number | null> } | null>(null);
  useEffect(() => {
    let off = false;
    if (!retCache.has(ticker)) {
      retCache.set(ticker, fetch(`/api/stocks/${ticker}/history`).then((r) => r.json())
        .then((b: { rows?: DayRow[] }) => stockReturns(b.rows ?? [])).catch(() => ({})));
    }
    retCache.get(ticker)!.then((v) => { if (!off) setGot({ t: ticker, v }); });
    return () => { off = true; };
  }, [ticker]);
  return got?.t === ticker ? got.v : null;
}
const pct = (r: number | null | undefined) => (r == null ? "-" : `${r > 0 ? "+" : ""}${r.toFixed(2)}%`);
const tone = (r: number | null | undefined) => (r == null ? undefined : r > 0 ? c.pos : r < 0 ? c.neg : undefined);
/** 기간 등락률 표: 섹터 한 줄 + 이 종목 한 줄 (같은 기간) */
function PerfTable({ ticker, sector, name }: { ticker: string; sector: TossSector; name: string }) {
  const mine = useStockReturns(ticker);
  const rows: [string, Record<string, number | null> | null][] = [[`${sector.name} 섹터`, sector.perf], [name, mine]];
  return (
    <div className={c.tableScroll}>
      <table className={c.perfTable}>
        <thead><tr><th scope="col"><span className={c.srOnly}>구분</span></th>{DUR.map(([k, t]) => <th key={k} scope="col">{t}</th>)}</tr></thead>
        <tbody>
          {rows.map(([label, perf]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              {DUR.map(([k]) => <td key={k} className={tone(perf?.[k])}>{perf ? pct(perf[k]) : "…"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SectorPerf({ ticker, part, name }: { ticker: string; part?: string | null; name: string }) {
  const v = useTossSector(ticker);
  if (!v || v.error || !v.sectors.length) return null;
  const want = PART_SECTOR[part ?? ""] ?? [];
  const s = want.map((n) => v.sectors.find((x) => x.name === n)).find(Boolean) ?? mainSector(v);
  if (!s) return null;
  return (
    <section className={c.section}>
      <div className={c.sectionHead}><h3>업권 등락률</h3></div>
      <PerfTable ticker={ticker} sector={s} name={name} />
    </section>
  );
}

/** 비금융 경쟁사 맨 위 (2026-11-26 사용자 A안): 섹터 버튼 + 기간 등락률 한 줄. 섹터를 바꾸면 아래 경쟁사 표·비교 회사 후보도 그 섹터 */
export function SectorBar({ ticker, preferId, name }: { ticker: string; preferId?: string | null; name: string }) {
  const v = useTossSector(ticker);
  const [pick, setPick] = useSectorPick(ticker);
  if (!v) return <p className={c.notes}>섹터 자료를 받는 중입니다 (섹터 색인을 처음 만들 때는 2~3분, 이후 7일 동안 다시 씀)…</p>;
  if (v.error || !v.sectors.length) return <SectorAdd ticker={ticker} view={v} onAdded={setPick} />;
  const s = v.sectors.find((x) => x.id === pick) ?? mainSector(v, preferId)!;
  return (
    <div className={c.sectorBar}>
      <div className={c.sectorBtns}>
        <div className={c.segment} role="group" aria-label="섹터">
          {v.sectors.map((x) => <button key={x.id} type="button" aria-pressed={x.id === s.id} onClick={() => setPick(x.id)}
            title={x.extra ? "직접 추가한 섹터" : x.path.join(" > ")}>{x.name}{x.extra ? " ✚" : ""}</button>)}
        </div>
        <SectorAdd ticker={ticker} view={v} onAdded={setPick} />
      </div>
      <PerfTable ticker={ticker} sector={s} name={name} />
    </div>
  );
}

export function TossSectorPanel({ ticker, preferId }:{ ticker: string; preferId?: string | null; candidates?: boolean }) {
  const v = useTossSector(ticker);
  const [pick, setPick] = useSectorPick(ticker);
  const [all, setAll] = useState(false);
  if (!v) return (
    <section className={c.section}>
      <div className={c.sectionHead}><h3>경쟁사</h3></div>
      <p className={c.notes}>섹터 자료를 받는 중입니다 (섹터 색인을 처음 만들 때는 2~3분, 이후 7일 동안 다시 씀)…</p>
    </section>
  );
  if (v.error || !v.sectors.length) return (
    <section className={c.section}>
      <div className={c.sectionHead}><h3>경쟁사</h3></div>
      <p className={c.notes}>{v.error ?? "토스증권 섹터에 들어 있지 않은 종목입니다."}</p>
    </section>
  );
  const first = mainSector(v, preferId)!;
  const s = v.sectors.find((x) => x.id === pick) ?? first;
  const max = Math.max(...s.members.map((m) => m.mktcap ?? 0), 1);
  const meIdx = s.members.findIndex((m) => m.ticker === ticker);
  const shown = all ? s.members : s.members.filter((m, i) => i < SHOW || i === meIdx);
  return (
    <section className={c.section}>
      <div className={c.sectionHead}>
        <h3>경쟁사<small>{s.path.join(" > ")}{s.rank ? `, 시가총액 ${s.rank}위 / ${s.count}곳` : ""}</small></h3>
        {v.sectors.length > 1 && (
          <div className={c.filters}>
            <div className={c.segment} role="group" aria-label="섹터">
              {v.sectors.map((x) => <button key={x.id} type="button" aria-pressed={x.id === s.id} onClick={() => { setPick(x.id); setAll(false); }}>{x.name}</button>)}
            </div>
          </div>
        )}
      </div>
      {s.summary && <p className={c.sectorSummary}>{s.summary}</p>}
      <dl className={c.perfRow}>
        {DUR.map(([k, t]) => {
          const r = s.perf[k];
          return <div key={k}><dt>{t}</dt><dd className={r == null ? undefined : r > 0 ? c.pos : r < 0 ? c.neg : undefined}>{r == null ? "-" : `${r > 0 ? "+" : ""}${r.toFixed(2)}%`}</dd></div>;
        })}
      </dl>
      <div className={c.rankList} role="list" aria-label="섹터 시가총액 순위">
        {shown.map((m) => (
          <div key={m.ticker} role="listitem" className={c.rankRow} data-me={m.ticker === ticker || undefined}>
            <span className={c.rankNo}>{s.members.indexOf(m) + 1}</span>
            <span className={c.rankName}>{m.name}</span>
            <span className={c.rankTrack}>{m.mktcap != null && <span className={c.rankBar} style={{ width: `${(m.mktcap / max) * 100}%` }} />}</span>
            <span className={c.rankVal}>{fmtWon(m.mktcap)}</span>
          </div>
        ))}
      </div>
      {s.members.length > SHOW + 1 && (
        <button type="button" className={c.peerPaneMore} aria-expanded={all} onClick={() => setAll((x) => !x)}>
          {all ? "상위 15곳만" : `${s.members.length}곳 모두 보기`}
        </button>
      )}
    </section>
  );
}

/* 섹터 직접 추가 (2026-11-26 사용자): 토스 분류가 어색한 종목(예: 지주사로만 묶인 HD한국조선해양)에 섹터를 붙인다.
   종목마다 저장(library/_common/extra_sectors.json). 붙인 섹터의 종목이 경쟁사 표·비교 회사 후보가 된다 */
type SecItem = { id: string; name: string; path: string[]; count: number };
let secList: Promise<SecItem[]> | null = null;
function SectorAdd({ ticker, view, onAdded }: { ticker: string; view: TossSectorView; onAdded: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [list, setList] = useState<SecItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    let off = false;
    secList ??= fetch("/api/sectors").then((r) => r.json()).then((b) => (Array.isArray(b.sectors) ? b.sectors : [])).catch(() => { secList = null; return []; });
    secList.then((x) => { if (!off) setList(x); });
    box.current?.querySelector<HTMLElement>("input")?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const click = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", key);
    window.addEventListener("mousedown", click);
    return () => { off = true; window.removeEventListener("keydown", key); window.removeEventListener("mousedown", click); };
  }, [open]);
  const mine = (view.sectors ?? []).filter((x) => x.extra);
  const extra = mine.map((x) => x.id);
  const have = new Set((view.sectors ?? []).map((x) => x.id));
  const save = async (ids: string[], show?: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/stocks/${ticker}/sector`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extra: ids }) });
      const b = (await r.json()) as TossSectorView;
      if (!r.ok || b.error) throw new Error(b.error || "섹터를 저장하지 못했습니다.");
      cache.set(ticker, b);
      viewSubs.forEach((f) => f());
      if (show) { onAdded(show); setOpen(false); setQ(""); }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "섹터를 저장하지 못했습니다.");
    }
    setBusy(false);
  };
  const t = q.trim().replace(/\s+/g, "");
  const hits = t ? list.filter((x) => !have.has(x.id) && x.path.join("").replace(/\s+/g, "").includes(t)).slice(0, 12) : [];
  return (
    <div className={c.sectorAdd} ref={box}>
      <button type="button" className={c.sectorAddBtn} aria-expanded={open} onClick={() => setOpen((o) => !o)}>+ 섹터</button>
      {open && (
        <div className={c.sectorAddPop} role="dialog" aria-label="섹터 직접 추가">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="섹터 이름 (예: 조선)" aria-label="섹터 찾기" />
          {busy && <small className={c.modalHint}>저장하고 섹터 자료를 받는 중…</small>}
          {err && <small className={c.warnCell}>{err}</small>}
          {hits.length > 0 && (
            <ul>
              {hits.map((x) => (
                <li key={x.id}><button type="button" disabled={busy} onClick={() => save([...extra, x.id], x.id)}>
                  <span>{x.path.join(" > ")}</span><small>{x.count}곳</small>
                </button></li>
              ))}
            </ul>
          )}
          {t && !hits.length && list.length > 0 && <small className={c.modalHint}>맞는 섹터가 없습니다.</small>}
          {mine.length > 0 && (
            <div className={c.sectorAddMine}>
              <small>직접 추가한 섹터</small>
              {mine.map((x) => (
                <button key={x.id} type="button" disabled={busy} aria-label={`${x.name} 빼기`} onClick={() => save(extra.filter((y) => y !== x.id))}>{x.name} ×</button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
