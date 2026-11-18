"""배심원 지시서의 '업종별 심화 점검' 모듈을 코드가 고른다.

리포트에 맞는 모듈만 붙여, 배심원 다섯 명이 같은 업종 판단을 공유하게 한다.
- 업종: 리포트 입력(추출)이 적은 meta.sector -> 없으면 회사 이름·제목의 낱말
- 평가 방식: SOTP(지분가치 합산)면 지주회사 모듈도 붙인다 (예: 보험사인데 보유 지분이 가치의 대부분)
- 아무것도 못 정하면 모든 모듈을 붙인다 (빠뜨리는 것보다 낫다)
모듈 파일: prompts/modules/<배심원>/<업종>.md. 폴더가 없는 배심원은 모듈 없이 자기 지시서만 쓴다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = ROOT / "prompts" / "modules"

# 순서 = 지시서에 붙는 순서
SECTORS = {
    "insurance": "보험", "bank": "은행", "securities": "증권", "manufacturing": "제조업",
    "construction": "건설·프로젝트형 사업", "consumer": "소비재·유통·식품", "software": "소프트웨어·플랫폼",
    "holding": "지주회사",
}
# 추출 단계가 적는 업종 이름 -> 모듈
_LABEL = {"보험": "insurance", "생명보험": "insurance", "손해보험": "insurance", "은행": "bank", "증권": "securities",
          "제조": "manufacturing", "제조업": "manufacturing", "건설": "construction", "소비재": "consumer",
          "유통": "consumer", "식품": "consumer", "소프트웨어": "software", "플랫폼": "software",
          "지주회사": "holding", "지주": "holding"}
# 회사 이름·제목에서 업종을 짐작하는 낱말 (앞에 있을수록 먼저)
_WORDS = [
    ("insurance", r"생명|화재|손해보험|보험"),
    ("securities", r"증권|투자증권"),
    ("bank", r"은행|금융지주"),
    ("holding", r"홀딩스|지주"),
    ("construction", r"건설|E&C|엔지니어링"),
    ("manufacturing", r"반도체|전자|자동차|모비스|화학|케미칼|철강|조선|중공업|디스플레이|배터리|에너지솔루션|소재|기계"),
    ("consumer", r"식품|푸드|제당|음료|유통|리테일|쇼핑|화장품|코스메틱|백화점|마트|F&B"),
    ("software", r"소프트|플랫폼|게임즈|게임|엔터|카카오|네이버|NAVER"),
]


def pick(meta) -> Tuple[List[str], str]:
    """(고른 모듈, 어떻게 골랐는지)"""
    keys: List[str] = []
    how = ""
    label = (getattr(meta, "sector", None) or "").strip()
    if label:                          # 최대 2개, 쉼표로 (예: "보험, 지주회사")
        for part in re.split(r"[,/·]", label)[:2]:
            part = part.strip()
            k = _LABEL.get(part.replace(" ", "")) or next((v for w, v in _LABEL.items() if w in part), None)
            if k and k not in keys:
                keys.append(k)
        if keys:
            how = f"리포트 입력의 업종({label})"
    if not keys:
        text = f"{getattr(meta, 'name', '')} {getattr(meta, 'title', '')}"
        k = next((k for k, pat in _WORDS if re.search(pat, text)), None)
        if k:
            keys, how = [k], "회사 이름·제목의 낱말"
    if re.search(r"SOTP|지분가치|NAV", getattr(meta, "valuation_method", None) or "", re.I) and "holding" not in keys:
        keys.append("holding")
        how += (" + " if how else "") + "평가 방식 SOTP"
    if not keys:
        return list(SECTORS), "업종을 정하지 못해 모든 모듈"
    return [k for k in SECTORS if k in keys], how


def text_for(juror_key: str, keys: List[str]) -> str:
    """이 배심원에게 붙일 모듈 글. 모듈 폴더가 없으면 빈 글."""
    d = MODULE_DIR / juror_key
    if not d.is_dir():
        return ""
    parts = [(d / f"{k}.md").read_text(encoding="utf-8").strip() for k in keys if (d / f"{k}.md").exists()]
    if not parts:
        return ""
    names = ", ".join(SECTORS[k] for k in keys)
    return f"# 업종별 심화 점검 모듈 (이 리포트: {names})\n\n" + "\n\n".join(parts)
