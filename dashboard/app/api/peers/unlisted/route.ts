import { unlistedPeers } from "@/lib/server/library";

/** 같은 업종의 비상장 정기보고서 제출사 (DART): ?induty=26429 */
export async function GET(request: Request) {
  const induty = new URL(request.url).searchParams.get("induty") ?? "";
  return Response.json({ items: await unlistedPeers(induty) });
}
