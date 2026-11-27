import { stockSeparate, UserError } from "@/lib/server/library";

/** 별도(회사 단독) 기준 재무제표. 화면에서 '별도'를 누를 때 부른다 */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]/separate">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json(await stockSeparate(ticker));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "별도 기준 재무를 받지 못했습니다." }, { status: 400 });
  }
}
