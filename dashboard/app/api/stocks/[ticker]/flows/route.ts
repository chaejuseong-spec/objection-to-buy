import { stockFlows, UserError } from "@/lib/server/library";

/** 수급 자료: 토스증권 투자자별 매매동향·프로그램매매·공매도·신용·대차 (일별 1년), 매수 유의사항 */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]/flows">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json(await stockFlows(ticker));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "수급 자료를 받지 못했습니다." }, { status: 400 });
  }
}
