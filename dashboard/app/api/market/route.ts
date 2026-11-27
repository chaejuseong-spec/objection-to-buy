import { marketData, UserError } from "@/lib/server/library";

/** 시장 화면 자료: 코스피·코스닥, 국채 금리, 원·달러 환율, 시장 투자자별 매매대금, 섹터 등락률 순위 (토스증권) */
export async function GET() {
  try {
    return Response.json(await marketData());
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "시장 자료를 받지 못했습니다." }, { status: 400 });
  }
}
