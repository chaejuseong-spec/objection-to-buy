import { isValidId, readCharts, UserError, writeCharts } from "@/lib/server/library";

/** 차트 만들기: 이 리포트에 저장한 차트 카드 (설정만, 숫자는 화면이 그때그때 계산) */
export async function GET(_request: Request, ctx: RouteContext<"/api/reports/[id]/charts">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  return Response.json({ charts: await readCharts(id) });
}

export async function PUT(request: Request, ctx: RouteContext<"/api/reports/[id]/charts">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  try {
    const body = await request.json();
    await writeCharts(id, body?.charts);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "차트를 저장하지 못했습니다." }, { status: 400 });
  }
}
