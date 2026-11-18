"use client";

// 작업대는 브라우저에서만 그린다(주소창, 이 브라우저에 저장한 메모를 처음부터 읽어야 해서).
import dynamic from "next/dynamic";
import s from "./workspace.module.css";

const Workspace = dynamic(() => import("./Workspace"), {
  ssr: false,
  loading: () => (
    <div className={s.root}>
      <p className={s.muted} style={{ padding: "40px 28px" }}>작업대를 여는 중입니다.</p>
    </div>
  ),
});

export default function WorkspaceClient() {
  return <Workspace />;
}
