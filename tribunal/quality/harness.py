"""테스트 세트를 돌리고 채점한다.

사본 하나에 드는 것: 리포트 입력(Gemini 1번) + 코드 검증(다시 계산, DART, KRX, ECOS: 무료).
리스크 검토자·배심원·판사는 숫자 오류를 잡는 데 쓰이지 않으므로 부르지 않는다.
뽑은 결과는 (사본 글 + 지시서 + 모델)별로 저장해, 바뀐 게 없으면 다시 부르지 않는다.

지표
- 탐지율          : 넣은 오류 중 '불일치'로 잡은 비율 (검증 수단이 있는 숫자 기준 / 전체 기준)
- 놓친 이유       : 뽑지 않음 / 검증 수단 없음 / 검증했지만 통과
- 원본 불일치     : 아무것도 바꾸지 않은 사본에서 나온 불일치 (리포트의 실제 오류이거나 잘못 짚은 것. 사람이 본다)
- 추가 오탐       : 바꾼 사본에서, 바꾼 숫자와 상관없이 새로 나온 불일치
- 검증 불가 비율  : 원본 사본의 검증 중 확인 불가·검증 불가 비율
- 처리 시간, Gemini 호출 수, 공식 데이터 호출 수
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import formula_check, llm, number_check
from ..extract import extract, read_pages, verify
from ..external import common, dart_check, ecos_check, kosis_check, krx_check
from ..number_check import MATCH, MISMATCH
from ..schemas import Report
from .inject import SETS, involved

ROOT = llm.ROOT
QUALITY = ROOT / "eval" / "quality"
CACHE = QUALITY / "cache"
RUNS = QUALITY / "runs"
PROVIDERS = (("dart", dart_check), ("krx", krx_check), ("ecos", ecos_check), ("kosis", kosis_check))


def _norm(s: str) -> str:
    return re.sub(r"\s", "", s or "")


def patched_pages(clean_pdf: Path, muts: List[dict]) -> List[dict]:
    """원본 글 순서를 지키고, 바꾼 숫자 토큰만 갈아 끼운 글"""
    pages = read_pages(clean_pdf)
    for m in muts:
        p = pages[m["page"] - 1]
        p["text"] = re.sub(rf"(?<![\d,.]){re.escape(m['old'])}(?![\d,]|\.\d)", m["new"], p["text"], count=1)
    return pages


def cache_key(pages: List[dict], cfg: dict) -> str:
    st = llm.settings(cfg)
    parts = [json.dumps(pages, ensure_ascii=False), llm.load_prompt("extractor"), st["provider"], st["model"],
             str(cfg.get("extract_max_output_tokens", 16000))]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]


def get_extraction(case_pdf: Path, pages: List[dict], cfg: dict, allow_call: bool):
    """돌려주는 값: (Report 또는 None, 기록, 'cache'|'call'|'need')"""
    f = CACHE / f"{cache_key(pages, cfg)}.json"
    if f.exists():
        saved = json.loads(f.read_text(encoding="utf-8"))
        if not saved["report"]:
            return None, saved["stats"], "cache"
        # 저장한 추출 결과에도 지금의 원문 대조 규칙을 다시 적용한다 (코드만 고쳤을 때도 무료로 다시 잴 수 있게)
        report, stats = verify(Report.model_validate(saved["report"]), case_pdf, pages)
        return report, stats, "cache"
    if not allow_call:
        return None, {}, "need"
    report, stats = extract(case_pdf, cfg, mock=False, pages=pages)
    CACHE.mkdir(parents=True, exist_ok=True)
    if report is None:                                   # 실패는 결과가 아니다. 다음에 다시 부른다
        raw = llm.FAILED_RAW.pop("extractor", None)
        if raw:
            f.with_suffix(".failed.txt").write_text(raw, encoding="utf-8")
        return None, stats, "call"
    f.write_text(json.dumps({"report": report.model_dump() if report else None, "stats": stats,
                             "saved_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
    return report, stats, "call"


def verify_numbers(report: Report, clients: Optional[dict] = None) -> dict:
    """코드 검증 전부: 다시 계산 + 공식 데이터 대조. 돌려주는 값: {'results', 'count', 'external_calls', 'errors'}"""
    nc = formula_check.extend(number_check.run_checks(report), report)
    exts = {k: m.run(report, (clients or {}).get(k)) for k, m in PROVIDERS}
    merged = common.merge(nc, *exts.values())
    return {"results": merged["results"], "count": merged["count"],
            "external_calls": sum(e.get("calls", 0) for e in exts.values()),
            "errors": {k: e["error"] for k, e in exts.items() if e.get("error")}}


def score_case(report: Optional[Report], checks: List[dict], muts: List[dict]) -> dict:
    """넣은 오류마다 잡았는지와 놓친 이유"""
    from ..table_check import all_rows
    rows = all_rows(report) if report else {}      # 표의 칸에 넣은 오류도 추적한다
    found = []
    hit_rids = set()
    for m in muts:
        new = _norm(m["new"])
        rids = [rid for rid, r in rows.items() if re.search(rf"(?<![\d,.]){re.escape(new)}(?![\d,]|\.\d)", _norm(r.value))]
        hit_rids.update(rids)
        about = [c for c in checks if involved(c) & set(rids)]
        caught = [c for c in about if c["status"] == MISMATCH]
        if caught:
            verdict, why = "잡음", ", ".join(f"{c['check_id']} {c['name']}" for c in caught)
        elif not rids:
            verdict, why = "놓침", "뽑지 않음"
        elif not about:
            verdict, why = "놓침", "검증 수단 없음"
        else:
            verdict, why = "놓침", "검증했지만 통과: " + ", ".join(f"{c['check_id']} {c['status']}" for c in about)
        found.append({**{k: m[k] for k in ("rid", "item", "page", "value_old", "value_new", "expect")},
                      "result": verdict, "why": why, "extracted": bool(rids)})
    others = [{"check_id": c["check_id"], "name": c["name"], "kind": c.get("kind") or "recompute", "reason": c.get("reason", "")}
              for c in checks if c["status"] == MISMATCH and not (involved(c) & hit_rids)]
    return {"mutations": found, "other_mismatches": others}


def git_version() -> dict:
    def g(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        return r.stdout.strip() if r.returncode == 0 else ""
    return {"commit": g("rev-parse", "--short", "HEAD") or "기록 없음", "dirty": bool(g("status", "--porcelain"))}


def plan(set_dir: Path, cfg: dict) -> dict:
    """돌리기 전에: 사본 수와, 그중 Gemini 를 새로 불러야 하는 수"""
    meta = json.loads((set_dir / "cases.json").read_text(encoding="utf-8"))
    need = [c["id"] for c in meta["cases"]
            if not (CACHE / f"{cache_key(patched_pages(set_dir / 'clean.pdf', c['mutations']), cfg)}.json").exists()]
    return {"cases": len(meta["cases"]), "need_calls": need, "model": llm.settings(cfg)["model"],
            "mutations": sum(len(c["mutations"]) for c in meta["cases"])}


def run_set(set_dir: Path, cfg: dict, allow_call: bool = False, clients: Optional[dict] = None) -> dict:
    meta = json.loads((set_dir / "cases.json").read_text(encoding="utf-8"))
    cases, calls, ext_calls, t_all = [], 0, 0, time.time()
    for c in meta["cases"]:
        t0 = time.time()
        pages = patched_pages(set_dir / "clean.pdf", c["mutations"])
        report, stats, how = get_extraction(set_dir / c["pdf"], pages, cfg, allow_call)
        if how == "need":
            raise RuntimeError(f"사본 {c['id']} 는 저장해 둔 추출 결과가 없습니다. Gemini 를 불러야 하니 --yes 를 붙여 실행하세요.")
        calls += how == "call"
        v = verify_numbers(report, clients) if report else {"results": [], "count": {}, "external_calls": 0, "errors": {}}
        ext_calls += v["external_calls"]
        s = score_case(report, v["results"], c["mutations"])
        cases.append({"id": c["id"], "extraction": how, "seconds": round(time.time() - t0, 1),
                      "rows": len(report.data_rows) if report else 0, "claims": len(report.claims) if report else 0,
                      "error": stats.get("error") if not report else None,
                      "checks": [{"check_id": x["check_id"], "kind": x.get("kind") or "recompute", "name": x["name"], "status": x["status"]} for x in v["results"]],
                      "external_errors": v["errors"], **s})
    return summarize(meta, cases, cfg, calls, ext_calls, round(time.time() - t_all, 1))


def summarize(meta: dict, cases: List[dict], cfg: dict, calls: int, ext_calls: int, seconds: float) -> dict:
    muts = [m for c in cases for m in c["mutations"]]
    checkable = [m for m in muts if m["expect"] != "none"]
    caught = [m for m in muts if m["result"] == "잡음"]
    clean = next((c for c in cases if c["id"] == "clean"), None)
    base = {(x["kind"], x["name"]) for x in (clean["other_mismatches"] if clean else [])}
    extra = [dict(x, case=c["id"]) for c in cases if c["id"] != "clean" for x in c["other_mismatches"] if (x["kind"], x["name"]) not in base]
    why: Dict[str, int] = {}
    for m in muts:
        if m["result"] == "놓침":
            k = m["why"].split(":")[0]
            why[k] = why.get(k, 0) + 1
    by_expect: Dict[str, List[int]] = {}
    for m in muts:
        a = by_expect.setdefault(m["expect"], [0, 0])
        a[0] += m["result"] == "잡음"
        a[1] += 1
    cc = clean["checks"] if clean else []
    ratio = lambda a, b: round(a / b, 3) if b else None      # noqa: E731
    return {
        "at": datetime.now().isoformat(timespec="seconds"), "set": meta["name"], "report": meta["report"], "test_material": True,
        "version": git_version(), "model": llm.settings(cfg)["model"],
        "metrics": {
            "injected": len(muts), "checkable": len(checkable), "caught": len(caught),
            "detect_rate_checkable": ratio(sum(m["result"] == "잡음" for m in checkable), len(checkable)),
            "detect_rate_all": ratio(len(caught), len(muts)),
            "extracted_rate": ratio(sum(m["extracted"] for m in muts), len(muts)),
            "missed_why": why, "by_expect": {k: {"caught": v[0], "total": v[1]} for k, v in by_expect.items()},
            "clean_mismatches": len(clean["other_mismatches"]) if clean else None, "extra_false_alarms": len(extra),
            "unverifiable_rate": ratio(sum(x["status"] not in (MATCH, MISMATCH) for x in cc), len(cc)),
            "clean_checks": len(cc), "seconds": seconds, "gemini_calls": calls, "external_calls": ext_calls,
        },
        "extra_false_alarms": extra, "cases": cases,
    }


def save_run(rec: dict) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    f = RUNS / f"{rec['at'].replace(':', '').replace('-', '')}-{rec['set']}.json"
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def history(set_name: Optional[str] = None) -> List[dict]:
    out = []
    for f in sorted(RUNS.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if not set_name or r["set"] == set_name:
            out.append(r)
    return out


def set_dir(name: str) -> Path:
    return SETS / name
