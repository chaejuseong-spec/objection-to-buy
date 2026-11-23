import { exportXlsx, UserError } from "@/lib/server/library";

/** 기본 종목정보 표 -> 엑셀 파일 (표 + 엑셀 차트, 사용자 서식 규칙). 몸통: { title, periods, rows, notes, sources, ... } */
export async function POST(request: Request) {
  let payload: { title?: string };
  try {
    payload = await request.json();
  } catch {
    return Response.json({ error: "잘못된 요청입니다." }, { status: 400 });
  }
  try {
    const file = await exportXlsx(payload);
    const name = `${(payload.title || "자료").replace(/[\\/:*?"<>|]/g, "_")}.xlsx`;
    return new Response(new Uint8Array(file), {
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename="export.xlsx"; filename*=UTF-8''${encodeURIComponent(name)}`,
      },
    });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "엑셀 파일을 만들지 못했습니다." }, { status: 400 });
  }
}
