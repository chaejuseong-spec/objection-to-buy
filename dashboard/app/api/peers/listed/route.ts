import { matchListed } from "@/lib/server/library";

/** 금감원 회사 이름 → 상장 종목코드: ?names=a|b|c → { map: { 이름: 종목코드 또는 null } } */
export async function GET(request: Request) {
  const names = (new URL(request.url).searchParams.get("names") ?? "").split("|").map((x) => x.trim()).filter(Boolean).slice(0, 60);
  return Response.json({ map: await matchListed(names) });
}
