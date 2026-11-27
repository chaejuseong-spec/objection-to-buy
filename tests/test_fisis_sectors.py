"""업권별 사업 지표 (tribunal/external/fisis.py, 2026-11-24 확정). 가상 숫자"""
from tribunal.external import fisis


def test_ten_sectors_and_no_roe_roa_from_fisis():
    """10개 업권 + 붙이기(할부·신기술 = 캐피탈, 선물 = 증권). ROE·ROA 는 DART 로 계산하므로 금감원 목록에 없다"""
    names = {fisis.SECTORS[p]["name"] for p in "ALFCKHIEMG"}
    assert names == {"은행", "금융지주", "증권", "카드", "캐피탈", "생명보험", "손해보험", "저축은행", "부동산신탁", "자산운용"}
    assert fisis.SECTORS["T"]["metrics"] == fisis.SECTORS["K"]["metrics"] == fisis.SECTORS["N"]["metrics"]
    assert fisis.SECTORS["W"]["metrics"] == fisis.SECTORS["F"]["metrics"]
    for p, sec in fisis.SECTORS.items():
        assert not {m["key"] for m in sec["metrics"]} & {"roe", "roa", "ins_profit", "inv_profit"}, p


def test_pick_prefers_exact_name():
    """'BIS기준 자기자본비율' 을 찾을 때 '…_보통주자본 비율' 줄을 잡지 않는다"""
    rows = {"BIS기준 자기자본비율_보통주자본 비율": {"a": "13.5"}, "BIS기준 자기자본비율": {"a": "16.2"}}
    assert fisis._pick(rows, "BIS기준 자기자본비율") == 16.2
    assert fisis._pick(rows, "BIS기준 자기자본비율_보통주자본 비율") == 13.5


class FakeFisis:
    """반기 지표(보험 유지율)는 term='H' 로만 값이 있다"""
    def __init__(self):
        self.calls = []

    def stat(self, cd, no, month, term="Q"):
        self.calls.append((no, month, term))
        if no == "KEEP":
            return {"13회차 계약유지율": {"a": "85.1"}, "25회차 계약유지율": {"a": "70.2"}} if term == "H" else {}
        if no == "CAP":
            return {"지급여력비율(경과조치 적용 전)": {"a": "190.0"}}
        return {}


def test_half_year_metrics_only_on_june_and_december():
    names = {"자본적정성": "CAP", "보험계약 유지율(13회, 25회)": "KEEP", "경영효율지표": "EFF"}
    fs = FakeFisis()
    jun = fisis._metric_values(fs, names, "X", "H", "202506")
    sep = fisis._metric_values(fs, names, "X", "H", "202509")
    assert jun["keep13"] == 85.1 and jun["keep25"] == 70.2 and jun["kics"] == 190.0
    assert sep["keep13"] is None and sep["kics"] == 190.0
    assert ("KEEP", "202509", "H") not in fs.calls                    # 3·9월에는 반기 표를 부르지 않는다


def test_numbers_off_keeps_only_sector(monkeypatch):
    """전면 개편 1차 (2026-11-25): FISIS 숫자는 끄고 업권 판단(회사 목록)만. 통계표·숫자는 부르지 않는다"""
    class FakeFs:
        def companies(self, part):
            return [{"finance_cd": "001", "finance_nm": "가상카드주식회사"}] if part == "C" else []

        def tables(self, part):
            raise AssertionError("숫자 표를 부르면 안 된다")

        def stat(self, *a, **k):
            raise AssertionError("숫자를 부르면 안 된다")
    assert fisis.NUMBERS is False
    monkeypatch.setattr(fisis, "METRICS", False)
    out = fisis.sector("가상카드", fs=FakeFs())
    assert out["part"] == "C" and out["sector"] == "카드"
    assert out["series"] == [] and out["peers"] == [] and out["metrics"] == []
