import { readFile } from "node:fs/promises";
import { connection } from "next/server";
import { isValidId, readEntry, recordPath } from "@/lib/server/library";


/** 평가기록지.md 내려받기 */
export async function GET(_req: Request, ctx: RouteContext<"/api/reports/[id]/record">) {
  await connection();
  const { id } = await ctx.params;
  if (!isValidId(id)) return new Response("잘못된 주소입니다.", { status: 400 });
  try {
    const text = await readFile(recordPath(id), "utf-8");
    const entry = await readEntry(id);
    const name = encodeURIComponent(`평가기록지_${entry?.company ?? id}.md`);
    return new Response(text, {
      headers: { "Content-Type": "text/markdown; charset=utf-8", "Content-Disposition": `attachment; filename*=UTF-8''${name}` },
    });
  } catch {
    return new Response("이 리포트에는 평가기록지가 없습니다.", { status: 404 });
  }
}
