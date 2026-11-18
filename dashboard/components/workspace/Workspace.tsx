"use client";

// 최상위 화면. 보관함 목록, 올리기, 분석 시작, 선택, 내 메모 저장을 모두 여기서 관리한다.
// 분석 결과는 서버(/api/reports)에서 받아 보여주기만 하고 계산하지 않는다.
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, useLayoutEffect } from "react";
import type { LibraryEntry, Result } from "@/lib/types";
import { displayName, pickPdf, ReportHeader, SectionTabs, Sidebar, SECTIONS, TopBar, type Section } from "./chrome";
import { DocumentView, type Jump } from "./document-view";
import { FiguresView } from "./figures-view";
import { GradingView, ScorePanel } from "./grading-view";
import { score, type AnswerKey, type Grading, type QualityRun, type ScoreRecord } from "@/lib/grading";
import { Intro, INTRO_KEY } from "./intro";
import { DetailPanel, EntryPanel, JournalPanel, SummaryPanel, WelcomePanel } from "./inspector";
import { problems } from "./problems";
import { RowsProvider } from "./refs";
import { Skeleton } from "./primitives";

const EMPTY_ROWS: [] = [];
import { FailedView, ReadyView, RunningView, WelcomeView } from "./job-views";
import { toneOf, unitAfter } from "./tone";
import { ChecklistBlock, EMPTY_FORM, JournalView, RecordsView, SummaryView, type JournalEntry, type JournalForm } from "./views";
import s from "./workspace.module.css";

type Detail = { entry: LibraryEntry; result: Result | null };
type GradeData = { answer: AnswerKey; grading: Grading; history: ScoreRecord[]; quality?: QualityRun[] };
type Journal = { form: JournalForm; history: JournalEntry[]; savedAt?: string; dirty?: boolean };

const load = <T,>(key: string, fallback: T): T => {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
};
const save = (key: string, value: unknown) => {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장소를 못 쓰면 화면에서만 유지 */ }
};

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error ?? "서버와 연결하지 못했습니다. 웹사이트 창(파란 창)이 켜져 있는지 확인하세요.");
  return body as T;
}

const initial = () => {
  const p = new URLSearchParams(window.location.search);
  const id = p.get("r") ?? load<{ id: string | null }>("last-report", { id: null }).id;
  const tab = p.get("tab") as Section | null;
  return { id, tab: tab && SECTIONS.some((x) => x.id === tab) ? tab : ("summary" as Section) };
};
const loadChecks = (id: string) => load<Record<string, boolean>>(`checks:${id}`, {});
const loadJournal = (id: string) => load<Journal>(`journal:${id}`, { form: EMPTY_FORM, history: [] });

