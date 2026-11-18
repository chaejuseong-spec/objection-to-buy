import { connection } from "next/server";
import { addUpload, hasCustomOrder, listEntries, UserError } from "@/lib/server/library";


/** 보관함 목록 */
export async function GET() {
  await connection();
  return Response.json({ entries: await listEntries(), customOrder: hasCustomOrder() });
}

/** PDF 올리기. 올리기만 하고 분석은 시작하지 않는다(비용 확인을 먼저 받는다). */
export async function POST(request: Request) {
  try {
    const form = await request.formData();
    const file = form.get("file");
    if (!(file instanceof File)) throw new UserError("파일이 오지 않았습니다.");
    return Response.json({ entry: await addUpload(file) });
  } catch (e) {
    const message = e instanceof UserError ? e.message : "파일을 저장하지 못했습니다.";
    return Response.json({ error: message }, { status: 400 });
  }
}
