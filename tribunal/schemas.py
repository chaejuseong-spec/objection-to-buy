"""입력(report.json)과 AI 출력의 형식을 정의하고 검사한다."""
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

Opinion = Literal["매수", "보유", "매도"]
DoubtType = Literal["숫자의 기준", "기간 선택", "가정의 근거", "논리 연결", "위험 누락", "근거의 독립성"]
_OLD_DOUBT = {"빠진 위험": "위험 누락"}   # 2026-11-20 이름 변경 전 결과를 읽을 때
Kind = Literal["사실 오류", "해석 차이"]
VoteValue = Literal["동의", "비동의", "판단 불가"]
Importance = Literal["상", "중", "하"]
_OLD_VOTE = {"타당": "동의", "과장": "동의", "확인 불가": "판단 불가"}   # 2026-11-20 전 결과를 읽을 때
# 예전 '과장'은 "맞지만 영향이 작다"는 뜻이라 새 규칙의 동의 + 중요도 하에 해당한다
Part = Literal["산업", "기업·재무", "밸류에이션", "센터장"]

JUROR_KEYS = ["accountant", "industry", "short", "statistician", "behavioral"]
JUROR_NAMES = {
    "accountant": "회계사", "industry": "산업 전문가", "short": "공매도 투자자",
    "statistician": "통계학자", "behavioral": "행동경제학자",
}


# ---------- 입력 ----------
_OPINION = {"매수": "매수", "buy": "매수", "strongbuy": "매수", "tradingbuy": "매수", "적극매수": "매수", "비중확대": "매수",
            "outperform": "매수", "overweight": "매수",
            "보유": "보유", "hold": "보유", "neutral": "보유", "중립": "보유", "marketperform": "보유", "시장수익률": "보유",
            "매도": "매도", "sell": "매도", "비중축소": "매도", "underperform": "매도", "underweight": "매도", "reduce": "매도"}


def _blank(v):
    """AI가 빈 칸을 null 로 적으면 빈 글자로 본다"""
    return "" if v is None else v


class Meta(BaseModel):
    ticker: str
    name: str
    report_date: str
    snapshot_date: str
    opinion: Opinion

    @model_validator(mode="before")
    @classmethod
    def _fill(cls, d):
        if isinstance(d, dict):
            d = dict(d)
            if not d.get("snapshot_date"):                       # 스냅샷 날짜가 없으면 리포트 날짜로
                d["snapshot_date"] = d.get("report_date")
            if isinstance(d.get("opinion"), str):                # Buy, BUY(유지), 매수 유지 -> 매수
                key = d["opinion"].split("(")[0].replace(" ", "").lower()
                d["opinion"] = _OPINION.get(key) or next((v for k, v in _OPINION.items() if key.startswith(k)), d["opinion"])
            if d.get("ticker") is not None:
                d["ticker"] = str(d["ticker"])
        return d
    demo: bool = False
    title: str = ""
    current_price: Optional[str] = None
    target_price: Optional[str] = None
    valuation_method: Optional[str] = None
    sector: Optional[str] = None      # 업종 (배심원 업종별 모듈을 고를 때 쓴다, tribunal/modules.py)
    source_file: Optional[str] = None


class Evidence(BaseModel):
    ref_id: str            # M# 또는 rid
    value: str             # 리포트에 적힌 그대로
    unit: str = ""
    page: Optional[int] = None


class Claim(BaseModel):
    claim_id: str
    text: str
    evidence: List[Evidence] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    is_core: bool = False
    page: Optional[int] = None


class Source(BaseModel):
    sid: str
    title: str
    url: str = ""
    date: str = ""


class DataRow(BaseModel):
    rid: str
    item: str
    value: str
    unit: str = ""
    basis: str = ""
    as_of: str = ""
    source: str = ""
    page: Optional[int] = None
    location: str = ""

    _none_is_blank = field_validator("unit", "basis", "as_of", "source", "location", mode="before")(_blank)

    @field_validator("value", mode="before")
    @classmethod
    def _text(cls, v):
        return v if isinstance(v, str) or v is None else str(v)   # 숫자로 적은 값도 글자로


class Metric(BaseModel):
    mid: str
    name: str
    formula: str
    inputs: List[str] = Field(default_factory=list)
    value: str


class SummaryPoint(BaseModel):
    """리포트 요약 한 줄. 원문 쪽 번호를 함께 적는다."""
    text: str
    pages: List[int] = Field(default_factory=list)


class Check(BaseModel):
    """숫자 검증 한 건. 코드가 계산한다.
    kind=recompute: expr(rid를 변수로 쓰는 사칙연산)을 계산해 stated(rid)와 비교
    kind=same: 두 rid의 표기가 같은지 비교"""
    check_id: str
    name: str
    kind: Literal["recompute", "same"]
    expr: str = ""
    stated: str = ""            # 리포트가 적은 값이 있는 rid
    compare: List[str] = Field(default_factory=list)   # kind=same 일 때 두 rid
    tolerance_pct: float = 0.5
    note: str = ""


