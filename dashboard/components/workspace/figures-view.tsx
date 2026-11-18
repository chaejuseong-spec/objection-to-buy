"use client";

// 수치와 도표: 핵심 수치, 리포트 표의 숫자로 그린 작은 그래프, 원문 도표를 잘라 그대로 보여주기
import { useEffect, useRef, useState } from "react";
import type { Figure, Result, Table } from "@/lib/types";
import { loadPdf, pdfUrl } from "@/lib/pdf";
import { Verdict } from "./primitives";
import { unitAfter } from "./tone";
import s from "./workspace.module.css";

const fmt = (v: number, unit: string) =>
  unit === "%" ? `${v.toFixed(1)}%` : `${v.toLocaleString("ko-KR")}${unit.includes("원") ? "" : unit}`;
const short = (y: string) => (/^\d{4}/.test(y) ? `'${y.slice(2)}` : y);

/** 한 항목의 연도별 값. 금액은 막대, 비율은 선. 추정 연도는 빗금과 점선. */
function Series({ name, unit, kind, years, values, estimateFrom, highlight, source }: {
  name: string; unit: string; kind: string; years: string[]; values: number[]; estimateFrom: number; highlight: number;
  source: string;            // 이 숫자가 나온 원문 표 (제목, 쪽)
}) {
  const [hover, setHover] = useState<number | null>(null);
  const at = Math.min(values.length - 1, Math.max(0, hover ?? highlight ?? values.length - 1));
  const W = 280, H = 150, L = 6, R = 6, T = 24, B = 24;
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const col = (W - L - R) / values.length;
  const cx = (i: number) => L + col * i + col / 2;
  const y = (v: number) => T + (1 - (v - lo) / span) * (H - T - B);
  const zero = y(0), bw = Math.min(22, col - 8);
  const id = `hatch-${name.replace(/[^a-zA-Z0-9가-힣]/g, "")}`;
  const est = estimateFrom >= 0 ? estimateFrom : values.length;

  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{name} <span className={s.muted} style={{ fontSize: 11.5, fontWeight: 400 }}>{unit}</span></span>
        <span className={s.muted} style={{ fontSize: 12 }}>{years[at]}{at >= est ? " 추정" : ""} <strong style={{ fontSize: 15, color: "var(--ink)" }}>{fmt(values[at], unit)}</strong></span>
      </figcaption>
      <div className={s.chartBox}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", marginTop: 4, display: "block" }} role="img" onMouseLeave={() => setHover(null)}
           aria-label={`${name}: ${years.map((yr, i) => `${yr}${i >= est ? "(추정)" : ""} ${fmt(values[i], unit)}`).join(", ")}`}>
        <defs>
          <pattern id={id} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="#e6e9ec" /><rect width="2" height="5" fill="#8e9aa6" />
          </pattern>
        </defs>
        <line x1={L} x2={W - R} y1={zero} y2={zero} stroke="#a3a8ae" />
        {hover !== null && <line x1={cx(hover)} x2={cx(hover)} y1={T - 8} y2={H - B} stroke="#a3a8ae" strokeDasharray="2 3" />}
        {kind === "amount" ? values.map((v, i) => {
          const top = Math.min(y(v), zero), h = Math.max(1.5, Math.abs(y(v) - zero));
          return <rect key={i} x={cx(i) - bw / 2} y={top} width={bw} height={h}
                       fill={i >= est ? `url(#${id})` : "#2e3a46"} stroke={i >= est ? "#8e9aa6" : "none"} opacity={i === at ? 1 : 0.85} />;
        }) : (
          <>
            <polyline fill="none" stroke="#2e3a46" strokeWidth={2} points={values.slice(0, est).map((v, i) => `${cx(i)},${y(v)}`).join(" ")} />
            {est < values.length && (
              <polyline fill="none" stroke="#8e9aa6" strokeWidth={2} strokeDasharray="4 4"
                        points={values.slice(Math.max(0, est - 1)).map((v, i) => `${cx(i + Math.max(0, est - 1))},${y(v)}`).join(" ")} />
            )}
            {values.map((v, i) => (
              <circle key={i} cx={cx(i)} cy={y(v)} r={i === at ? 4.5 : 3.5} fill={i >= est ? "#fff" : "#2e3a46"} stroke={i >= est ? "#8e9aa6" : "#fff"} strokeWidth={1.5} />
            ))}
          </>
        )}
        {years.map((yr, i) => (
          <g key={`${yr}-${i}`}>
            <text x={cx(i)} y={H - 7} textAnchor="middle" fontSize="10.5" fill={i === at ? "#1a1d21" : "#5c626b"}>{short(yr)}</text>
            <rect x={L + col * i} y={0} width={col} height={H} fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
      </svg>
      {hover !== null && (
        // 표에 적힌 값 그대로. 막대 옆에 띄우고, 오른쪽 끝이면 왼쪽으로 붙인다
        <div className={s.chartTip} role="status" style={cx(hover) / W > 0.6
          ? { right: `${(1 - cx(hover) / W) * 100 + 4}%` } : { left: `${(cx(hover) / W) * 100 + 4}%` }}>
          <strong>{years[hover]}</strong>
          <span>{name}: <b>{fmt(values[hover], unit)}</b>{unit.includes("원") ? ` ${unit}` : ""}</span>
          <small>{hover >= est ? "리포트 추정치" : "실적"} · 출처: {source}</small>
        </div>
      )}
      </div>
    </figure>
  );
}

function DataTable({ t }: { t: Table }) {
  return (
    <details style={{ marginTop: 14 }}>
      <summary className={s.link} style={{ cursor: "pointer", display: "inline" }}>표로 보기</summary>
      <div className={s.tableWrap} style={{ marginTop: 8 }}>
        <table className={s.table} style={{ minWidth: 560 }}>
          <thead><tr><th>항목</th>{t.years.map((y, i) => <th key={`${y}-${i}`} className={s.num}>{y}</th>)}</tr></thead>
          <tbody>
            {t.rows.map((r) => (
              <tr key={r.name}><td>{r.name} <span className={s.muted}>{r.unit}</span></td>
                {r.values.map((v, i) => <td key={i} className={s.num}>{v.toLocaleString("ko-KR")}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** 원문의 도표 영역을 그대로 잘라 그린다. 값을 읽어 다시 그리지 않는다. */
function Crop({ reportId, fig }: { reportId: string; fig: Figure }) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"wait" | "done" | "fail">("wait");
  const [ratio, setRatio] = useState((fig.rect[3] / fig.rect[2]) * 1.414);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let dead = false;
    const io = new IntersectionObserver(async ([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      try {
        const pdf = await loadPdf(pdfUrl(reportId));
        const page = await pdf.getPage(fig.page);
        const base = page.getViewport({ scale: 1 });
        const [rx, ry, rw, rh] = fig.rect;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const scale = Math.min(4, (el.clientWidth * dpr) / (rw * base.width));
        const vp = page.getViewport({ scale });
        const off = document.createElement("canvas");
        off.width = Math.floor(vp.width);
        off.height = Math.floor(vp.height);
        await page.render({ canvas: off, viewport: vp }).promise;
        if (dead || !canvas.current) return;
        const c = canvas.current;
        c.width = Math.floor(rw * vp.width);
        c.height = Math.floor(rh * vp.height);
        c.getContext("2d")!.drawImage(off, rx * vp.width, ry * vp.height, c.width, c.height, 0, 0, c.width, c.height);
        setRatio(c.height / c.width);
        setState("done");
      } catch {
        if (!dead) setState("fail");
      }
    }, { rootMargin: "400px 0px" });
    io.observe(el);
    return () => { dead = true; io.disconnect(); };
  }, [fig, reportId]);

  return (
    <div ref={box} className={s.crop} style={{ aspectRatio: `1 / ${ratio}` }}>
      {state === "fail" && <p className={s.muted} style={{ padding: 12, fontSize: 12 }}>도표를 그리지 못했습니다.</p>}
      <canvas ref={canvas} style={{ opacity: state === "done" ? 1 : 0 }} aria-label={`${fig.id} ${fig.title}`} />
    </div>
  );
}

export function FiguresView({ reportId, result, onPage, onCheck }: {
  reportId: string; result: Result; onPage: (p: number) => void; onCheck: (ref: string) => void;
}) {
  const { kpis, tables, figures, numbers, document: doc, claims, marks } = result;
  const claimPages = new Set([...claims.map((c) => c.page), ...marks.map((m) => m.page)]);
  const near = figures.filter((f) => claimPages.has(f.page));
  const [all, setAll] = useState(near.length === 0);
  const list = all ? figures : near;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 44 }}>
      <section className={s.block}>
        <div className={s.sectionHead}><h3>핵심 수치<small>식이 있는 {numbers.n_checks}건은 코드가 다시 계산했습니다</small></h3></div>
        <dl className={s.kpiGrid}>
          {kpis.map((k) => (
            <div key={k.rid}>
              <dt title={k.item}>{k.item}</dt>
              <dd>{k.value}<small>{unitAfter(k.value, k.unit)}</small></dd>
              <div className={s.kpiFoot}>
                {k.check ? (
                  <button type="button" className={s.link} style={{ textDecoration: "none" }} onClick={() => onCheck(k.check!.id)}>
                    <Verdict label={k.check.status} />
                  </button>
                ) : <span className={s.muted} style={{ fontSize: 12 }}>식 없음</span>}
                <button type="button" className={s.link} onClick={() => onPage(k.page)}>{k.page}쪽</button>
              </div>
            </div>
          ))}
        </dl>
      </section>

      {tables.map((t) => (
        <section key={t.id} className={s.block}>
          <div className={s.sectionHead}>
            <h3>{t.title}<small>{t.note || "빗금과 점선은 리포트의 추정치"}</small></h3>
            <button type="button" className={s.link} onClick={() => onPage(t.page)}>원문 {t.page}쪽</button>
          </div>
          <div className={s.seriesGrid}>
            {t.rows.map((r) => (
              <Series key={r.name} name={r.name} unit={r.unit} kind={r.kind} years={t.years} values={r.values}
                      estimateFrom={t.estimate_from} highlight={t.highlight}
                      source={`${t.title || "리포트 표"}${t.page ? `, 원문 ${t.page}쪽` : ""}`} />
            ))}
          </div>
          <DataTable t={t} />
        </section>
      ))}

      <section className={s.block}>
        <div className={s.sectionHead}>
          <h3>원문 도표<small>원문에서 그대로 잘라 보여줍니다</small></h3>
          {figures.length > 0 && (
            <div className={s.segmented} role="group" aria-label="도표 범위">
              <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>주장이 있는 쪽 {near.length}</button>
              <button type="button" aria-pressed={all} onClick={() => setAll(true)}>전체 {figures.length}</button>
            </div>
          )}
        </div>
        {doc.has_pdf && list.length ? (
          <div className={s.figureGrid}>
            {list.map((f) => (
              <figure key={`${f.id}-${f.page}`}>
                <Crop reportId={reportId} fig={f} />
                <figcaption>
                  <span><span className={s.muted}>{f.id}</span> {f.title}</span>
                  <button type="button" className={s.link} onClick={() => onPage(f.page)}>{f.page}쪽</button>
                </figcaption>
              </figure>
            ))}
          </div>
        ) : <p className={s.muted} style={{ fontSize: 13 }}>{figures.length ? "주장이 있는 쪽에는 도표가 없습니다. 전체를 눌러 보세요." : "원문에서 찾은 도표가 없습니다."}</p>}
      </section>
    </div>
  );
}
