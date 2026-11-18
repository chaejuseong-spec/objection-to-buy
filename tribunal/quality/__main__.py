"""검증 품질 평가 (오류 삽입 테스트)

python -m tribunal.quality make <분석 결과 폴더>     끝난 분석에서 테스트 세트를 만든다 (무료)
python -m tribunal.quality plan [세트 이름]          돌리면 Gemini 를 몇 번 부르는지 미리 본다 (무료)
python -m tribunal.quality run [세트 이름] --yes     돌리고 채점한다. --yes 가 없으면 저장된 결과로만 돈다
python -m tribunal.quality report [세트 이름]        지난 실행들과 비교한다 (무료)

사본 하나에 Gemini 1번(리포트 입력). 지시서와 모델이 그대로면 저장해 둔 결과를 써서 0번이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..runner import load_config
from . import harness, inject


def _cfg() -> dict:
    cfg = load_config()
    cfg["provider"] = "gemini"            # 웹과 같은 회사
    cfg["providers"]["gemini"]["fallback_models"] = []   # 평가는 한 모델로만 잰다 (예비 모델 점수가 섞이지 않게)
    return cfg


def _sets(name=None):
    if name:
        return [inject.SETS / name]
    return sorted(p for p in inject.SETS.glob("*") if (p / "cases.json").exists()) if inject.SETS.exists() else []


def _pct(v):
    return "–" if v is None else f"{round(v * 100)}%"


def show(rec: dict) -> None:
    m = rec["metrics"]
    v = rec["version"]
    print(f"\n== {rec['set']} ({rec['report']}) | {rec['at']} | 코드 {v['commit']}{'+수정' if v['dirty'] else ''} | {rec['model']}")
    print(f"  넣은 오류 {m['injected']}개 (검증 수단 있음 {m['checkable']}개) 중 {m['caught']}개 잡음")
    print(f"  탐지율: 검증 수단 있는 것 {_pct(m['detect_rate_checkable'])} / 전체 {_pct(m['detect_rate_all'])} | 바꾼 숫자를 뽑은 비율 {_pct(m['extracted_rate'])}")
    print(f"  종류별: " + ", ".join(f"{k} {x['caught']}/{x['total']}" for k, x in m["by_expect"].items()))
    print(f"  놓친 이유: {m['missed_why'] or '없음'}")
    print(f"  원본 불일치 {m['clean_mismatches']}건, 추가 오탐 {m['extra_false_alarms']}건, 검증 불가 비율 {_pct(m['unverifiable_rate'])} (원본 검증 {m['clean_checks']}건)")
    print(f"  시간 {m['seconds']}초, Gemini {m['gemini_calls']}번, 공식 데이터 {m['external_calls']}번")
    for c in rec["cases"]:
        if c.get("error"):
            print(f"  [{c['id']}] 추출 실패: {c['error']}")
        for x in c["mutations"]:
            print(f"  [{c['id']}] {x['result']} | {x['item']} {x['value_old']} -> {x['value_new']} ({x['page']}쪽, 기대 {x['expect']}) | {x['why']}")
        for x in c["other_mismatches"]:
            print(f"  [{c['id']}] 그 밖의 불일치: {x['check_id']} {x['name']}")
        for k, e in c.get("external_errors", {}).items():
            print(f"  [{c['id']}] {k} 건너뜀: {e[:50]}")


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], [a for a in argv[1:] if not a.startswith("--")]
    if cmd == "make":
        out = inject.make_set(Path(args[0]), args[1] if len(args) > 1 else None)
        meta = json.loads((out / "cases.json").read_text(encoding="utf-8"))
        print(f"세트 '{meta['name']}' 를 만들었습니다: {out}")
        for c in meta["cases"]:
            print(f"  사본 {c['id']}: 바꾼 숫자 {len(c['mutations'])}개")
            for m in c["mutations"]:
                print(f"     {m['page']}쪽 {m['item']}: {m['value_old']} -> {m['value_new']} (기대 {m['expect']})")
        return 0
    sets = _sets(args[0] if args else None)
    if not sets:
        print("테스트 세트가 없습니다. 먼저 make 로 만드세요.")
        return 1
    cfg = _cfg()
    if cmd == "plan":
        for s in sets:
            p = harness.plan(s, cfg)
            print(f"{s.name}: 사본 {p['cases']}개, 넣은 오류 {p['mutations']}개, 새로 부를 Gemini {len(p['need_calls'])}번 {p['need_calls']} ({p['model']})")
        return 0
    if cmd == "run":
        for s in sets:
            try:
                rec = harness.run_set(s, cfg, allow_call="--yes" in argv)
            except RuntimeError as e:
                print(f"{s.name}: {e}")
                return 1
            f = harness.save_run(rec)
            show(rec)
            print(f"  저장: {f}")
        return 0
    if cmd == "report":
        for s in sets:
            hist = harness.history(s.name)
            print(f"\n== {s.name}: 실행 {len(hist)}번")
            print("  시각               코드       모델                탐지율(수단有) 전체  뽑음  오탐  검증불가  Gemini")
            for r in hist:
                m, v = r["metrics"], r["version"]
                print(f"  {r['at']}  {(v['commit'] + ('+' if v['dirty'] else '')):9}  {r['model'][:18]:18}  {_pct(m['detect_rate_checkable']):>8}  {_pct(m['detect_rate_all']):>6}  {_pct(m['extracted_rate']):>4}  {m['extra_false_alarms']:>4}  {_pct(m['unverifiable_rate']):>6}  {m['gemini_calls']:>4}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
