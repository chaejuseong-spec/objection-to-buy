"use client";

// 화면 틀: 상단 바, 왼쪽 사이드바(올리기, 보관함, 리포트 안 이동), 리포트 머리글과 탭
import { useEffect, useLayoutEffect, useRef, useState, type DragEvent } from "react";
import type { LibraryEntry } from "@/lib/types";
import { Chevron, Spinner, Verdict } from "./primitives";
import { toneOf, TONE } from "./tone";
import s from "./workspace.module.css";

export type Section = "summary" | "document" | "figures" | "records" | "journal" | "grading";

export const SECTIONS: { id: Section; label: string }[] = [
  { id: "summary", label: "리포트 요약" },
  { id: "figures", label: "수치와 도표" },
  { id: "document", label: "원문에서 보기" },
  { id: "records", label: "문제 목록" },
  { id: "journal", label: "나의 판단 기록" },
  { id: "grading", label: "채점" },
];

export const displayName = (e: LibraryEntry) => e.company ?? e.fileName.replace(/\.pdf$/i, "");

const Crumb = () => (
  <svg aria-hidden="true" width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#A3A8AE" strokeWidth="1.6">
    <path d="M6 3l5 5-5 5" />
  </svg>
);

export function TopBar({ crumbs, recordHref, onBrand }: { crumbs: string[]; recordHref?: string; onBrand: () => void }) {
  return (
    <header className={s.topbar}>
      <button type="button" className={s.brand} onClick={onBrand} title="소개 화면 다시 보기">
        <svg aria-hidden="true" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <rect x="3" y="2.5" width="11" height="15" stroke="#1A1D21" strokeWidth="1.5" />
          <path d="M6 7h5M6 10h5M6 13h3" stroke="#1A1D21" strokeWidth="1.3" />
          <path d="M13 12.5l4.5-4.5" stroke="#B42318" strokeWidth="2" strokeLinecap="round" />
        </svg>
        매수에 이의 있습니다!
      </button>
      <nav className={s.crumbs} aria-label="현재 위치">
        {crumbs.map((c, i) => (
          <span key={`${c}-${i}`} style={{ display: "contents" }}>
            {i > 0 && <Crumb />}
            <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c}</span>
          </span>
        ))}
      </nav>
      <div className={s.spacer} />
      <a className={s.btn} style={{ height: 32, fontSize: 12.5, textDecoration: "none" }} href="/guide">설명서</a>
      {recordHref ? (
        <a className={s.btn} style={{ height: 32, fontSize: 12.5, textDecoration: "none" }} href={recordHref} download>
          평가기록지 받기
        </a>
      ) : null}
    </header>
  );
}

/** PDF 파일만 골라낸다 */
export const pickPdf = (files: FileList | null | undefined) =>
  Array.from(files ?? []).find((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));

function RowState({ e }: { e: LibraryEntry }) {
  if (e.state === "running") return <span className={s.rowState}><Spinner />분석 중</span>;
  if (e.state === "ready") return <span className={s.rowState}>분석 전</span>;
  if (e.state === "failed") return <span className={s.rowState} style={{ color: TONE.red.ink }}>멈춤</span>;
  return e.grade ? <Verdict label={e.grade} /> : <span className={s.rowState}>완료</span>;
}

