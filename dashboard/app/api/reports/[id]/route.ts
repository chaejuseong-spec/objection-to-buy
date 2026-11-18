import { connection } from "next/server";
import { isValidId, readEntry, readResult, removeEntry, saveNote, UserError } from "@/lib/server/library";


/** 리포트 한 건의 상태와, 끝났으면 분석 결과 */
export async function GET(_req: Request, ctx: RouteContext<"/api/reports/[id]">) {
  await connection();
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  const entry = await readEntry(id);
  if (!entry) return Response.json({ error: "보관함에 없는 리포트입니다." }, { status: 404 });
  const result = entry.state === "done" ? await readResult(id) : null;
  return Response.json({ entry, result });
}

/** 보관함에서 지우기 (올린 PDF와 분석 결과 모두). 바로 지우지 않고 library/_trash/ 로 옮긴다 */
export async function DELETE(_req: Request, ctx: RouteContext<"/api/reports/[id]">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  try {
    await removeEntry(id);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "지우지 못했습니다." }, { status: 400 });
  }
}

/** 메모 고치기: { note: "..." } */
export async function PATCH(req: Request, ctx: RouteContext<"/api/reports/[id]">) {
  const { id } = await ctx.params;
  if (!isValidId(id)) return Response.json({ error: "잘못된 주소입니다." }, { status: 400 });
  try {
    const body = (await req.json()) as { note?: unknown };
    if (typeof body.note !== "string") throw new UserError("메모가 오지 않았습니다.");
    return Response.json({ note: await saveNote(id, body.note) });
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "메모를 저장하지 못했습니다." }, { status: 400 });
  }
}
