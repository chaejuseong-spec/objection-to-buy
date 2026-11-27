import { sectorList, UserError } from "@/lib/server/library";

/** 토스 섹터 전체 목록 (섹터 직접 추가용): { sectors: [{ id, name, path, count }] } */
export async function GET() {
  try {
    return Response.json(await sectorList());
  } catch (e) {
    return Response.json({ error: e instanceof UserError ? e.message : "섹터 목록을 받지 못했습니다.", sectors: [] }, { status: 400 });
  }
}
