import { readStockCharts, UserError, writeStockCharts } from "@/lib/server/library";

/** 검색한 종목의 '내 차트' (설정만 저장, 숫자는 화면이 그때그때 계산) */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]/charts">) {
  const { ticker } = await ctx.params;
  return Response.json({ charts: await readStockCharts(ticker) });
}

export async function PUT(request: Request, ctx: RouteContext<"/api/stocks/[ticker]/charts">) {
  const { ticker } = await ctx.params;
  try {
    const body = await request.json();
    await writeStockCharts(ticker, body?.charts);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "차트를 저장하지 못했습니다." }, { status: 400 });
  }
}
