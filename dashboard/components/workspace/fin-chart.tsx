"use client";

// 재무 추이 차트와 같은 데이터를 쓰는 수치표 (2026-11-22, 기본 종목정보 탭)
// 차트 라이브러리를 쓰지 않고 SVG 로 그린다(이 대시보드의 다른 차트와 같은 방식). 차트와 표는 같은 series 를 받는다.
// 한 차트에는 단위가 같은 지표만 그린다(축 하나). 단위가 다른 지표는 FinCharts 가 위아래 차트로 나눈다.
import { useEffect, useRef, useState } from "react";
import c from "./company.module.css";

export type Fmt = "won" | "pct" | "krw" | "days";
export interface Series {
  key: string; label: string; color: string; kind: "bar" | "line"; fmt: Fmt;
  values: (number | null)[];
  marks?: ("derived" | "missing" | null)[];   // 칸마다: 계산으로 만든 값(*) / 공시 API 값이 틀려 비운 값
  dashed?: boolean;
  signed?: boolean;                            // 증가율처럼 오르면 빨강·내리면 파랑으로 보이는 값
  calc?: boolean;                              // 공시 값을 코드가 나눠 만든 비율·증가율 (표에 '계산' 표시)
  src?: string;                                // 표에 보이는 출처 (DART, FISIS 등)
  strong?: boolean;                            // 비교 차트에서 이 회사(굵은 선)
}

/** 금액: 1조 이상은 조, 1억 이상은 억, 그 밖은 원 */
export function fmtWon(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "-";
  const a = Math.abs(v);
  if (a >= 1e12) return `${(v / 1e12).toFixed(digits)}조원`;
  if (a >= 1e8) return `${Math.round(v / 1e8).toLocaleString("ko-KR")}억원`;
  return `${Math.round(v).toLocaleString("ko-KR")}원`;
}
export function fmtVal(v: number | null | undefined, f: Fmt): string {
  if (v == null || !Number.isFinite(v)) return "-";
  if (f === "pct") return `${v.toFixed(2)}%`;
  if (f === "krw") return `${Math.round(v).toLocaleString("ko-KR")}원`;
  if (f === "days") return `${Math.round(v).toLocaleString("ko-KR")}일`;
  return fmtWon(v);
}
export const UNIT: Record<Fmt, string> = { won: "금액", pct: "비율 (%)", krw: "주당 금액 (원)", days: "일수" };

function axisLabel(v: number, f: Fmt): string {
  if (f === "pct") return `${+v.toFixed(Math.abs(v) < 10 ? 1 : 0)}%`;
  if (f === "days") return `${Math.round(v)}일`;
  const a = Math.abs(v);
  if (f === "won" && a >= 1e12) return `${+(v / 1e12).toFixed(2)}조`;
  if (f === "won" && a >= 1e8) return `${Math.round(v / 1e8).toLocaleString("ko-KR")}억`;
  return Math.round(v).toLocaleString("ko-KR");
}
/** 보기 좋은 눈금. 칸 수 4~6 중 데이터 범위에 가장 딱 맞는 간격을 고른다 (위아래 빈칸이 적게).
 *  막대가 있으면 0 을 꼭 넣는다(막대 길이가 값에 비례하도록) */
function ticks(lo: number, hi: number, zero: boolean): number[] {
  if (zero) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  let best: { a: number; b: number; step: number; waste: number } | null = null;
  for (const n of [4, 5, 6]) {
    const raw = (hi - lo) / n;
    const p = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= raw * 0.999) ?? 10 * p;
    const a = Math.floor(lo / step + 1e-9) * step, b = Math.ceil(hi / step - 1e-9) * step;
    if ((b - a) / step > 7) continue;
    const waste = (b - a) / (hi - lo);
    if (!best || waste < best.waste - 1e-9) best = { a, b, step, waste };
  }
  const { a, b, step } = best!;
  const out: number[] = [];
  for (let t = a; t <= b + step * 0.001; t += step) out.push(+t.toFixed(10));
  return out;
}

/** 가로로 넘기는 칸을 처음에 가장 최근 기간(오른쪽 끝)에 맞춘다 */
export function useLatest(dep: string) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [dep]);
  return ref;
}

