import { connection } from "next/server";
import type { Score } from "@/lib/grading";
import { readGrading, recordScore, saveGrading } from "@/lib/server/grading";
import { isValidId, readEntry } from "@/lib/server/library";

async function doneEntry(id: string) {
  if (!isValidId(id)) return null;
  const entry = await readEntry(id);
  return entry && entry.state === "done" ? entry : null;
}
const nameOf = (e: { company?: string; fileName: string }) => e.company ?? e.fileName;
const fail = (msg: string, status = 400) => Response.json({ error: msg }, { status });

/** 정답지, 채점 표시, 기록한 점수 */
export async function GET(_req: Request, ctx: RouteContext<"/api/reports/[id]/grading">) {
  await connection();
  const { id } = await ctx.params;
  if (!(await doneEntry(id))) return fail("분석이 끝난 리포트만 채점할 수 있습니다.", 404);
  return Response.json(await readGrading(id));
}

/** 정답지나 채점 표시 저장 (화면에서 고칠 때마다 자동으로) */
export async function PUT(request: Request, ctx: RouteContext<"/api/reports/[id]/grading">) {
  const { id } = await ctx.params;
  const entry = await doneEntry(id);
  if (!entry) return fail("분석이 끝난 리포트만 채점할 수 있습니다.", 404);
  try {
    const body = (await request.json()) as { answer?: unknown; grading?: unknown };
    return Response.json(await saveGrading(id, body, nameOf(entry)));
  } catch {
    return fail("채점 내용을 저장하지 못했습니다.");
  }
}

/** 지금 점수를 기록한다 */
export async function POST(request: Request, ctx: RouteContext<"/api/reports/[id]/grading">) {
  const { id } = await ctx.params;
  const entry = await doneEntry(id);
  if (!entry) return fail("분석이 끝난 리포트만 채점할 수 있습니다.", 404);
  try {
    const { score } = (await request.json()) as { score: Score };
    return Response.json({ history: await recordScore(id, score, nameOf(entry), !!entry.mock) });
  } catch {
    return fail("점수를 기록하지 못했습니다.");
  }
}
