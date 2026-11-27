import { stockPeersSector, UserError } from "@/lib/server/library";

/** 고른 토스 섹터의 경쟁사 표: ?sector=<섹터 id> */
export async function GET(request: Request, ctx: RouteContext<"/api/stocks/[ticker]/peers">) {
  const { ticker } = await ctx.params;
  const sector = new URL(request.url).searchParams.get("sector") ?? "";
  try {
    return Response.json(await stockPeersSector(ticker, sector));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "그 섹터의 경쟁사 표를 만들지 못했습니다." }, { status: 400 });
  }
}
