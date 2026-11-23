import { peerSeries, UserError } from "@/lib/server/library";

/** 동종사 추이 (리포트와 상관없이): ?ticker=088350 또는 ?fisis=H:0010597 */
export async function GET(request: Request) {
  const q = new URL(request.url).searchParams;
  try {
    return Response.json({ peer: await peerSeries(q.get("ticker") ?? "", q.get("fisis") ?? undefined, q.get("corp") ?? undefined) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "동종사 자료를 받지 못했습니다." }, { status: 400 });
  }
}