/** 막대: 0 선에 붙은 쪽은 각지고, 값 쪽 끝만 4px 둥글게 (값이 음수면 아래쪽 끝) */
function barPath(x: number, w: number, y0: number, y1: number): string {
  const h = Math.abs(y1 - y0);
  if (h < 1) return `M${x},${y0 - 0.5}h${w}v1h${-w}z`;
  const r = Math.min(4, w / 2, h);
  if (y1 < y0) return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`;
  return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
}

const PAD = { l: 56, r: 16, t: 14, b: 26 };
const LABEL_W = 54;                                   // 기간 이름 하나에 필요한 너비. 칸이 좁으면 건너서 쓴다

/** 같은 단위의 막대·선 차트. 단위가 정확히 2가지면 '함께 보기': 왼쪽 축(금액, 막대 가능) + 오른쪽 축(다른 단위, 선만).
 *  범례 이름 뒤에 (좌)/(우). 범례를 눌러 지표를 숨길 수 있다(지표 묶음이 바뀌면 부모가 key 로 초기화) */
export function FinChart({ periods, series, height = 240 }: { periods: string[]; series: Series[]; height?: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const fmts = [...new Set(series.map((s) => s.fmt))];
  const dual = fmts.length === 2;
  const fmt = !dual ? fmts[0] ?? "won"
    : series.find((s) => s.kind === "bar")?.fmt ?? (fmts.includes("won") ? "won" : fmts[0]);   // 왼쪽 축 단위
  const rfmt = dual ? fmts.find((f) => f !== fmt)! : fmt;                                    // 오른쪽 축 단위
  const right = (s: Series) => dual && s.fmt === rfmt;
  const shown = series.filter((s) => !hidden.has(s.key));
  const bars = shown.filter((s) => s.kind === "bar" && !right(s));                         // 막대는 왼쪽 축에만
  const lines = shown.filter((s) => s.kind === "line" || right(s));
  const tag = (s: Series) => (dual ? `${s.label} (${right(s) ? "우" : "좌"})` : s.label);
  const n = periods.length;
  const W = width;                                     // 가로로 넘기지 않는다 (세로축 숫자가 늘 보이게)
  const H = height;
  const PR = dual ? 60 : PAD.r;
  const col = (W - PAD.l - PR) / Math.max(n, 1);
  const x0 = (i: number) => PAD.l + col * i + col / 2;
  const every = Math.max(1, Math.ceil(LABEL_W / col));   // 이름을 몇 칸마다 쓰나 (최신 기간은 늘 쓴다)

  const scale = (list: Series[], zero: boolean) => {
    const vals = list.flatMap((s) => s.values.filter((v): v is number => v != null));
    const tt = ticks(vals.length ? Math.min(...vals) : 0, vals.length ? Math.max(...vals) : 1, zero);
    const a = tt[0], b = tt[tt.length - 1];
    return { t: tt, lo: a, hi: b, y: (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - (v - a) / (b - a || 1)) };
  };
  const L = scale(shown.filter((s) => !right(s)), bars.length > 0);
  const R = dual ? scale(shown.filter(right), false) : L;
  const { t, lo, hi, y } = L;
  const yOf = (s: Series) => (right(s) ? R.y : L.y);
  const axisInk = (side: Series[]) => (dual && side.length === 1 ? side[0].color : undefined);   // 축이 한 지표면 그 색
  const base = y(Math.min(Math.max(0, lo), hi));       // 막대가 서는 선 (0 이 범위 안이면 0)
  const group = Math.min(col * 0.62, bars.length * 22);
  const bw = bars.length ? group / bars.length : 0;

  const linePath = (s: Series) => {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x0(i).toFixed(1)},${yOf(s)(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  return (
    <div className={c.chartBox}>
      {series.length > 1 && (
        <div className={c.legend} role="group" aria-label="지표 표시">
          {series.map((s) => (
            <button key={s.key} type="button" aria-pressed={!hidden.has(s.key)} className={c.legendItem}
                    onClick={() => setHidden((h) => { const x = new Set(h); if (x.has(s.key)) x.delete(s.key); else x.add(s.key); return x; })}>
              <span className={s.kind === "bar" ? c.swatchBar : c.swatchLine} style={{ background: s.kind === "bar" ? s.color : undefined, borderColor: s.color }} />
              {tag(s)}
            </button>
          ))}
        </div>
      )}
      <div ref={wrap} className={c.chartScroll}>
        <div style={{ position: "relative", width: W }} onMouseLeave={() => setHover(null)}>
          <svg width={W} height={H} role="img" aria-label={`${series.map((s) => s.label).join(", ")} 추이`}>
            {t.map((v) => (
              <g key={v}>
                <line x1={PAD.l} x2={W - PR} y1={y(v)} y2={y(v)} stroke={v === 0 ? "#9aa1a9" : "#eef0f2"} strokeWidth={v === 0 ? 1.2 : 1} />
                <text x={PAD.l - 8} y={y(v) + 4} textAnchor="end" className={c.axis} style={{ fill: axisInk(series.filter((x) => !right(x))) }}>{axisLabel(v, fmt)}</text>
              </g>
            ))}
            {dual && R.t.map((v) => (
              <text key={`r${v}`} x={W - PR + 8} y={R.y(v) + 4} className={c.axis} style={{ fill: axisInk(series.filter(right)) }}>{axisLabel(v, rfmt)}</text>
            ))}
            {hover != null && <rect x={PAD.l + col * hover} y={PAD.t} width={col} height={H - PAD.t - PAD.b} fill="#f3f4f5" />}
            {hover != null && <line x1={x0(hover)} x2={x0(hover)} y1={PAD.t} y2={H - PAD.b} stroke="#c9ccd0" strokeWidth={1} />}
            {bars.map((s, k) => s.values.map((v, i) => v == null ? null : (
              <path key={`${s.key}${i}`} d={barPath(x0(i) - group / 2 + k * bw + 1, Math.max(bw - 2, 2), base, y(v))} fill={s.color} />
            )))}
            {lines.map((s) => (
              <g key={s.key}>
                <path d={linePath(s)} fill="none" stroke={s.color} strokeWidth={s.strong ? 3 : 2} strokeLinejoin="round" strokeDasharray={s.dashed ? "5 3" : undefined} />
                {s.values.map((v, i) => v == null ? null : (
                  <circle key={i} cx={x0(i)} cy={yOf(s)(v)} r={hover === i ? 5 : 3.5} fill="#fff" stroke={s.color} strokeWidth={2} />
                ))}
              </g>
            ))}
            {periods.map((p, i) => ((n - 1 - i) % every === 0 || hover === i) && (
              <text key={p} x={x0(i)} y={H - 8} textAnchor="middle" className={c.axis} fontWeight={hover === i ? 600 : undefined}>{p}</text>
            ))}
            {periods.map((p, i) => (
              <rect key={`h${p}`} x={PAD.l + col * i} y={0} width={col} height={H} fill="transparent" onMouseEnter={() => setHover(i)} />
            ))}
          </svg>
          {hover != null && (
            <div className={c.tooltip} style={{ left: Math.min(Math.max(x0(hover) - 100, 0), W - 210) }}>
              <strong>{periods[hover]}</strong>
              {series.map((s) => {
                const v = s.values[hover], prev = hover > 0 ? s.values[hover - 1] : null;
                const d = v != null && prev != null ? (s.fmt === "pct" ? v - prev : prev !== 0 ? ((v - prev) / Math.abs(prev)) * 100 : null) : null;
                return (
                  <div key={s.key} className={c.tipRow} style={{ opacity: hidden.has(s.key) ? 0.45 : 1 }}>
                    <span><i style={{ background: s.color }} />{tag(s)}</span>
                    <b>{fmtVal(v, s.fmt)}{v != null && s.marks?.[hover] === "derived" ? "*" : ""}
                      {d != null && <small className={d > 0 ? c.pos : d < 0 ? c.neg : undefined}> {d > 0 ? "+" : ""}{d.toFixed(1)}{s.fmt === "pct" ? "%p" : "%"}</small>}</b>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 단위가 다른 지표를 위아래 차트로 나눈다 (금액 차트, 비율 차트 …). 기간 칸은 같다 */
export function FinCharts({ periods, series, height = 240 }: { periods: string[]; series: Series[]; height?: number }) {
  const groups: Series[][] = [];
  series.forEach((s) => {
    const g = groups.find((x) => x[0].fmt === s.fmt);
    if (g) g.push(s); else groups.push([s]);
  });
  return (
    <div className={c.chartStack}>
      {groups.map((g, i) => (
        <div key={g[0].fmt}>
          {groups.length > 1 && <div className={c.chartUnit}>{UNIT[g[0].fmt]}</div>}
          <FinChart key={g.map((s) => s.key).join()} periods={periods} series={g} height={i === 0 ? height : Math.max(160, Math.round(height * 0.7))} />
        </div>
      ))}
    </div>
  );
}

/** 차트 아래 수치표: 왼쪽 지표명 칸은 가로로 넘겨도 고정, 최신 기간 칸 강조, 고른 지표 줄 강조 */
export function FinTable({ periods, series, focus, onFocus }: { periods: string[]; series: Series[]; focus?: string; onFocus?: (key: string) => void }) {
  const box = useLatest(periods.join());
  const last = periods.length - 1;
  return (
    <div ref={box} className={c.tableScroll}>
      <table className={c.finTable}>
        <thead>
          <tr><th className={c.stick}>기간</th>{periods.map((p, i) => <th key={p} className={i === last ? c.latest : undefined}>{p}</th>)}</tr>
        </thead>
        <tbody>
          {series.map((s) => (
            <tr key={s.key} className={focus === s.key ? c.focusRow : undefined} onClick={onFocus ? () => onFocus(s.key) : undefined}
                style={onFocus ? { cursor: "pointer" } : undefined}>
              <th className={c.stick} scope="row">
                <span className={s.kind === "bar" ? c.swatchBar : c.swatchLine} style={{ background: s.kind === "bar" ? s.color : undefined, borderColor: s.color }} />
                {s.label}
                {s.calc ? <small className={c.calcTag}>계산</small> : s.src ? <small className={c.srcTag}>{s.src}</small> : null}
              </th>
              {s.values.map((v, i) => (
                <td key={i} title={s.marks?.[i] === "missing" ? "공시 API 값이 앞 분기를 되풀이해 표시하지 않음" : s.marks?.[i] === "derived" ? "계산으로 만든 값" : undefined}
                    className={[s.signed && v != null ? (v < 0 ? c.neg : c.pos) : "", i === last ? c.latest : ""].filter(Boolean).join(" ") || undefined}>
                  {fmtVal(v, s.fmt)}{v != null && s.marks?.[i] === "derived" ? "*" : ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
