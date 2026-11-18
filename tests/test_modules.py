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


def test_모듈은_회계사에게만_붙고_순서는_지시서_모듈_공통규칙():
    acc = modules.text_for("accountant", ["insurance", "holding"])
    assert "### 보험" in acc and "### 지주회사" in acc and "### 제조업" not in acc
    assert modules.text_for("nobody", ["insurance"]) == ""                 # 모듈 폴더가 없는 역할
    ind = modules.text_for("industry", ["insurance"])
    assert "### 보험" in ind and "CSM" not in ind.split("### 보험")[1][:20]   # 역할마다 다른 모듈
    text = llm.load_role("juror_accountant", acc)
    assert text.index("## 2. 업종별 심화 점검") < text.index("### 보험") < text.index("# 배심원 공통 규칙")


def test_업종을_두_개까지_읽는다():
    """추출이 '보험, 지주회사'처럼 본업과 가치의 중심을 함께 적으면 두 모듈을 붙인다"""
    assert modules.pick(meta("가상생명", sector="보험, 지주회사"))[0] == ["insurance", "holding"]
    assert modules.pick(meta("A사", sector="제조업, 소비재, 은행"))[0] == ["manufacturing", "consumer"]   # 3개째는 버림
    assert modules.pick(meta("A사", valuation_method="SOTP", sector="보험"))[0] == ["insurance", "holding"]
