import { setStockSectors, stockSector, UserError } from "@/lib/server/library";

/** 토스증권 섹터: 종목이 속한 섹터들, 기간별 등락률, 구성 종목 시가총액 순위 */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]/sector">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json(await stockSector(ticker));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "섹터 자료를 받지 못했습니다." }, { status: 400 });
  }
}

/** 섹터 직접 추가·빼기: { extra: [섹터 id …] } 를 저장하고 그 종목 섹터 자료를 돌려준다 */
export async function PUT(request: Request, ctx: RouteContext<"/api/stocks/[ticker]/sector">) {
  const { ticker } = await ctx.params;
  try {
    const body = (await request.json().catch(() => ({}))) as { extra?: unknown };
    const ids = Array.isArray(body.extra) ? body.extra.map(String) : [];
    return Response.json(await setStockSectors(ticker, ids));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "섹터를 저장하지 못했습니다." }, { status: 400 });
  }
}
