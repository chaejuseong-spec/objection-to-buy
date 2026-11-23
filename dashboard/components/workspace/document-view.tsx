"use client";

// 원문에서 보기: PDF 위에 판정 색을 칠한다. 원문 뷰어는 페이지와 따로 스크롤된다.
import { useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import type { Mark, Result } from "@/lib/types";
import { loadPdf, pdfUrl } from "@/lib/pdf";
import { findObjection, shownMarks } from "./problems";
import { TONE, toneOf, toneVars, type Tone, NUMBER_COLOR, numberVars } from "./tone";
import s from "./workspace.module.css";

const WIDTHS = [560, 680, 800, 940];
export type Filter = "all" | "red" | "orange" | "green" | "number";

/** 표시 하나가 가리키는 판정(주장 판정, 원문 문장을 짚은 이의의 판정, 숫자 검증 결과) */
/** 표시 하나의 묶음(위 거르개 칸): 숫자 문제 / 판정 색 */
export function markGroup(result: Result, ref: string): Filter {
  if (result.numbers.results.some((x) => x.check_id === ref)) return "number";
  return markTone(result, ref) as Filter;
}

export function markTone(result: Result, ref: string): Tone {
  const c = result.claims.find((x) => x.id === ref);
  if (c) return toneOf(c.verdict);
  const o = findObjection(result, ref);
  if (o) return toneOf(o.verdict);
  const k = result.numbers.results.find((x) => x.check_id === ref);
  return k ? toneOf(k.status) : "none";
}

function Page({ pdf, n, width, ratio, marks, selected, flash, isDimmed, toneFor, onSelect }: {
  pdf: PDFDocumentProxy | null; n: number; width: number; ratio: number; marks: Mark[]; selected: string | null; flash: string | null;
  isDimmed: (m: Mark) => boolean; toneFor: (ref: string) => Tone; onSelect: (ref: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) setNear(true); }, { rootMargin: "900px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!pdf || !near || !canvas.current) return;
    let task: RenderTask | null = null, dead = false;
    pdf.getPage(n).then((page) => {
      if (dead || !canvas.current) return;
      const base = page.getViewport({ scale: 1 });
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const vp = page.getViewport({ scale: (width / base.width) * dpr });
      const c = canvas.current;
      c.width = Math.floor(vp.width);
      c.height = Math.floor(vp.height);
      task = page.render({ canvas: c, viewport: vp });
      task.promise.catch(() => {});
    }).catch(() => {});
    return () => { dead = true; task?.cancel(); };
  }, [pdf, near, n, width]);

  return (
    <div ref={box} id={`pg-${n}`} data-page={n} className={s.pdfPage} style={{ width, height: width * ratio }}>
      <canvas ref={canvas} aria-label={`원문 ${n}쪽`} />
      {marks.map((m, mi) => (m.ctx ?? []).map((r, ri) => (
        <div key={`c${mi}-${ri}`} className={s.pdfCtx} aria-hidden="true" data-active={selected === m.ref || undefined}
             style={{ ...toneVars(toneFor(m.ref)), left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${r[2] * 100}%`, height: `${r[3] * 100}%` }}
             onClick={() => onSelect(m.ref)} />
      )))}
      {marks.map((m, mi) => m.rects.map((r, ri) => (
        <button key={`${mi}-${ri}`} type="button" className={s.pdfMark} data-ref={m.ref} data-first={ri === 0 || undefined}
                style={{ ...(m.kind === "number" ? numberVars() : toneVars(toneFor(m.ref))), left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${r[2] * 100}%`, height: `${r[3] * 100}%` }}
                data-active={selected === m.ref} data-dimmed={isDimmed(m)} data-flash={flash === m.ref || undefined} aria-pressed={selected === m.ref}
                aria-label={`${m.kind === "claim" ? "주장" : m.kind === "objection" ? "원문 지적" : "숫자 검증"} 기록 보기`} tabIndex={ri === 0 ? 0 : -1}
                onClick={() => onSelect(m.ref)} />
      )))}
      <span>{n}</span>
    </div>
  );
}

export interface Jump { ref?: string; page?: number; nonce: number }