export default function Workspace() {
  const [first] = useState(initial);
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null);
  const [customOrder, setCustomOrder] = useState(false);
  // 짧은 알림 (실제로 저장·업로드·삭제가 끝났을 때만)
  const [toasts, setToasts] = useState<{ id: number; text: string; tone: "ok" | "error" }[]>([]);
  const notify = useCallback((text: string, tone: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 6000 : 3200);
  }, []);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(first.id);
  const [details, setDetails] = useState<Record<string, Detail>>({});
  const [section, setSection] = useState<Section>(first.tab);
  const [selRef, setSelRef] = useState<Record<string, string>>({});
  const [jump, setJump] = useState<Jump | null>(null);
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, Record<string, boolean>>>(() => (first.id ? { [first.id]: loadChecks(first.id) } : {}));
  const [journals, setJournals] = useState<Record<string, Journal>>(() => (first.id ? { [first.id]: loadJournal(first.id) } : {}));
  const [dragDepth, setDragDepth] = useState(0);
  const mainRef = useRef<HTMLElement>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  const [intro, setIntro] = useState(() => {
    try { return !localStorage.getItem(INTRO_KEY); } catch { return false; }
  });
  const closeIntro = useCallback(() => {
    setIntro(false);
    try { localStorage.setItem(INTRO_KEY, "1"); } catch { /* 저장 못 해도 이번에는 닫힌다 */ }
  }, []);

  // 다른 리포트나 탭으로 옮기면 가운데 화면만 맨 위로 돌린다 (양옆 칸은 그대로)
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [selectedId, section]);

  useEffect(() => {
    const p = new URLSearchParams();
    if (selectedId) { p.set("r", selectedId); p.set("tab", section); }
    window.history.replaceState(null, "", p.toString() ? `?${p}` : window.location.pathname);
    save("last-report", { id: selectedId });
  }, [selectedId, section]);

  /* ───── 보관함 목록: 분석 중이면 자주, 아니면 가끔 새로 받는다 ───── */
  const refresh = useCallback(() => {
    api<{ entries: LibraryEntry[]; customOrder?: boolean }>("/api/reports")
      .then(({ entries, customOrder }) => { setEntries(entries); setCustomOrder(!!customOrder); setListError(null); })
      .catch((e: Error) => setListError(e.message));
  }, []);
  const anyRunning = !!entries?.some((e) => e.state === "running");
  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const t = setInterval(refresh, anyRunning ? 3000 : 20000);
    return () => { clearTimeout(first); clearInterval(t); };
  }, [refresh, anyRunning]);

  // 지워졌거나 없는 리포트를 가리키면 첫 화면을 보여준다
  const entry = entries?.find((e) => e.id === selectedId) ?? null;

  /* ───── 선택한 리포트의 결과 ───── */
  const detail = selectedId ? details[selectedId] : undefined;
  useEffect(() => {
    if (!entry || entry.state !== "done" || detail?.result) return;
    let dead = false;
    api<Detail>(`/api/reports/${entry.id}`).then((d) => { if (!dead) setDetails((m) => ({ ...m, [entry.id]: d })); }).catch((e) => setActionError(e.message));
    return () => { dead = true; };
  }, [entry, detail?.result]);
  const result = entry?.state === "done" ? detail?.result ?? null : null;

  /* ───── 채점: 탭을 열 때 받아 오고, 고치면 잠시 뒤 자동 저장 ───── */
  const [grades, setGrades] = useState<Record<string, GradeData>>({});
  const [gradeSave, setGradeSave] = useState("");
  const [recording, setRecording] = useState(false);
  const gradeTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const gradePending = useRef<Record<string, Partial<GradeData>>>({});
  const grade = selectedId ? grades[selectedId] : undefined;
  useEffect(() => {
    if (section !== "grading" || !entry || entry.state !== "done" || grade) return;
    let dead = false;
    api<GradeData>(`/api/reports/${entry.id}/grading`)
      .then((d) => { if (!dead) setGrades((m) => ({ ...m, [entry.id]: d })); })
      .catch((e: Error) => setGradeSave(e.message));
    return () => { dead = true; };
  }, [section, entry, grade]);
  const updateGrade = (id: string, patch: Partial<GradeData>) => {
    setGrades((m) => ({ ...m, [id]: { ...m[id], ...patch } }));
    gradePending.current[id] = { ...gradePending.current[id], ...patch };
    setGradeSave("저장하는 중");
    clearTimeout(gradeTimers.current[id]);
    gradeTimers.current[id] = setTimeout(() => {
      const body = gradePending.current[id];
      delete gradePending.current[id];
      api(`/api/reports/${id}/grading`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(() => setGradeSave("자동 저장됨"))
        .catch((e: Error) => setGradeSave(`저장하지 못했습니다: ${e.message}`));
    }, 600);
  };

  /* ───── 원문 표시 순서 (이전/다음 이동) ───── */
  const order = useMemo(() => {
    if (!result) return [];
    // 화면에 칠해지는 곳만 (통과한 곳, 맞는 것으로 확인된 숫자는 빼고) 원문 순서대로
    const ok = new Set(result.numbers.results.filter((x) => x.status === "일치").map((x) => x.check_id));
    const refs = Array.from(new Set(result.marks.filter((m) => !m.pass && !(m.kind === "number" && ok.has(m.ref))).map((m) => m.ref)));
    result.claims.forEach((c) => { if (!refs.includes(c.id)) refs.push(c.id); });
    return refs;
  }, [result]);
  const defaultRef = result ? (result.claims.find((c) => c.verdict === "수정")?.id ?? order[0] ?? null) : null;
  const selected = selectedId ? selRef[selectedId] ?? defaultRef : null;
  const select = (ref: string) => selectedId && setSelRef((m) => ({ ...m, [selectedId]: ref }));
  const step = (d: number) => {
    if (!order.length) return;
    const i = Math.max(0, order.indexOf(selected ?? ""));
    const next = order[Math.min(order.length - 1, Math.max(0, i + d))];      // 처음·끝에서 멈춘다 (버튼도 비활성)
    if (next === selected) return;
    select(next);
    if (section === "document") setJump({ ref: next, nonce: Date.now() });
  };
  const openRef = (ref: string) => { select(ref); setSection("document"); setJump({ ref, nonce: Date.now() }); };
  const goSection = (next: Section) => {
    setSection(next);
    if (next === "document" && section !== "document" && selected) setJump({ ref: selected, nonce: Date.now() }); // 오른쪽 패널과 같은 곳을 보여준다
  };
  const openPage = (page: number) => { setSection("document"); setJump({ page, nonce: Date.now() }); };

  /* ───── 동작 ───── */
  const chooseReport = (id: string | null) => {
    if (id) {
      setChecks((c) => (c[id] ? c : { ...c, [id]: loadChecks(id) }));
      setJournals((j) => (j[id] ? j : { ...j, [id]: loadJournal(id) }));
    }
    setSelectedId(id);
    setActionError(null);
    setUploadError(null);
    if (id && id !== selectedId) setSection("summary");
  };

  const upload = async (file: File) => {
    if (uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { entry } = await api<{ entry: LibraryEntry }>("/api/reports", { method: "POST", body: fd });
      setEntries((list) => [entry, ...(list ?? []).filter((e) => e.id !== entry.id)]);
      chooseReport(entry.id);
      notify(`${file.name}을(를) 보관함에 올렸습니다`);
    } catch (e) {
      setUploadError((e as Error).message);
      notify(`올리지 못했습니다: ${(e as Error).message}`, "error");
    } finally {
      setUploading(false);
    }
  };

  const start = async () => {
    if (!entry) return;
    setBusy(true);
    setActionError(null);
    try {
      const { entry: updated } = await api<{ entry: LibraryEntry }>(`/api/reports/${entry.id}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setEntries((list) => (list ?? []).map((e) => (e.id === updated.id ? updated : e)));
      setDetails((m) => { const n = { ...m }; delete n[updated.id]; return n; });
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!entry) return;
    try {
      await api(`/api/reports/${entry.id}`, { method: "DELETE" });
      try { localStorage.removeItem(`checks:${entry.id}`); localStorage.removeItem(`journal:${entry.id}`); } catch { /* 무시 */ }
      setEntries((list) => (list ?? []).filter((e) => e.id !== entry.id));
      chooseReport(null);
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  /* ───── 보관함 관리: 순서, 삭제, 메모 ───── */
  /** id 를 to 번째 자리 앞에 끼운다 (끌어다 놓기, Alt+↑/↓) */
  const moveEntry = async (id: string, to: number) => {
    const list = [...(entries ?? [])];
    const from = list.findIndex((e) => e.id === id);
    if (from < 0) return;
    const [item] = list.splice(from, 1);
    const at = Math.max(0, Math.min(list.length, from < to ? to - 1 : to));
    if (at === from) return;
    list.splice(at, 0, item);
    setEntries(list);
    setCustomOrder(true);
    try {
      await api("/api/reports/order", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: list.map((e) => e.id) }) });
    } catch (e) {
      notify(`순서를 저장하지 못했습니다: ${(e as Error).message}`, "error");
      refresh();
    }
  };
  const resetOrder = async () => {
    try {
      await api("/api/reports/order", { method: "DELETE" });
      notify("올린 날짜 최신순으로 되돌렸습니다");
    } catch (e) {
      notify(`되돌리지 못했습니다: ${(e as Error).message}`, "error");
    }
    refresh();
  };
  const deleteEntry = async (e: LibraryEntry) => {
    const name = displayName(e);
    if (!window.confirm(`'${name}'을(를) 보관함에서 지울까요?

분석 결과와 원문 PDF가 함께 빠집니다. 완전히 지우지 않고 library/_trash 폴더로 옮겨 두므로 필요하면 되살릴 수 있습니다.`)) return;
    try {
      await api(`/api/reports/${e.id}`, { method: "DELETE" });
      try { localStorage.removeItem(`checks:${e.id}`); localStorage.removeItem(`journal:${e.id}`); } catch { /* 무시 */ }
      setEntries((list) => (list ?? []).filter((x) => x.id !== e.id));
      if (selectedId === e.id) chooseReport(null);
      notify(`'${name}'을(를) 지웠습니다 (휴지통 폴더로 옮김)`);
    } catch (err) {
      notify(`지우지 못했습니다: ${(err as Error).message}`, "error");
    }
  };
  const saveNoteFor = async (id: string, text: string) => {
    try {
      const { note } = await api<{ note: string }>(`/api/reports/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: text }) });
      setEntries((list) => (list ?? []).map((e) => (e.id === id ? { ...e, note } : e)));
      notify(note ? "메모를 저장했습니다" : "메모를 비웠습니다");
    } catch (e) {
      notify(`메모를 저장하지 못했습니다: ${(e as Error).message}`, "error");
    }
  };

  const toggleCheck = (key: string) => {
    if (!selectedId) return;
    setChecks((c) => {
      const next = { ...(c[selectedId] ?? {}), [key]: !c[selectedId]?.[key] };
      save(`checks:${selectedId}`, next);
      return { ...c, [selectedId]: next };
    });
  };

  const journal: Journal = (selectedId && journals[selectedId]) || { form: EMPTY_FORM, history: [] };
  const setJournal = (j: Journal) => {
    if (!selectedId) return;
    setJournals((m) => ({ ...m, [selectedId]: j }));
    save(`journal:${selectedId}`, j);
  };
  const updateForm = <K extends keyof JournalForm>(k: K, v: JournalForm[K]) => setJournal({ ...journal, form: { ...journal.form, [k]: v }, dirty: true });
  const saveJournal = () => {
    let note = (journal.form.final || journal.form.reason || journal.form.initial || "메모 없이 결정만 남김").trim();
    if (note.length > 90) note = `${note.slice(0, 90)}…`;
    const at = new Date().toISOString();
    const f = journal.form;
    const changed = !!(f.initialAction && f.action && f.initialAction !== f.action);
    setJournal({ ...journal, history: [{ at, action: f.action || "결정 전", agreement: f.agreement || "동의 여부 미정", note,
                                         before: f.initialAction || undefined, changed }, ...journal.history], savedAt: at, dirty: false });
    notify("판단을 저장했습니다");
  };
  const exportJournal = () => {
    if (!result) return;
    const q = (v: string) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const f = journal.form;
    const lines = [["항목", "내용"], ["리포트", `${result.meta.name} ${result.meta.report_date}`], ["판정", result.grade.value],
      ["구분", "아래는 나의 판단입니다. AI 검증 결과(판정, 근거)와는 따로 저장됩니다."],
      ["첫인상 (AI 결과 보기 전)", f.initial], ["처음 행동 (AI 결과 보기 전)", f.initialAction],
      ["투자의견 동의", f.agreement], ["행동 (반대신문 결과를 본 뒤)", f.action],
      ["판단이 바뀌었나", f.initialAction && f.action ? (f.initialAction === f.action ? "아니오" : `예 (${f.initialAction} → ${f.action})`) : ""],
      ["최종 판단", f.final], ["결정 이유", f.reason], ["반론 검토 메모", f.notes],
      ...journal.history.map((h) => [`이력 ${new Date(h.at).toLocaleString("ko-KR")}`,
        `${h.before ? `${h.before} → ` : ""}${h.action}${h.changed ? " (바뀜)" : ""}, ${h.agreement}, ${h.note}`])];
    const blob = new Blob(["﻿" + lines.map((l) => l.map(q).join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `내판단_${result.meta.name}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const saveState = journal.dirty
    ? { label: "아직 저장하지 않은 수정 내용이 있습니다", color: "#B54708" }
    : journal.savedAt
      ? { label: `${new Date(journal.savedAt).toLocaleString("ko-KR", { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" })}에 저장함`, color: "#067647" }
      : { label: "아직 저장한 판단이 없습니다", color: "#5C626B" };

  /* ───── 화면 어디에나 PDF를 끌어다 놓기 ───── */
  const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  const rootDrag = {
    onDragEnter: (e: DragEvent) => { if (hasFiles(e)) setDragDepth((d) => d + 1); },
    onDragLeave: (e: DragEvent) => { if (hasFiles(e)) setDragDepth((d) => Math.max(0, d - 1)); },
    onDragOver: (e: DragEvent) => { if (hasFiles(e)) e.preventDefault(); },
    onDrop: (e: DragEvent) => {
      setDragDepth(0);
      if (e.defaultPrevented || !hasFiles(e)) return; // 올리기 칸이 이미 받았음
      e.preventDefault();
      const f = pickPdf(e.dataTransfer.files);
      if (f) upload(f);
      else setUploadError("PDF 파일만 올릴 수 있습니다.");
    },
  };

  /* ───── 화면 ───── */
  const q = query.trim().toLowerCase();
  const shown = (entries ?? []).filter((e) => !q || [displayName(e), e.ticker ?? "", e.fileName, e.note ?? ""].some((x) => x.toLowerCase().includes(q)));
  const running = entries?.find((e) => e.state === "running");
  const sectionLabel = SECTIONS.find((x) => x.id === section)!.label;
  const ready = entry?.state === "done" && !!result;
  const navMeta: Partial<Record<Section, string>> = result ? {
    summary: result.grade.value,
    document: `${order.length}곳`,
    figures: `${result.kpis.length + result.tables.length}개`,
    records: `${problems(result).length}건`,
    journal: journal.dirty ? "작성 중" : journal.savedAt ? "저장됨" : "",
    grading: grade ? `문제 ${grade.answer.items.length}개` : "",
  } : {};

  let main: React.ReactNode;
  let inspector: React.ReactNode;
  if (!entries && !listError) {
    main = <Skeleton label="보관함을 불러오는 중입니다" />;
  } else if (!entry) {
    main = <WelcomeView onUpload={upload} uploading={uploading} error={uploadError ?? listError} />;
    inspector = <WelcomePanel total={entries?.length ?? 0} running={running} />;
  } else if (entry.state === "ready") {
    main = <ReadyView entry={entry} busy={busy} error={actionError} onStart={start} onDelete={remove}
                      otherRunning={running && running.id !== entry.id ? displayName(running) : undefined} />;
    inspector = <EntryPanel entry={entry} />;
  } else if (entry.state === "running") {
    main = <RunningView entry={entry} />;
    inspector = <EntryPanel entry={entry} />;
  } else if (entry.state === "failed") {
    main = <FailedView entry={entry} busy={busy} error={actionError} onRetry={start} onDelete={remove} />;
    inspector = <EntryPanel entry={entry} />;
  } else if (!result) {
    main = actionError ? <p className={s.muted} style={{ padding: "40px 0" }}>{actionError}</p> : <Skeleton label="결과를 불러오는 중입니다" />;
  } else {
    const checked = checks[entry.id] ?? {};
    const at = order.indexOf(selected ?? "");
    const position = at >= 0 ? `${at + 1} / ${order.length}` : "원문 표시 밖";      // 원문에 칠해지지 않은 항목은 순번이 없다
    const canPrev = at > 0, canNext = at < order.length - 1;
    const pickAndShow = (ref: string) => { select(ref); setJump({ ref, nonce: Date.now() }); };
    if (section === "summary") {
      main = <SummaryView result={result} onOpenClaim={openRef} onPage={openPage}
                          checklist={<ChecklistBlock result={result} checked={checked} onToggleCheck={toggleCheck} onOpen={openRef} />} />;
      inspector = <SummaryPanel result={result} onOpenDoc={() => openRef(defaultRef ?? order[0])} onOpenRef={openRef} onAll={() => goSection("records")} />;
    } else if (section === "document") {
      main = <DocumentView key={entry.id} reportId={entry.id} result={result} selected={selected} onSelect={select} jump={jump} />;
      inspector = <DetailPanel result={result} selected={selected} checked={checked} onToggleCheck={toggleCheck} onStep={step} position={position}
                               canPrev={canPrev} canNext={canNext} onOpenRef={(ref) => setJump({ ref, nonce: Date.now() })} onSelect={pickAndShow} />;
    } else if (section === "figures") {
      main = <FiguresView reportId={entry.id} result={result} onPage={openPage} onCheck={openRef} />;
      inspector = <SummaryPanel result={result} onOpenDoc={() => openRef(defaultRef ?? order[0])} onOpenRef={openRef} onAll={() => goSection("records")} />;
    } else if (section === "records") {
      main = <RecordsView result={result} selected={selected} onSelect={select} onOpenClaim={openRef} onPage={openPage} />;
      inspector = <DetailPanel result={result} selected={selected} checked={checked} onToggleCheck={toggleCheck} onStep={step} position={position}
                               canPrev={canPrev} canNext={canNext} onOpenRef={openRef} onSelect={select} />;
    } else if (section === "grading") {
      if (!grade) {
        main = <p className={s.muted} style={{ padding: "40px 0" }}>{gradeSave || "채점 내용을 불러오는 중입니다."}</p>;
      } else {
        const id = entry.id;
        const record = async () => {
          setRecording(true);
          try {
            const { history } = await api<{ history: ScoreRecord[] }>(`/api/reports/${id}/grading`, {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ score: score(result, grade.answer, grade.grading) }),
            });
            setGrades((m) => ({ ...m, [id]: { ...m[id], history } }));
            setGradeSave("점수를 기록했습니다");
          } catch (e) {
            setGradeSave((e as Error).message);
          } finally {
            setRecording(false);
          }
        };
        main = <GradingView result={result} answer={grade.answer} grading={grade.grading} saveLabel={gradeSave || "고치면 자동으로 저장됩니다"}
                            onAnswer={(answer) => updateGrade(id, { answer })} onGrading={(grading) => updateGrade(id, { grading })} />;
        inspector = <ScorePanel result={result} answer={grade.answer} grading={grade.grading} history={grade.history} quality={grade.quality ?? []} busy={recording} onRecord={record} />;
      }
    } else {
      main = <JournalView name={result.meta.name} opinion={result.meta.opinion} target={result.meta.target_price} form={journal.form}
                          saveLabel={saveState.label} saveColor={saveState.color} onChange={updateForm} onSave={saveJournal}
                          onReset={() => setJournal({ ...journal, form: EMPTY_FORM, dirty: true })} onExport={exportJournal} />;
      inspector = <JournalPanel result={result} saveLabel={saveState.label} saveColor={saveState.color} history={journal.history} onOpenClaim={openRef} />;
    }
  }

  const crumbs = ["보관함", ...(entry ? [displayName(entry)] : ["새 리포트"]), ...(ready ? [sectionLabel] : [])];
  const gradeTone = toneOf(result?.grade.value);

  // 가운데 머리글과 오른쪽 패널 첫 칸의 아래 선을 같은 높이로: 둘의 원래 높이를 재서 큰 쪽에 맞춘다
  useLayoutEffect(() => {
    const root = layoutRef.current;
    if (!root) return;
    const fit = () => {
      root.style.removeProperty("--head-h");
      const head = root.querySelector<HTMLElement>("main [class*='reportHeader']");
      const first = root.querySelector<HTMLElement>("aside[aria-label='검토 패널'] [class*='panelBlock']");
      if (!head || !first) return;
      // 오른쪽 첫 칸이 긴 글(주장 원문 등)이면 머리글을 늘리지 않는다 (차이가 작을 때만 맞춘다)
      if (Math.abs(head.offsetHeight - first.offsetHeight) > 60) return;
      root.style.setProperty("--head-h", `${Math.ceil(Math.max(head.offsetHeight, first.offsetHeight))}px`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [section, selected, result, entry?.id]);

  return (
    <div className={s.root} {...rootDrag}>
      <TopBar crumbs={crumbs} onBrand={() => setIntro(true)} recordHref={ready && entry?.hasRecord ? `/api/reports/${entry.id}/record` : undefined} />
      <RowsProvider rows={result?.rows ?? EMPTY_ROWS}>
      <div ref={layoutRef} className={s.layout}>
        <Sidebar entries={shown} total={entries?.length ?? 0} query={query} onQuery={setQuery} selectedId={selectedId} onSelect={chooseReport} onUpload={upload}
                 uploading={uploading} uploadError={entry ? uploadError : null} section={section}
                 onSection={goSection} navMeta={navMeta} showNav={ready}
                 customOrder={customOrder} onMove={moveEntry} onResetOrder={resetOrder} onDelete={deleteEntry} onNote={saveNoteFor} />

        <main ref={mainRef} className={`${s.main} ${s.scroll}`}>
          {entry && (
            ready && result ? (
              <ReportHeader name={result.meta.name} ticker={result.meta.ticker}
                            sub={`${result.meta.report_date} 발간, ${entry.demo ? "데모 리포트" : entry.fileName}, 원문 ${result.document.pages}쪽, 배심원 ${result.logic.n_jurors}명`}
                            facts={[
                              { label: "원문 의견", value: result.meta.opinion },
                              { label: "목표주가", value: `${result.meta.target_price}${unitAfter(String(result.meta.target_price ?? ""), "원")}` },
                              { label: "검증 판정", value: result.grade.value, tone: gradeTone },
                            ]} />
            ) : (
              <ReportHeader name={displayName(entry)} sub={`${new Date(entry.uploadedAt).toLocaleString("ko-KR")} 올림`} facts={[]} />
            )
          )}
          {ready && <SectionTabs section={section} onSelect={goSection} />}
          {ready && result?.run.mock && (
            <p className={s.demoNote} style={{ marginTop: 14 }}>
              시연용 결과입니다. 논리 평가(리스크 검토자, 배심원, 판사)는 정해진 가짜 답으로 채웠고, 숫자 검증과 원문 대조는 실제 계산입니다.
            </p>
          )}
          <div key={`${entry?.id ?? "none"}-${ready ? section : entry?.state ?? ""}`} id="review-panel" role={ready ? "tabpanel" : undefined}
               aria-labelledby={ready ? `tab-${section}` : undefined} className={`${s.content} ${s.enter}`}>
            {main}
          </div>
        </main>

        <aside className={`${s.inspector} ${s.scroll}`} aria-label="검토 패널">
          <div key={`${entry?.id ?? "none"}-${section}-${selected ?? ""}`} className={s.enterSoft}>{inspector}</div>
        </aside>
      </div>
      </RowsProvider>
      <div className={s.toasts} aria-live="polite" role="status">
        {toasts.map((t) => <div key={t.id} className={s.toast} data-tone={t.tone}>{t.text}</div>)}
      </div>
      {intro && <Intro onClose={closeIntro} />}
      {dragDepth > 0 && (
        <div className={s.dropOverlay} aria-hidden="true"><strong>놓으면 보관함에 올립니다</strong></div>
      )}
    </div>
  );
}
