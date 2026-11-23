import { searchStocks, UserError } from "@/lib/server/library";

/** 종목 검색: ?q=삼성 → [{ ticker, name }] (상장사, 최대 12개) */
export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q") ?? "";
  try {
    return Response.json({ items: await searchStocks(q.slice(0, 40)) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "검색하지 못했습니다.", items: [] }, { status: 400 });
  }
}