export function Sidebar({ entries, total, query, onQuery, selectedId, onSelect, onUpload, uploading, uploadError, section, onSection, navMeta, showNav,
                         customOrder, onMove, onResetOrder, onDelete, onNote }: {
  entries: LibraryEntry[]; total: number; query: string; onQuery: (q: string) => void; selectedId: string | null; onSelect: (id: string) => void;
  onUpload: (f: File) => void; uploading: boolean; uploadError: string | null;
  section: Section; onSection: (s: Section) => void; navMeta: Partial<Record<Section, string>>; showNav: boolean;
  customOrder: boolean; onMove: (id: string, to: number) => void; onResetOrder: () => void;
  onDelete: (e: LibraryEntry) => void; onNote: (id: string, text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // 보관함은 접힌 배너로 두고, 누르면 목록이 펼쳐진다. 리포트를 고르지 않았으면 펼쳐 둔다
  // 보관함 관리 창: 왼쪽 배너를 누르면 가운데에 작은 창으로 뜬다 (Esc 나 바깥을 누르면 닫힘)
  const [libOpen, setLibOpen] = useState(false);
  const dlg = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = dlg.current;
    if (!d) return;
    if (libOpen && !d.open) d.showModal();
    if (!libOpen && d.open) d.close();
  }, [libOpen]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  // 끌어서 순서 바꾸기: 잡은 리포트와, 놓을 자리(그 번호 앞에 끼움)
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const canDrag = !query;            // 찾기 중에는 일부만 보이므로 순서를 바꾸지 않는다
  const endDrag = () => { setDragId(null); setDropAt(null); };
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const selected = entries.find((e) => e.id === selectedId);

  // 선택된 메뉴의 회색 바탕이 메뉴를 바꿀 때 미끄러지듯 따라간다 (처음 나타날 때는 움직이지 않는다)
  const nav = useRef<HTMLElement>(null);
  const [mark, setMark] = useState<{ top: number; height: number; ready: boolean } | null>(null);
  useLayoutEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!el) { setMark(null); return; }
    setMark((m) => ({ top: el.offsetTop, height: el.offsetHeight, ready: !!m }));
  }, [section, showNav, selectedId]);

  const drop = (ev: DragEvent) => {
    ev.preventDefault();
    setOver(false);
    const f = pickPdf(ev.dataTransfer.files);
    if (f) onUpload(f);
  };

  return (
    <aside className={`${s.rail} ${s.scroll}`} aria-label="리포트 탐색">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <button type="button" className={s.btnPrimary} style={{ height: 38 }} aria-expanded={open || uploading} onClick={() => setOpen((v) => !v)}>
          <svg aria-hidden="true" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M8 3v10M3 8h10" />
          </svg>
          새 리포트 올리기
        </button>
        {(open || uploading || uploadError) && (
          <div className={s.dropzone} data-over={over} data-busy={uploading}
               onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
            {uploading ? (
              <>
                <strong style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}><Spinner />올리는 중</strong>
                <span className={s.muted} style={{ fontSize: 11.5, lineHeight: 1.55 }}>쪽수와 예상 사용량을 계산하고 있습니다</span>
              </>
            ) : (
              <>
                <strong style={{ fontSize: 12.5 }}>PDF를 여기에 놓으세요</strong>
                <span className={s.muted} style={{ fontSize: 11.5, lineHeight: 1.55 }}>올린 뒤 비용을 확인하고 분석을 시작합니다</span>
                <label className={s.btn} style={{ height: 30, marginTop: 4, fontSize: 12 }}>
                  파일 고르기
                  <input ref={input} type="file" accept="application/pdf,.pdf" className={s.visuallyHidden}
                         onChange={(e) => { const f = pickPdf(e.target.files); if (f) onUpload(f); e.target.value = ""; }} />
                </label>
              </>
            )}
            {uploadError && <p className={s.uploadError} role="alert">{uploadError}</p>}
          </div>
        )}
      </div>

      <section className={s.libBanner} aria-labelledby="rail-archive">
        <button type="button" className={s.libToggle} aria-haspopup="dialog" aria-expanded={libOpen} onClick={() => setLibOpen(true)}>
          <span className={s.libTitle}>
            <h2 id="rail-archive">보관함</h2>
            <small>{total}건</small>
          </span>
          <span className={s.libCurrent} title={selected ? displayName(selected) : undefined}>
            {selected ? displayName(selected) : "리포트를 고르세요"}
          </span>
          <Chevron size={10} />
        </button>
        <dialog ref={dlg} className={s.libDialog} aria-labelledby="lib-title" onClose={() => { setLibOpen(false); setEditing(null); }}
                onClick={(ev) => { if (ev.target === ev.currentTarget) setLibOpen(false); }}>
          <div className={s.libDialogInner}>
            <div className={s.libDialogHead}>
              <h2 id="lib-title">보관함 <small>{total}건</small></h2>
              <button type="button" className={s.iconBtn} aria-label="닫기" onClick={() => setLibOpen(false)}>✕</button>
            </div>
            <label className={s.railSearch}>
              <svg aria-hidden="true" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="#5C626B" strokeWidth="1.6">
                <circle cx="7" cy="7" r="4.5" />
                <path d="M10.5 10.5L14 14" />
              </svg>
              <input type="search" aria-label="보관함에서 리포트 찾기" placeholder="리포트 찾기 (기업명, 종목코드, 메모)" value={query} onChange={(e) => onQuery(e.target.value)} autoFocus />
            </label>
            <div className={s.railHead}>
              <span className={s.muted} style={{ fontSize: 11.5 }}>
                {entries.length === total ? "끌어서 순서를 바꿀 수 있습니다" : `찾은 리포트 ${entries.length}/${total}건`}
              </span>
              {customOrder && <button type="button" className={s.textBtn} onClick={onResetOrder}>날짜순으로 되돌리기</button>}
            </div>
            <div className={`${s.reportList} ${s.scroll}`}>
              {entries.length === 0 && <p className={s.listEmpty}>{total ? "찾는 리포트가 없습니다" : "아직 올린 리포트가 없습니다"}</p>}
              {entries.map((e, i) => (
                <div key={e.id} className={s.reportItem} aria-current={e.id === selectedId || undefined}
                     draggable={canDrag && editing !== e.id} data-dragging={dragId === e.id || undefined}
                     data-drop={dropAt === i ? "before" : dropAt === i + 1 && i === entries.length - 1 ? "after" : undefined}
                     onDragStart={(ev) => { setDragId(e.id); ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/x-report", e.id); }}
                     onDragOver={(ev) => {
                       if (!dragId) return;
                       ev.preventDefault();
                       const r = ev.currentTarget.getBoundingClientRect();
                       setDropAt(ev.clientY < r.top + r.height / 2 ? i : i + 1);
                     }}
                     onDrop={(ev) => { ev.preventDefault(); if (dragId && dropAt !== null) onMove(dragId, dropAt); endDrag(); }}
                     onDragEnd={endDrag}
                     onKeyDown={(ev) => {
                       if (!canDrag || !ev.altKey || (ev.key !== "ArrowUp" && ev.key !== "ArrowDown")) return;
                       ev.preventDefault();
                       onMove(e.id, ev.key === "ArrowUp" ? Math.max(0, i - 1) : Math.min(entries.length, i + 2));
                     }}>
                  <button type="button" className={s.reportRow} aria-pressed={e.id === selectedId} onClick={() => { onSelect(e.id); setLibOpen(false); }}>
                    <span className={s.company} title={displayName(e)}>{displayName(e)}</span>
                    <RowState e={e} />
                    <span className={s.sub} title={e.reportDate ? `${e.reportDate} 발간` : undefined}>{e.uploadedAt.slice(0, 10)} 올림</span>
                    <span className={s.sub} style={{ justifySelf: "end" }}>{e.demo ? "데모" : e.ticker ?? ""}</span>
                    {e.note && editing !== e.id && <span className={s.reportNote} title={e.note}>{e.note}</span>}
                  </button>
                  {editing === e.id ? (
                    <div className={s.noteEdit}>
                      <textarea aria-label={`${displayName(e)} 메모`} value={draft} autoFocus rows={3} maxLength={2000}
                                onChange={(ev) => setDraft(ev.target.value)}
                                onKeyDown={(ev) => { if (ev.key === "Escape") setEditing(null); if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { onNote(e.id, draft); setEditing(null); } }} />
                      <div>
                        <button type="button" className={s.textBtn} onClick={() => setEditing(null)}>취소</button>
                        <button type="button" className={s.textBtnStrong} onClick={() => { onNote(e.id, draft); setEditing(null); }}>저장</button>
                      </div>
                    </div>
                  ) : (
                    <div className={s.rowActions} role="group" aria-label={`${displayName(e)} 관리`}>
                      {canDrag && <span className={s.dragHint} title="끌어서 순서를 바꿉니다 (키보드: Alt + ↑/↓)" aria-hidden="true">⋮⋮ 끌어서 이동</span>}
                      <button type="button" onClick={() => { setEditing(e.id); setDraft(e.note ?? ""); }}>{e.note ? "메모 고치기" : "메모"}</button>
                      <button type="button" className={s.dangerText} disabled={e.state === "running"} onClick={() => onDelete(e)}>삭제</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </dialog>
      </section>

      {showNav && selected && (
        <nav ref={nav} className={s.railNav} aria-label="리포트 안에서 이동" data-indicator={mark ? "" : undefined}>
          {mark && <span className={s.navIndicator} data-ready={mark.ready || undefined} aria-hidden="true"
                         style={{ transform: `translateY(${mark.top}px)`, height: mark.height }} />}
          <div className={s.railNavHead}>
            <h2>{displayName(selected)}</h2>
            <span>{selected.title ?? selected.fileName}</span>
          </div>
          {SECTIONS.map((item) => (
            <button key={item.id} type="button" className={s.navItem} aria-current={item.id === section ? "page" : undefined} onClick={() => onSection(item.id)}>
              <span>{item.label}</span>
              <span>{navMeta[item.id] ?? ""}</span>
            </button>
          ))}
        </nav>
      )}

      <p className={s.railFoot}>평가는 AI와 코드가 만든 초안입니다. 매수, 보유, 매도는 사람이 정합니다.</p>
    </aside>
  );
}

export function ReportHeader({ name, ticker, sub, facts }: {
  name: string; ticker?: string; sub: string; facts: { label: string; value: string; tone?: ReturnType<typeof toneOf> }[];
}) {
  return (
    <div className={s.reportHeader}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "1 1 360px", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <h1>{name}</h1>
          {ticker && <span className={s.muted}>{ticker}</span>}
        </div>
        <span className={s.muted} style={{ fontSize: 12.5 }}>{sub}</span>
      </div>
      {facts.length > 0 && (
        <dl className={s.headerFacts}>
          {facts.map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd style={{ color: f.tone && f.tone !== "none" ? TONE[f.tone].ink : undefined }}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function SectionTabs({ section, onSelect }: { section: Section; onSelect: (s: Section) => void }) {
  return (
    <div className={s.tabs} role="tablist" aria-label="화면 전환">
      {SECTIONS.map((item) => (
        <button key={item.id} type="button" role="tab" id={`tab-${item.id}`} aria-controls="review-panel"
                aria-selected={item.id === section} className={s.tab} onClick={() => onSelect(item.id)}>
          {item.label}
        </button>
      ))}
    </div>
  );
}
