"""배심원 업종별 모듈을 코드가 고른다 (tribunal/modules.py)"""
from types import SimpleNamespace as NS

from tribunal import llm, modules


def meta(name="", title="", valuation_method=None, sector=None):
    return NS(name=name, title=title, valuation_method=valuation_method, sector=sector)


def test_회사_이름으로_업종을_고른다():
    assert modules.pick(meta("가상생명"))[0] == ["insurance"]
    assert modules.pick(meta("가상반도체"))[0] == ["manufacturing"]
    assert modules.pick(meta("가상식품홀딩스"))[0] == ["holding"]


def test_SOTP면_지주회사_모듈을_더한다():
    """보험사인데 가치의 대부분이 보유 지분이면 보험과 지주회사 모듈이 함께 붙는다 (가상생명)"""
    assert modules.pick(meta("가상생명", valuation_method="SOTP"))[0] == ["insurance", "holding"]


def test_추출이_적은_업종이_이름보다_앞선다():
    assert modules.pick(meta("가상생명", sector="은행"))[0] == ["bank"]


def test_업종을_못_정하면_모든_모듈():
    keys, how = modules.pick(meta("알수없음"))
    assert keys == list(modules.SECTORS) and "모든" in how


def test_모듈은_업종_공통과_자기_칸을_붙이고_순서는_지시서_모듈_공통규칙():
    acc = modules.text_for("accountant", ["insurance", "holding"])
    assert "## 보험" in acc and "## 지주회사" in acc and "### 제조업" not in acc
    assert modules.text_for("nobody", ["insurance"]) == ""                 # 모듈 폴더가 없는 역할
    # 보험: 공통(구조 -> 검증 변수 -> 쟁점 -> 위험 -> 주의) 다음 자기 칸
    order = ["### 1. 핵심 사업 구조와 연결고리", "### 2. 주요 검증 변수", "### 4. 업종 특유의 위험", "### 6. 회계사의 보험"]
    at = [acc.index(x) for x in order]
    assert at == sorted(at) and acc.index("### 6. 회계사의 보험") < acc.index("## 지주회사")
    assert acc.rstrip().endswith(modules._read(modules.SHARED_DIR / "insurance+holding.md"))   # 두 업종이면 복합기업 규칙
    ind = modules.text_for("industry", ["insurance"])
    assert "산업 전문가의 보험" in ind and "회계사의" not in ind and "복합기업" not in ind     # 자기 칸만
    text = llm.load_role("juror_accountant", acc)
    assert text.index("## 2. 업종별 심화 점검") < text.index("## 보험") < text.index("# 배심원 공통 규칙")


def test_공통_파일이_없는_업종은_자기_칸만():
    bank = modules.text_for("accountant", ["bank"])
    assert "### 은행" in bank and "### 1. 핵심 사업 구조" not in bank


def test_업종을_두_개까지_읽는다():
    """추출이 '보험, 지주회사'처럼 본업과 가치의 중심을 함께 적으면 두 모듈을 붙인다"""
    assert modules.pick(meta("가상생명", sector="보험, 지주회사"))[0] == ["insurance", "holding"]
    assert modules.pick(meta("A사", sector="제조업, 소비재, 은행"))[0] == ["manufacturing", "consumer"]   # 3개째는 버림
    assert modules.pick(meta("A사", valuation_method="SOTP", sector="보험"))[0] == ["insurance", "holding"]


def test_리스크_검토자는_업종_공통과_다섯_칸을_모두_받는다():
    pros = modules.text_for_prosecutor(["insurance", "holding"])
    for who in ["회계사", "산업 전문가", "공매도 투자자", "통계학자", "행동경제학자"]:
        assert f"### 6. {who}의 보험" in pros and f"### 6. {who}의 지주회사" in pros
    assert pros.index("## 보험") < pros.index("### 6. 회계사의 보험") < pros.index("## 지주회사")
    assert "복합기업" in pros and "확정하지 않는다" in pros.split("\n")[1]
    assert modules.text_for_prosecutor([]) == ""
    text = llm.load_role("prosecutor", pros)
    assert text.index("## 멈출 조건") < text.index("# 업종별 심화 모듈") and "# 배심원 공통 규칙" not in text


def test_업종_모듈은_기본으로_끄고_주의할_점은_공통_규칙에_있다():
    # 2026-11-21: 업종별로 다 쓸 수 없어 모듈을 끄고, 업종과 관계없는 주의할 점을 모든 역할 공통 규칙에 둔다
    import yaml
    from tribunal.llm import ROOT, load_common
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert cfg.get("sector_modules") is False
    common = load_common()
    assert "지적하거나 판단할 때 주의할 점" in common and "흔히 쓰는 계산·평가 방식" in common


def test_배심원은_표결_뒤_놓친_지적_찾기를_정식_단계로_한다():
    # 2026-11-21: 재실행 4번 모두 배심원 추가 지적이 0건 -> 판단 순서 7단계로, 주의할 점은 '근거 없는 지적을 거르는 기준'임을 밝힘
    from tribunal.llm import ROOT, load_common
    juror = (ROOT / "prompts" / "_juror.md").read_text(encoding="utf-8")
    assert "7. **놓친 지적 찾기" in juror and "A~E를 모두 다시 훑은 뒤" in juror
    assert "근거 있는 지적을 줄이거나 망설이라는 뜻이 아니다" in load_common()
