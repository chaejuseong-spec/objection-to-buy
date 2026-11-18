"""서기: 순서 진행, 집계, 규칙 적용, 파일 저장만 한다. 주장·투표·판결을 직접 만들지 않는다."""
from __future__ import annotations
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml

from . import llm, number_check, rules
from .case_file import build_case_file
from .render import render_markdown, write_verdict_form
from .schemas import JUROR_KEYS, JUROR_NAMES, JudgeOut, JurorOut, Objection, ProsecutorOut, Report

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Optional[Path] = None) -> dict:
    return yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def brief_checks(nc: dict) -> list:
    """배심원에게 줄 숫자 확인 결과 (코드가 한 것). 어느 숫자(rid)를 무엇과 대조해 어떻게 나왔는지만 짧게."""
    keep = ("check_id", "name", "rids", "status", "stated", "computed", "reason", "timing")   # timing: 공시가 작성 당시/이후 공개인지
    return [{k: x.get(k) for k in keep} for x in nc.get("results", [])]


def _step(out: Path, name: str, schema, fn, mock: bool):
    """AI 한 단계의 답을 out/steps/<name>.json 에 저장해 둔다.
    같은 폴더로 다시 돌리면(웹의 '다시 시도') 저장된 답을 쓰고 AI를 다시 부르지 않는다.
    답이 없으면(None) 저장하지 않아서 다음에 다시 부른다. mock 은 저장하지 않는다."""
    if mock:
        return fn()
    path = out / "steps" / f"{name}.json"
    if path.exists():
        try:
            got = schema.model_validate_json(path.read_text(encoding="utf-8"))
            llm._log(f"[{name}] 저장해 둔 답을 씁니다 (AI를 다시 부르지 않음)")
            return got
        except ValueError:
            pass
    try:
        res = fn()
    finally:                                # 실패해도 이미 쓴 비용은 남긴다
        save_usage(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if res is not None:
        path.write_text(res.model_dump_json(indent=1), encoding="utf-8")
    else:                                   # 형식이 틀려 버린 답은 원인을 볼 수 있게 따로 남긴다
        _save_failed(out, name)
    return res


def _save_failed(out: Path, role: str) -> None:
    raw = llm.FAILED_RAW.pop(role, None)
    if raw:
        (out / "steps").mkdir(parents=True, exist_ok=True)
        (out / "steps" / f"{role}.failed.txt").write_text(raw, encoding="utf-8")


def plan(report_path: Path, cfg: dict, n_jurors: int) -> dict:
    """실행 전에 호출 횟수와 예상 비용 상한을 계산한다."""
    n_calls = 1 + n_jurors + 1
    roles = ["prosecutor"] + ["juror"] * n_jurors + ["judge"]     # 역할마다 모델과 단가가 다를 수 있다
    if report_path.suffix.lower() == ".pdf":
        from .extract import read_pages
        pdf_chars = sum(len(p["text"]) for p in read_pages(report_path))
        # 리스크 검토자·배심원도 원문을 읽는다. 첫 호출은 캐시 쓰기(1.25배), 이후 배심원은 캐시 읽기(약 0.05배)로 어림한다
        doc_chars = int(pdf_chars * (1.25 + 0.05 * n_jurors))
        est = llm.estimate(pdf_chars + doc_chars + (pdf_chars // 3 + 3000) * n_calls, n_calls, cfg, roles)
        st = llm.settings(cfg, "extractor")
        extra = int(cfg.get("extract_max_output_tokens", 16000) * st["token_factor"])
        est["calls"] += 1
        est["output_tokens_max"] += extra
        if est["usd_max"] is not None:
            est["usd_max"] = round(est["usd_max"] + extra / 1e6 * st["price_out"], 2)
        return est
    report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    chars = len(json.dumps(build_case_file(report), ensure_ascii=False)) * n_calls + 3000 * n_calls
    return llm.estimate(chars, n_calls, cfg, roles)


def run_pdf(pdf: Path, mock: bool, n_jurors: Optional[int] = None, out_root: Optional[Path] = None,
            cfg: Optional[dict] = None, mock_dir: Optional[Path] = None, run_id: Optional[str] = None,
            dashboard: Optional[Path] = None, demo: bool = False) -> Path:
    """PDF 한 편을 넣으면 끝까지: 추출(AI) -> 원문 대조(코드) -> 반대신문 -> 판정 -> 결과."""
    from .extract import extract
    cfg = cfg or load_config()
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = (out_root or ROOT / ".tribunal") / run_id
    out.mkdir(parents=True, exist_ok=True)
    claims_done, stats_done = out / "claims.json", out / "extraction.json"
    if not mock and claims_done.exists() and stats_done.exists():   # 지난번에 추출까지 끝났으면 건너뛴다
        llm._log("[extractor] 저장해 둔 추출 결과를 씁니다 (AI를 다시 부르지 않음)")
        report = Report.model_validate_json(claims_done.read_text(encoding="utf-8"))
        stats = json.loads(stats_done.read_text(encoding="utf-8"))
    else:
        try:
            report, stats = extract(pdf, cfg, mock, mock_dir, demo=demo)
        finally:
            save_usage(out)
        _dump(out / "extraction.json", stats)
        if report is None and not mock:
            _save_failed(out, "extractor")
    if report is None:
        raise RuntimeError(stats.get("error", "추출에 실패했습니다."))
    if not report.claims:
        raise RuntimeError("원문에서 확인된 주장이 없습니다. extraction.json 을 확인하세요.")
    claims_path = out / "claims.json"
    claims_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    run(claims_path, mock=mock, n_jurors=n_jurors, out_root=out_root, cfg=cfg, mock_dir=mock_dir,
        run_id=run_id, pdf=pdf, dashboard=dashboard, extraction=stats)
    return out


def run(report_path: Path, mock: bool, n_jurors: Optional[int] = None, out_root: Optional[Path] = None,
        cfg: Optional[dict] = None, mock_dir: Optional[Path] = None, run_id: Optional[str] = None,
        pdf: Optional[Path] = None, dashboard: Optional[Path] = None, extraction: Optional[dict] = None) -> Path:
    cfg = cfg or load_config()
    n_jurors = n_jurors or cfg.get("jurors", 5)
    if n_jurors not in (3, 5):
        raise ValueError("배심원 수는 3 또는 5만 됩니다.")
    before = _sha(report_path)
    report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = (out_root or ROOT / ".tribunal") / run_id
    out.mkdir(parents=True, exist_ok=True)

    # 1) 사건 파일
    case = build_case_file(report)
    _dump(out / "case_file.json", case)

    # 1-1) 숫자 검증과 공식 데이터 대조 (코드가 한다, AI 호출 없음). 판정에 반영하려고 반대신문보다 먼저 한다.
    nc = number_check.run_checks(report)
    from . import formula_check
    nc = formula_check.extend(nc, report)      # AI가 식을 안 만들었어도 정해진 산식은 코드가 항상 검산한다
    _dump(out / "number_check.json", nc)

    #      공식 데이터 대조: OpenDART 공시 재무제표, KRX 시장 데이터, ECOS 거시 통계, KOSIS 산업 통계. 키가 없거나 꺼져 있으면 건너뛴다.
    from .external import common, dart_check, ecos_check, kosis_check, krx_check
    exts = {} if mock else {k: m.maybe_run(report, cfg) for k, m in (("dart", dart_check), ("krx", krx_check), ("ecos", ecos_check), ("kosis", kosis_check))}
    exts = {k: v for k, v in exts.items() if v is not None}
    for k, v in exts.items():
        _dump(out / f"external_{k}.json", v)
        if v.get("error"):
            llm._log(f"[{k}] 공식 데이터 대조를 하지 못했습니다: {v['error']}")

    nc_all = common.merge(nc, *exts.values())

    # 리포트 원문. 리스크 검토자와 배심원이 요약본(사건 파일)뿐 아니라 원문 전체를 읽는다.
    # 같은 원문을 맨 앞에 두므로 두 번째 호출부터는 캐시로 싸게 읽힌다. PDF가 없으면(JSON 입력) 요약본만 쓴다.
    pages, document = None, None
    if pdf:
        from .extract import read_pages
        pages = read_pages(pdf)
        document = llm.document_text(pages)

    # 2) 리스크 검토자
    pros_cfg = dict(cfg, max_output_tokens=cfg.get("prosecutor_max_output_tokens", cfg["max_output_tokens"]))
    pros = _step(out, "prosecutor", ProsecutorOut,
                 lambda: llm.call("prosecutor", dict(case, number_checks=brief_checks(nc_all)), ProsecutorOut,
                                  pros_cfg, mock, mock_dir, document=document), mock)
    if pros is None:      # 이의 없이 배심원·판사로 넘어가면 '문제 없음'처럼 보이는 잘못된 결과가 나온다. 여기서 멈춘다
        raise RuntimeError("리스크 검토자가 답을 끝까지 쓰지 못했습니다. 저장된 단계부터 다시 시도하세요.")
    raw_objs = pros.objections
    objections = rules.filter_counter_ids(raw_objs, report, pages)
    if len(objections) < len(raw_objs):
        llm._log(f"[prosecutor] 이의 {len(raw_objs)}개 중 {len(raw_objs) - len(objections)}개를 뺐습니다 "
                 f"(근거 숫자도 없고 인용이 원문에 없음, 또는 안전 상한 {rules.MAX_OBJECTIONS}개 초과)")
    _dump(out / "prosecutor.json", {"failed": pros is None,
                                    "objections": [o.model_dump() for o in objections]})

    # 3) 배심원 (서로 안 봄). 동시에 부르는 수는 max_concurrent_calls 로 제한한다.
    keys = JUROR_KEYS[:n_jurors]
    payload = dict(case, objections=[o.model_dump(exclude={"removed_ids", "added_by"}) for o in objections],
                   number_checks=brief_checks(nc_all))   # 코드가 이미 확인한 숫자. 배심원이 직접 계산하지 않게 한다
    failed: Dict[str, str] = {}
    from . import modules
    mod_keys, mod_how = modules.pick(report.meta)          # 업종별 심화 점검 모듈: 다섯 배심원이 같은 업종 판단을 쓴다
    mod_used = [k for k in keys if modules.text_for(k, mod_keys)]
    llm._log(f"[modules] {', '.join(modules.SECTORS[k] for k in mod_keys)} ({mod_how})")

    def ask(k: str) -> Optional[JurorOut]:
        try:
            return _step(out, f"juror_{k}", JurorOut,
                         lambda: llm.call(f"juror_{k}", payload, JurorOut, cfg, mock, mock_dir, document=document,
                                          extra=modules.text_for(k, mod_keys)), mock)
        except RuntimeError as e:          # 한 명이 실패해도 나머지는 끝까지 받아 저장한다
            failed[k] = str(e)
            return None

    workers = max(1, min(n_jurors, int(cfg.get("max_concurrent_calls", 2))))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(ask, keys))
    if failed:
        names = ", ".join(JUROR_NAMES[k] for k in failed)
        raise RuntimeError(f"배심원 {len(failed)}명({names})의 AI 호출이 실패해 멈췄습니다. "
                           f"나머지 {n_jurors - len(failed)}명의 표는 저장했습니다. 원인: {next(iter(failed.values()))}")
    jurors: Dict[str, Optional[JurorOut]] = dict(zip(keys, results))
    for k, r in jurors.items():
        _dump(out / f"juror_{k}.json", {"juror": JUROR_NAMES[k], "abstained": r is None,
                                        **(r.model_dump() if r else {})})

    # 배심원이 추가한 이의 (표 없음 -> 확인 필요)
    extras = []
    for k, r in jurors.items():
        if r and r.extra_objection:
            e = r.extra_objection
            extras.append(Objection(objection_id=f"J-{k}", added_by=JUROR_NAMES[k], **e.model_dump()))
    all_objs = objections + rules.filter_counter_ids(extras, report, pages)

    # 4) 코드가 집계하고 판정
    votes = {k: (r.votes if r else None) for k, r in jurors.items()}
    ev = rules.evaluate(report, all_objs, votes, n_jurors)
    # 코드 검증에서 주장의 근거 숫자가 틀린 것으로 나오면 배심원 판정보다 엄한 쪽을 쓴다 (공식 데이터 불일치 -> 수정)
    ev = rules.apply_code_findings(report, ev, rules.code_findings(report, nc_all["results"]))

    # 5) 판사: 표를 다시 매기지 않고 집계 결과를 받아 글만 쓴다
    judge_payload = dict(payload, objections=[o.model_dump() for o in all_objs], evaluation=ev,
                         juror_votes={JUROR_NAMES[k]: (r.model_dump() if r else "기권") for k, r in jurors.items()})
    judge = _step(out, "judge", JudgeOut,
                  lambda: llm.call("judge", judge_payload, JudgeOut, cfg, mock, mock_dir), mock)
    _dump(out / "judge.json", {"failed": judge is None, **(judge.model_dump() if judge else {})})

    ev_out = dict(ev, demo=report.meta.demo, mock=mock, run_id=run_id)
    _dump(out / "evaluation.json", ev_out)
    (out / "평가기록지.md").write_text(
        render_markdown(report, all_objs, jurors, judge, ev, mock), encoding="utf-8")
    write_verdict_form(out / "verdict_form.csv", ev, report.meta.demo)

    from .export_dashboard import build_result, write_result
    marks, not_found, n_pages, figures = [], [], 0, []
    if pdf:
        from .figures import find_figures
        from .marks import compute_marks
        marks, not_found, n_pages = compute_marks(pdf, report, all_objs, ev, nc)
        marks = sorted(marks + common.external_marks(pdf, report, *exts.values()), key=lambda m: (m["page"], m["rects"][0][1]))
        figures = find_figures(pdf)
    nc = nc_all
    result = build_result(report, all_objs, jurors, judge, ev, nc, mock, run_id, marks)
    result["external"] = {k: common.summary(v) for k, v in exts.items()} or None
    result["code_findings"] = ev.get("code_findings", {})
    result["modules"] = {"sectors": [modules.SECTORS[k] for k in mod_keys], "how": mod_how,
                         "jurors": [JUROR_NAMES[k] for k in mod_used]}   # 어떤 업종 모듈을 누구에게 붙였는지
    if pdf:                                    # 통과한 곳(초록). 화면에서 켤 때만 보인다
        from .marks import pass_marks
        result["marks"] = sorted(result["marks"] + pass_marks(pdf, report, result), key=lambda m: (m["page"], m["rects"][0][1]))
    from . import evidence
    evidence.mark_split(result, n_jurors)
    result["evidence"] = evidence.build(report, result)    # 주장별 근거 종합표 (코드가 정리)
    result["extraction"] = ({k: v for k, v in extraction.items() if k != "dropped"} |
                            {"dropped": {k: len(v) for k, v in extraction["dropped"].items()}}) if extraction else None
    result["figures"] = figures
    result["document"] = {"has_pdf": bool(pdf), "pages": n_pages, "not_found": not_found}
    write_result(out / "result.json", result)
    if dashboard:                       # 대시보드 폴더에도 같은 파일과 원문 PDF를 둔다
        write_result(dashboard / "data" / "result.json", result)
        if pdf:
            import shutil
            (dashboard / "public").mkdir(parents=True, exist_ok=True)
            target = dashboard / "public" / "report.pdf"
            if pdf.resolve() != target.resolve():
                shutil.copyfile(pdf, target)

    save_usage(out)
    if _sha(report_path) != before:
        raise RuntimeError("원본 리포트 파일이 바뀌었습니다. 있어서는 안 되는 일입니다.")
    return out


def save_usage(out: Path) -> Optional[dict]:
    """이번 실행에서 실제로 쓴 토큰·비용을 usage.json 에 더해 적는다 (이어서 실행해도 앞의 기록을 지우지 않는다).
    역할별 합계와 전체 합계를 함께 남긴다. 가짜 실행(--mock)은 호출이 없어 아무것도 쓰지 않는다."""
    with llm._usage_lock:
        new, llm.USAGE[:] = list(llm.USAGE), []
    if not new:
        return None
    with _usage_file_lock:                  # 배심원은 동시에 끝날 수 있어 파일 쓰기를 하나씩 한다
        return _write_usage(out, new)


_usage_file_lock = threading.Lock()


def _write_usage(out: Path, new: list) -> dict:
    path = out / "usage.json"
    calls = (json.loads(path.read_text(encoding="utf-8")).get("calls", []) if path.exists() else []) + new
    by_role: dict = {}
    for c in calls:
        g = by_role.setdefault(llm.role_group(c["role"]) or "기타", {"calls": 0, "output": 0, "usd": 0.0, "models": []})
        g["calls"] += 1
        g["output"] += c["output"]
        g["usd"] = round(g["usd"] + c["usd"], 4)
        if c["model"] not in g["models"]:
            g["models"].append(c["model"])
    data = {"total_usd": round(sum(c["usd"] for c in calls), 4), "by_role": by_role, "calls": calls}
    _dump(path, data)
    llm._log(f"[비용] 이번까지 이 실행의 합계 약 {data['total_usd']:.2f}달러 "
             + ", ".join(f"{k} {v['usd']:.2f}" for k, v in by_role.items()))
    return data
