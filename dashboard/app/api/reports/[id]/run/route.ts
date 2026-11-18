import { isValidId, startRun, UserError } from "@/lib/server/library";


/** AI 분석 시작. 화면에서 비용을 확인하고 누른 경우에만 부른다. */
export async function POST(request: Request, ctx: RouteContext<"/api/reports/[id]/run">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  let mock = false;
  try {
    mock = !!(await request.json())?.mock; // 개발 확인용. 화면에서는 보내지 않는다.
  } catch { /* 본문 없음 */ }
  try {
    return Response.json({ entry: await startRun(id, mock) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "분석을 시작하지 못했습니다." }, { status: 400 });
  }
}