export function DocumentView({ reportId, result, selected, onSelect, jump, filter, onFilter }: {
  reportId: string; result: Result; selected: string | null; onSelect: (ref: string) => void; jump: Jump | null;
  filter: Filter; onFilter: (f: Filter) => void;     // 거르개는 오른쪽 패널의 이전/다음과 함께 쓰려고 바깥에서 들고 있다
}) {
  const { document: doc } = result;
  // 통과한 곳(일치한 숫자, 이의 없는 주장)은 칠하지 않는다. 문제가 있는 곳만 보여준다
  // 숫자 표시는 지금 결과가 '일치'면 칠하지 않는다 (다시 검산해 맞는 것으로 바뀐 경우의 남은 표시)
  const marks = useMemo(() => shownMarks(result), [result]);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [ratio, setRatio] = useState(1.414);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [current, setCurrent] = useState(1);
  const desk = useRef<HTMLDivElement>(null);
  const width = WIDTHS[zoom];
  const pages = doc.pages || 0;

  const toneFor = useMemo(() => {
    const tones = new Map(marks.map((m) => [m.ref, markTone(result, m.ref)] as const));
    return (ref: string): Tone => tones.get(ref) ?? "none";
  }, [result, marks]);

  const refs = useMemo(() => Array.from(new Map(marks.map((m) => [m.ref, m])).values()), [marks]);
  // 표시 하나의 분류: 통과(초록) / 숫자 문제 / 주장 판정 색
  const group = (m: Mark): Filter => (m.pass ? "green" : m.kind === "number" ? "number" : (toneFor(m.ref) as Filter));
  const count = (f: Filter) => refs.filter((m) => f === "all" || group(m) === f).length;
  const isDimmed = () => false;      // 거르개를 골라도 원문 표시는 모두 그대로 (2026-11-20 사용자 요청). 거르개는 오른쪽 패널 이동에만 쓴다

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const d = await loadPdf(pdfUrl(reportId));
        if (dead) return;
        const vp = (await d.getPage(1)).getViewport({ scale: 1 });
        setRatio(vp.height / vp.width);
        setPdf(d);
      } catch {
        if (!dead) setError("원문 PDF를 열지 못했습니다.");
      }
    })();
    return () => { dead = true; };
  }, [reportId]);

  // 이동해 온 곳을 잠깐 진하게 칠했다가 원래대로 (반복해서 깜빡이지 않는다)
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => {
    if (!jump?.ref) return;
    const reset = setTimeout(() => setFlash(null), 0);                  // 같은 곳을 다시 눌러도 처음부터 강조
    const on = setTimeout(() => setFlash(jump.ref ?? null), 260);       // 스크롤이 도착할 즈음 시작
    const off = setTimeout(() => setFlash(null), 260 + 900);
    return () => { clearTimeout(reset); clearTimeout(on); clearTimeout(off); };
  }, [jump]);

  // 다른 화면에서 "원문 n쪽" 또는 주장을 눌러 넘어왔을 때
  useEffect(() => {
    if (!jump) return;
    const t = setTimeout(() => {
      const root = desk.current;
      if (!root) return;
      const target = jump.ref
        ? root.querySelector<HTMLElement>(`[data-ref="${CSS.escape(jump.ref)}"][data-first]`)
        : jump.page ? root.querySelector<HTMLElement>(`#pg-${jump.page}`) : null;
      target?.scrollIntoView({ block: jump.ref ? "center" : "start", behavior: "smooth" });
    }, 120);
    return () => clearTimeout(t);
  }, [jump, pdf]);

  // 뷰어 가운데를 지나는 쪽을 현재 쪽으로 본다
  useEffect(() => {
    const root = desk.current;
    if (!root) return;
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => { if (e.isIntersecting) setCurrent(Number((e.target as HTMLElement).dataset.page)); });
    }, { root, rootMargin: "-48% 0px -48% 0px" });
    root.querySelectorAll("[data-page]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [pages, width, pdf]);

  const FILTERS: { id: Filter; label: string; color: string }[] = [
    { id: "all", label: "전체", color: "#5C626B" },
    { id: "red", label: "수정", color: TONE.red.line },
    { id: "orange", label: "확인 필요", color: "#F79009" },
    { id: "green", label: "유지", color: TONE.green.line },
    { id: "number", label: "숫자 문제", color: NUMBER_COLOR.line },
  ];

  if (!doc.has_pdf) return <p className={s.muted}>이 분석에는 원문 PDF가 연결되어 있지 않습니다.</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className={s.toolbar}>
        <div className={s.segmented} role="group" aria-label="표시할 판정">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => onFilter(f.id)}>
              <span className={s.swatch} style={{ background: f.color }} />
              {f.label} {count(f.id)}
            </button>
          ))}
        </div>
        <span className={s.toolbarHint}>칸을 누르면 그 판정만 오른쪽에서 차례로 볼 수 있습니다</span>
        <span className={s.spacer} />
        <span className={s.pageIndicator} aria-live="polite"><strong>{current}</strong> / {pages}쪽</span>
        <div className={s.zoom} role="group" aria-label="원문 크기">
          <button type="button" aria-label="작게" disabled={zoom === 0} onClick={() => setZoom((z) => Math.max(0, z - 1))}>−</button>
          <output>{Math.round((WIDTHS[zoom] / WIDTHS[1]) * 100)}%</output>
          <button type="button" aria-label="크게" disabled={zoom === WIDTHS.length - 1} onClick={() => setZoom((z) => Math.min(WIDTHS.length - 1, z + 1))}>+</button>
        </div>
      </div>

      <div ref={desk} className={s.desk} role="region" aria-label="리포트 원문" tabIndex={0}>
        {error ? <p className={s.errorBox}>{error}</p> : (
          <div className={s.pdfStack}>
            {Array.from({ length: pages }, (_, i) => (
              <Page key={i} pdf={pdf} n={i + 1} width={width} ratio={ratio} selected={selected} flash={flash} isDimmed={isDimmed} toneFor={toneFor}
                    onSelect={onSelect} marks={marks.filter((m) => m.page === i + 1)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
