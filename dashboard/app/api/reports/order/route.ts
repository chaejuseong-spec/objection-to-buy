import { saveOrder } from "@/lib/server/library";

/** 보관함 목록 순서 저장: { ids: ["r-…", …] } */
export async function PUT(req: Request) {
  try {
    const body = (await req.json()) as { ids?: unknown };
    if (!Array.isArray(body.ids) || !body.ids.every((x) => typeof x === "string")) throw new Error("bad");
    await saveOrder(body.ids as string[]);
    return Response.json({ ok: true });
  } catch {
    return Response.json({ error: "순서를 저장하지 못했습니다." }, { status: 400 });
  }
}

/** 순서를 올린 시각의 최신순으로 되돌리기 */
export async function DELETE() {
  await saveOrder(null);
  return Response.json({ ok: true });
}
