import { readCommonCharts, UserError, writeCommonCharts } from "@/lib/server/library";

/** 모든 회사 공통 '저장된 차트' (지표 구성만 저장, 숫자는 회사마다 화면이 계산) */
export async function GET() {
  return Response.json({ charts: await readCommonCharts() });
}

export async function PUT(request: Request) {
  try {
    const body = await request.json();
    await writeCommonCharts(body?.charts);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "차트를 저장하지 못했습니다." }, { status: 400 });
  }
}
