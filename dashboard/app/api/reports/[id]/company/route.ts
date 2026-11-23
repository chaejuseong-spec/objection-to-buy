import { isValidId, refreshCompany, UserError } from "@/lib/server/library";

/** 기본 종목정보 시세 새로 받기 (무료: KRX·DART). 화면이 탭을 열 때 저장된 시세가 오늘 확인한 것이 아니면 부른다. */
export async function POST(_request: Request, ctx: RouteContext<"/api/reports/[id]/company">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  try {
    return Response.json({ company: await refreshCompany(id) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "시세를 새로 받지 못했습니다." }, { status: 400 });
  }
}
