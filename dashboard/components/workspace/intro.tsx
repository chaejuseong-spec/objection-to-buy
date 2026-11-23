"use client";

// 소개 화면: 처음 한 번만 보인다. 아무 곳이나 누르거나 아무 키나 누르면 작업대로 넘어간다.
// 왼쪽 위 제목을 누르면 다시 볼 수 있다.
import Link from "next/link";
import { useEffect, useRef } from "react";
import s from "./workspace.module.css";

export const INTRO_KEY = "intro-seen";

const STEPS = [
  { who: "추출 담당", what: "올린 PDF에서 주장과 숫자를 원문 그대로 옮겨 적습니다" },
  { who: "리스크 검토자", what: "원문 전체에서 약한 고리를 찾아 이의를 냅니다" },
  { who: "배심원 5명", what: "회계사, 산업 전문가, 공매도 투자자, 통계학자, 행동경제학자가 따로 투표합니다" },
  { who: "판사", what: "표를 모아 쟁점과 판정 근거를 정리합니다" },
];

export function Intro({ onClose }: { onClose: () => void }) {
  const start = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    start.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.key === "Tab" || e.key === "Shift") return;
      if ((e.target as HTMLElement | null)?.dataset?.guide) return; // 설명서 링크에서 Enter
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  return (
    <div className={s.intro} role="dialog" aria-modal="true" aria-labelledby="intro-title" onClick={onClose}>
      <div className={s.introBody}>
        <p className={s.introKicker}>리포트를 믿기 전에, 반대신문부터</p>
        <h1 id="intro-title" className={s.serif}>매수에 이의 있습니다!</h1>
        <p className={s.introLead}>
          <span>기업분석 리포트 PDF를 올리면 AI 리스크 검토자와 배심원단이 투자 논리를 따져 묻고 판정을 냅니다.</span>
          <span>문제가 된 문장은 원문 위에 표시하고, 리포트의 숫자는 다시 계산해 맞는지 보여줍니다.</span>
        </p>

        <ol className={s.introSteps}>
          {STEPS.map((st, i) => (
            <li key={st.who}>
              <span className={s.introNo}>{i + 1}</span>
              <strong>{st.who}</strong>
              <span>{st.what}</span>
            </li>
          ))}
        </ol>

        <div className={s.introActions}>
          <button ref={start} type="button" className={s.btnPrimary} style={{ height: 42, padding: "0 22px" }}>
            시작하기
          </button>
          <Link href="/guide" data-guide="1" className={s.btn} style={{ height: 42, padding: "0 20px", textDecoration: "none" }}
                onClick={(e) => e.stopPropagation()}>
            설명서 보기
          </Link>
        </div>
        <p className={s.introHint}>화면 아무 곳이나 누르거나 아무 키나 누르면 시작합니다. 이 화면과 설명서는 왼쪽 위 제목과 설명서 단추로 다시 볼 수 있습니다.</p>
      </div>
    </div>
  );
}
