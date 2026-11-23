"""검토 자료 만들기. 각 AI는 대화를 모르므로 필요한 것을 모두 담아 넘긴다.
중립적으로: 사람의 매수 이유나 첫 판단은 넣지 않는다."""
from __future__ import annotations
from .schemas import Report


def build_case_file(report: Report) -> dict:
    d = report.model_dump()
    rules = [
        "아래 자료에 있는 id(rid, M#, S#)만 인용한다. 숫자나 링크를 새로 만들지 않는다.",
        f"리포트 작성일({report.meta.report_date}) 뒤에 일어나거나 공개된 일은 알고 있어도 쓰지 않는다.",
        "주가를 예측하지 않는다. 매수·보유·매도 결정은 사람이 한다.",
    ]
    if not report.external_data:
        rules.append("외부 대조 자료 없음: 모든 숫자는 리포트 본문에서 나왔다. "
                     "리포트 안의 숫자와 논리가 서로 맞는지만 따지고, 외부 사실이 필요한 판단은 '확인 불가'로 둔다.")
    return {
        "case_rules": rules,
        "external_data": report.external_data,
        "meta": d["meta"], "claims": d["claims"], "risks": d["risks"],
        "unverified": d["unverified"], "sources": d["sources"],
        "data_rows": d["data_rows"], "metrics": d["metrics"],
    }
