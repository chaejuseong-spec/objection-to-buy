import { isValidId, peerSeries, UserError } from "@/lib/server/library";

/** 동종사 추이 비교: 체크한 회사의 재무·보험·업종 지표 추이 (무료: DART·FISIS). ?ticker=088350 또는 ?fisis=H:0010597 (금감원, 비상장 포함) */
export async function GET(request: Request, ctx: RouteContext<"/api/reports/[id]/peer">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  const q = new URL(request.url).searchParams;
  try {
    return Response.json({ peer: await peerSeries(q.get("ticker") ?? "", q.get("fisis") ?? undefined) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "동종사 자료를 받지 못했습니다." }, { status: 400 });
  }
}
