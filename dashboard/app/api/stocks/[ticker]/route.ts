import { stockCompany, UserError } from "@/lib/server/library";

/** 리포트 없이 검색한 종목의 기본 종목정보. GET: 저장된 것(없으면 만든다), POST: 시세만 새로 */
export async function GET(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json({ company: await stockCompany(ticker) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "종목정보를 만들지 못했습니다." }, { status: 400 });
  }
}

export async function POST(_request: Request, ctx: RouteContext<"/api/stocks/[ticker]">) {
  const { ticker } = await ctx.params;
  try {
    return Response.json({ company: await stockCompany(ticker, true) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "시세를 새로 받지 못했습니다." }, { status: 400 });
  }
}
