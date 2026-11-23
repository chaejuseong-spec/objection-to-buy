"use client";

// 리포트 수치 검증: 리포트의 숫자마다 출처와 공식 데이터 대조 결과. 검토 과정 탭 맨 아래 (2026-11-21 수치와 도표에서 옮김)
import type { Result } from "@/lib/types";
import { Verdict } from "./primitives";
import { numberOrigin } from "./problems";
import { unitAfter } from "./tone";
import s from "./workspace.module.css";

// 리포트 수치 검증 표의 출처 순서: 공식 데이터에서 대조한 것 → 대조하지 않은 실적·표기 → 리포트 추정 (2026-11-21)
const SOURCE_ORDER = ["DART 공시", "DART·KRX", "KRX 시세", "한국은행 ECOS", "KOSIS 통계", "공식 데이터", "회사 실적", "리포트 표기", "리포트 추정"];
const sourceRank = (s: string) => { const i = SOURCE_ORDER.indexOf(s); return i < 0 ? SOURCE_ORDER.length : i; };

export function NumbersTable({ result, onPage, onCheck }: { result: Result; onPage: (p: number) => void; onCheck: (ref: string) => void }) {
  if (!result.rows?.length) return null;
  return (
      <section className={s.block}>
        <details>
          <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 700 }}>리포트 수치 검증</summary>
          <div className={s.tableWrap} style={{ marginTop: 10 }}>
            <table className={s.table} style={{ maxWidth: 960 }}>
              <thead><tr><th>항목</th><th className={s.num}>값</th><th>출처</th><th>검증</th><th className={s.num}>쪽</th></tr></thead>
              <tbody>
                {result.rows
                  .map((r, i) => ({ r, i, g: numberOrigin(result, r) }))
                  .sort((a, b) => sourceRank(a.g.source) - sourceRank(b.g.source) || a.i - b.i)   // 출처별로, 같은 출처는 리포트 순서
                  .map(({ r, g }) => {
                  return (
                    <tr key={r.rid}>
                      <td>{r.item}</td>
                      <td className={s.num}>{r.value}{unitAfter(r.value, r.unit)}</td>
                      <td className={s.originCell}>{g.source}</td>
                      <td className={s.originCell}>
                        {g.ref
                          ? <button type="button" className={s.origin} onClick={() => onCheck(g.ref!)} title="누르면 원문 위치"><Verdict label={g.status ?? ""} />{g.note && <span className={s.muted} style={{ marginLeft: 6 }}>{g.note}</span>}</button>
                          : <span className={s.muted}>{g.note}</span>}
                      </td>
                      <td className={s.num}><span className={s.pageLinks} style={{ margin: 0 }}><button type="button" onClick={() => onPage(r.page)}>{r.page}</button></span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className={s.foot}>출처가 DART 공시·KRX 시세·한국은행 ECOS·KOSIS 통계인 숫자는 그 숫자를 공식 데이터에서 그대로 찾아 맞춰 본 결과입니다(누르면 원문 위치). 리포트 추정은 리포트가 추정하거나 계산한 값이라 맞다 틀리다를 표시하지 않습니다.</p>
        </details>
      </section>
  );
}