class Report(BaseModel):
    meta: Meta
    claims: List[Claim]
    risks: List[str] = Field(default_factory=list)
    unverified: List[str] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    data_rows: List[DataRow] = Field(default_factory=list)
    metrics: List[Metric] = Field(default_factory=list)
    checks: List[Check] = Field(default_factory=list)
    summary: List[SummaryPoint] = Field(default_factory=list)   # 리포트가 말하는 것(평가가 아님)
    tables: List[dict] = Field(default_factory=list)            # 리포트 표에 글자로 적힌 숫자(차트로 다시 그림)
    kpis: List[str] = Field(default_factory=list)               # 대시보드에 앞세울 숫자의 rid
    external_data: bool = False   # 리포트 밖에서 가져온 대조 자료가 있는가


# ---------- AI 출력 ----------
class Objection(BaseModel):
    objection_id: str
    claim_id: Optional[str] = None      # 추출된 주장과 관련 있으면 그 id. 원문 전체에서 찾은 이의는 없을 수 있다
    page: Optional[int] = None          # 문제가 있는 쪽
    quote: str = ""                     # 문제가 있는 원문 문장 (글자 그대로)
    doubt_type: DoubtType

    @field_validator("doubt_type", mode="before")
    @classmethod
    def _old_doubt(cls, v):
        return _OLD_DOUBT.get(v, v)
    text: str
    kind: Kind
    counter_ids: List[str] = Field(default_factory=list)
    already_in_report: bool = False
    # 아래는 코드가 채운다
    removed_ids: List[str] = Field(default_factory=list)
    no_evidence: bool = False
    quote_found: bool = False           # 인용문이 PDF 원문에 실제로 있는지 코드가 확인한 결과
    added_by: Optional[str] = None      # 배심원이 추가한 이의면 배심원 이름


class ProsecutorOut(BaseModel):
    objections: List[Objection] = Field(default_factory=list)


class Vote(BaseModel):
    objection_id: str
    vote: VoteValue
    reason: str                 # 결론 영향
    view: str = ""              # 내 분야에서 본 점 (2026-11-17 추가, 예전 결과에는 없다)
    importance: Optional[Importance] = None   # 동의일 때만 (2026-11-20 추가). 빠지면 '미확인'

    @model_validator(mode="before")
    @classmethod
    def _old_names(cls, d):
        """예전 표 이름(타당/과장/확인 불가)으로 저장된 답도 읽는다. 과장은 동의(하). 동의가 아니면 중요도는 버린다."""
        if isinstance(d, dict):
            d = dict(d)
            if str(d.get("vote", "")).replace(" ", "") == "과장":
                d["importance"] = "하"
            d["vote"] = _OLD_VOTE.get(d.get("vote"), d.get("vote"))
            if d.get("vote") != "동의" or d.get("importance") not in ("상", "중", "하"):   # 틀린 값은 '미확인'으로
                d["importance"] = None
        return d


class ExtraObjection(BaseModel):
    claim_id: Optional[str] = None
    page: Optional[int] = None
    quote: str = ""
    doubt_type: DoubtType

    @field_validator("doubt_type", mode="before")
    @classmethod
    def _old_doubt(cls, v):
        return _OLD_DOUBT.get(v, v)
    text: str
    kind: Kind
    counter_ids: List[str] = Field(default_factory=list)


class JurorOut(BaseModel):
    votes: List[Vote] = Field(default_factory=list)
    extra_objection: Optional[ExtraObjection] = None


class KeyIssue(BaseModel):
    objection_id: str
    title: str


class ClaimRuling(BaseModel):
    claim_id: str
    reason: str
    impact: Literal["크다", "작다"]
    fix_part: Optional[Part] = None


class ObjectionRuling(BaseModel):
    """지적(이의) 하나의 판정 근거 (2026-11-20 추가: 처음엔 주장 밖 지적만, 같은 날 모든 지적으로)"""
    objection_id: str
    reason: str
    impact: Literal["크다", "작다"]
    fix_part: Optional[Part] = None


class JudgeOut(BaseModel):
    key_issues: List[KeyIssue] = Field(default_factory=list)
    claim_rulings: List[ClaimRuling] = Field(default_factory=list)
    objection_rulings: List[ObjectionRuling] = Field(default_factory=list)   # 예전 결과에는 없다


# ---------- DART 공시 짝 고르기 (2026-11-21, dart_pool ③) ----------
class MatchPick(BaseModel):
    """리포트 숫자 하나에 대해, 코드가 값·기간으로 찾은 공시 후보 가운데 같은 것을 뜻하는 번호. 없으면 null.
    AI는 숫자를 만들지 않고 후보 번호만 고른다. 값 비교와 판정은 코드가 한다."""
    rid: str
    pick: Optional[int] = None
    reason: str = ""


class MatchOut(BaseModel):
    picks: List[MatchPick] = Field(default_factory=list)


class RatioPlan(BaseModel):
    """비율 숫자 하나를 공시 숫자 두 개로 다시 계산하려면 어떤 공시 항목을 찾아야 하는지 (2026-11-21, dart_pool ②-3).
    AI는 찾을 낱말만 적고, 공시 숫자를 찾아 나누는 것은 코드가 한다"""
    rid: str
    is_ratio: bool = False           # 공시 숫자 두 개를 나눈 비율이면 true
    numerator: List[str] = Field(default_factory=list)     # 분자 공시 이름에 들어 있을 낱말 묶음들 (묶음 안 낱말은 모두 들어 있어야 함)
    denominator: List[str] = Field(default_factory=list)   # 분모 공시 이름에 들어 있을 낱말 묶음들
    reason: str = ""


class RatioOut(BaseModel):
    plans: List[RatioPlan] = Field(default_factory=list)
