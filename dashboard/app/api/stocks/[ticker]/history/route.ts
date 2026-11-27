import { stockHistory, UserError } from "@/lib/server/library";

/** 주가 차트 자료: 토스증권 수정주가 일봉 (못 받으면 받아 둔 KRX 일별 시세, 액면분할 보정) */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]/history">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json(await stockHistory(ticker));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "주가 자료를 받지 못했습니다." }, { status: 400 });
  }
}
