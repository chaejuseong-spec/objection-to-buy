import { lastPrices, UserError } from "@/lib/server/library";

/** 현재가 여러 종목: ?tickers=005930,000660 → { prices: { 종목: { d, c, prev } } } (토스, 10분 저장) */
export async function GET(request: Request) {
  const tickers = (new URL(request.url).searchParams.get("tickers") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  try {
    return Response.json(await lastPrices(tickers));
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "현재가를 받지 못했습니다.", prices: {} }, { status: 400 });
  }
}
