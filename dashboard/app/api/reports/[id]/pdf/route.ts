import { readFile } from "node:fs/promises";
import { connection } from "next/server";
import { isValidId, pdfPath } from "@/lib/server/library";


/** 올린 원문 PDF */
export async function GET(_req: Request, ctx: RouteContext<"/api/reports/[id]/pdf">) {
  await connection();
  const { id } = await ctx.params;
  if (!isValidId(id)) return new Response("잘못된 주소입니다.", { status: 400 });
  try {
    const buf = await readFile(pdfPath(id));
    return new Response(new Uint8Array(buf), { headers: { "Content-Type": "application/pdf", "Cache-Control": "private, max-age=3600" } });
  } catch {
    return new Response("원문이 없습니다.", { status: 404 });
  }
}
