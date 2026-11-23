"""공식 데이터 대조가 같이 쓰는 것: 결과 모양, 숫자 검증 목록에 붙이기, 형광펜, 요약, 이미 끝난 분석에 더하기.

결과 한 건의 모양 (숫자 검증 목록과 같고, 출처가 더 붙는다)
  check_id  D-1(공시), X-1(시장) …     kind    dart / krx
  status    일치 / 불일치 / 검증 불가   hue     green / red / amber (원문 형광펜 색)
  stated    리포트 표기                computed 공식 값(단위 붙은 글자)
  source    {provider, display: [[이름, 값]…], short, url, fetched_at, report_date, timing}
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..number_check import MATCH, MISMATCH, UNKNOWN, _show
from ..schemas import DataRow, Report

UNVERIFIABLE = "검증 불가"
HUE = {MATCH: "green", MISMATCH: "red"}


def make_result(kind: str, cid: str, row: DataRow, name: str, status: str, reason: str, **extra) -> dict:
    return {"check_id": cid, "kind": kind, "name": name, "expr": "", "stated_rid": row.rid, "rids": [row.rid],
            "note": "", "status": status, "hue": HUE.get(status, "amber"), "computed": None,
            "stated": _show(row.value, row.unit), "diff_pct": None, "pages": [row.page] if row.page else [],
            "reason": reason, **extra}


def _exts(exts: Iterable[Optional[dict]]) -> List[dict]:
    return [e for e in exts if e and e.get("results")]


def merge(nc: dict, *exts: Optional[dict]) -> dict:
    """공식 데이터 대조 결과를 숫자 검증 목록 뒤에 붙인다. 화면의 숫자 검증 표, 원문 형광펜, 오른쪽 패널이 같은 목록을 쓴다.
    같은 종류(dart, krx)의 예전 결과는 바꿔 끼운다. '검증 불가'는 개수에서 '확인 불가' 칸에 함께 센다."""
    exts = _exts(exts)
    if not exts:
        return nc
    kinds = {r["kind"] for e in exts for r in e["results"]}
    results = [r for r in nc["results"] if r.get("kind") not in kinds] + [r for e in exts for r in e["results"]]
    count = {MATCH: 0, MISMATCH: 0, UNKNOWN: 0}
    for r in results:
        count[r["status"] if r["status"] in count else UNKNOWN] += 1
    names = sorted({r.get("source", {}).get("provider", "").split(" ")[0] for r in results if r.get("source")} - {""})
    scope = "리포트 안의 숫자끼리 대조" + (" + 공식 데이터(" + ", ".join(names) + ") 대조" if names else "")
    return dict(nc, results=results, count=count, scope=scope)


def summary(ext: Optional[dict]) -> Optional[dict]:
    """result.json 의 external.<종류> 에 넣을 요약"""
    if ext is None:
        return None
    c: Dict[str, int] = {}
    for r in ext.get("results", []):
        c[r["status"]] = c.get(r["status"], 0) + 1
    return {"provider": ext.get("provider"), "enabled": ext.get("enabled"), "error": ext.get("error"),
            "count": c, "skipped": ext.get("skipped", []), "checked_at": date.today().isoformat()}


def external_marks(pdf: Path, report: Report, *exts: Optional[dict]) -> List[dict]:
    """불일치는 빨강, 검증 불가는 주황 형광펜. 일치는 칠하지 않는다 (문제가 있는 곳만 칠하는 원칙)."""
    exts = _exts(exts)
    if not exts:
        return []
    import pymupdf
    from ..pdf_locate import locate
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc = pymupdf.open(pdf)
    from ..table_check import all_rows
    rows = all_rows(report)               # 표의 칸도 (표 검산 결과를 칠할 때)
    marks = []
    for x in (r for e in exts for r in e["results"]):
        if x["status"] == MATCH or x["stated_rid"] not in rows:
            continue
        r = rows[x["stated_rid"]]
        for f in locate(doc, r.value, r.page)[:1]:
            marks.append({"ref": x["check_id"], "kind": "number", "hue": "red" if x["status"] == MISMATCH else "amber",
                          "page": f["page"], "rects": [[round(v, 5) for v in q] for q in f["rects"]]})
    return marks


def augment(run_dir: Path, checks: Dict[str, "object"], pdf: Optional[Path] = None, pool: bool = False,
            saved: bool = False) -> Dict[str, dict]:
    """이미 끝난 분석의 result.json 에 공식 데이터 대조를 더한다 (AI를 부르지 않는다).
    checks: {'dart': (모듈, 클라이언트)…}. 처음 한 번 result.before-external.json 으로 원래 파일을 남긴다."""
    report = Report.model_validate_json((run_dir / "claims.json").read_text(encoding="utf-8"))
    res_path = run_dir / "result.json"
    result = json.loads(res_path.read_text(encoding="utf-8"))
    backup = run_dir / "result.before-external.json"
    if not backup.exists() and not (run_dir / "result.before-dart.json").exists():
        shutil.copyfile(res_path, backup)
    if saved:                                       # --saved: 저장해 둔 대조 결과(external_*.json)로 다시 합친다. 공시·AI 를 다시 부르지 않는다
        exts = {name: json.loads((run_dir / f"external_{name}.json").read_text(encoding="utf-8")) for name in checks}
        if "dart" in exts:
            from . import dart_pool
            gone = dart_pool.prune(report, exts["dart"])
            if gone:
                print(f"DART 결과에서 뺀 것(리포트의 다른 계산 결과를 재료로 잘못 짝지음): {', '.join(gone)}")
            if exts["dart"].get("target"):
                dart_pool.add_fact_values(report, exts["dart"], dart=checks["dart"][1])   # 보유 주식 수 × 종가 (공식 데이터만, 무료)
    else:
        exts = {name: mod.run(report, client) for name, (mod, client) in checks.items()}
    if pool and not saved and "dart" in exts and exts["dart"].get("target"):     # --pool: DART 통째 짝 찾기 (AI 를 부른다, 유료)
        from . import dart_pool
        from ..runner import load_config
        covered = {x["stated_rid"] for v in exts.values() for x in v.get("results", [])}
        covered |= {x.get("stated_rid") for x in result["numbers"]["results"]
                    if x.get("kind") in ("krx", "ecos", "kosis") and x.get("stated_rid")}
        exts["dart"]["pool_info"] = dart_pool.extend(report, exts["dart"], covered, load_config(), dart=checks["dart"][1])
    from .. import formula_check
    nc = {"results": result["numbers"]["results"], "count": result["numbers"]["count"]}
    nc = formula_check.extend(nc, report)          # 고정 산식 검산도 함께 새로 고친다 (코드만 쓰므로 무료)
    merged = merge(nc, *exts.values())
    result["numbers"].update(results=merged["results"], count=merged["count"], n_checks=len(merged["results"]),
                             scope=merged.get("scope", result["numbers"].get("scope")))
    prev = result.get("external") if isinstance(result.get("external"), dict) and "provider" not in result["external"] else {}
    result["external"] = {**(prev or {}), **{k: summary(v) for k, v in exts.items()}}
    pdf = pdf or run_dir.parent / "report.pdf"
    prefixes = tuple(f"{mod.PREFIX}-" for mod, _ in checks.values()) + ("F-",)
    marks = [m for m in result.get("marks", []) if not str(m.get("ref", "")).startswith(prefixes)]
    if pdf.exists():
        marks += external_marks(pdf, report, *exts.values(), {"results": [r for r in merged["results"] if r.get("kind") == "formula"]})
        marks.sort(key=lambda m: (m["page"], m["rects"][0][1]))
    result["marks"] = [m for m in marks if not m.get("pass")]
    from .. import evidence, rules
    rules.refresh_result(report, result)            # 공식 데이터 불일치를 주장 판정에 다시 반영
    if pdf.exists():                                # 통과한 곳(초록)도 다시 찾는다
        from ..marks import pass_marks
        result["marks"] = sorted(result["marks"] + pass_marks(pdf, report, result), key=lambda m: (m["page"], m["rects"][0][1]))
    evidence.mark_split(result)
    result["evidence"] = evidence.build(report, result)
    for name, ext in exts.items():
        (run_dir / f"external_{name}.json").write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return exts


def print_report(name: str, ext: dict) -> None:
    print(f"== {name}")
    if ext.get("error"):
        print("  멈춤:", ext["error"])
    for r in ext.get("results", []):
        print(f"  {r['check_id']} {r['status']:5} | {r['name']} | 리포트 {r['stated']} / 공식 {r.get('computed') or '-'}"
              f" | {(r.get('source') or {}).get('short', '')}")
        print(f"        {r['reason']}")
    for s in ext.get("skipped", []):
        print(f"  건너뜀 {s['rid']} {s['item']}: {s['why']}")
    print(f"  호출 {ext.get('calls', 0)}번 (나머지는 저장해 둔 응답)")
