"""실행: python -m tribunal 리포트.pdf --mock --dashboard dashboard"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .runner import load_config, plan, run, run_pdf


def main() -> int:
    p = argparse.ArgumentParser(prog="tribunal", description="매수에 이의 있습니다! 반대신문 에이전트")
    p.add_argument("report", type=Path, help="리포트 PDF. (이미 옮겨 적은 report.json 도 받는다)")
    p.add_argument("--mock", action="store_true", help="API를 부르지 않고 정해진 가짜 답으로 실행")
    p.add_argument("--jurors", type=int, choices=[3, 5], help="배심원 수 (기본: config.yaml)")
    p.add_argument("--pdf", type=Path, help="report.json 을 넣을 때만: 리포트 원문 PDF")
    p.add_argument("--dashboard", type=Path, default=None, help="대시보드 폴더. 주면 그 안의 데이터를 갱신한다")
    p.add_argument("--provider", choices=["claude", "gemini"], help="이번 실행만 다른 회사로 (기본: config.yaml)")
    p.add_argument("--demo", action="store_true", help="결과에 DEMO 표시")
    p.add_argument("--yes", action="store_true", help="비용 확인 질문을 건너뜀")
    a = p.parse_args()
    cfg = load_config()
    if a.provider:
        cfg["provider"] = a.provider
    n = a.jurors or cfg.get("jurors", 5)
    is_pdf = a.report.suffix.lower() == ".pdf"
    try:
        est = plan(a.report, cfg, n)
    except RuntimeError as e:
        print(f"멈췄습니다: {e}")
        return 1
    print(f"AI 호출 {est['calls']}번 (" + ("추출 1 · " if is_pdf else "") + f"리스크 검토자 1 · 배심원 {n} · 판사 1)")
    if a.mock:
        print("MOCK 모드: API를 부르지 않습니다. 비용 0원.")
    else:
        print(f"{est['provider']} · 모델 {est['model']} · 입력 최대 약 {est['input_tokens_max']:,}토큰 · 출력 최대 {est['output_tokens_max']:,}토큰")
        if est["usd_max"] is None:
            print("예상 비용: 계산하지 못함 (config.yaml 에 이 회사의 단가가 비어 있습니다. 가격 페이지에서 확인하세요)")
        else:
            print(f"예상 비용 상한: 약 ${est['usd_max']} (config.yaml 단가 기준, 단가는 직접 확인 필요)")
        if not a.yes and input("실제 API를 부를까요? (y/N) ").strip().lower() != "y":
            print("취소했습니다.")
            return 1
    try:
        if is_pdf:
            out = run_pdf(a.report, mock=a.mock, n_jurors=n, cfg=cfg, dashboard=a.dashboard, demo=a.demo)
        else:
            out = run(a.report, mock=a.mock, n_jurors=n, cfg=cfg, pdf=a.pdf, dashboard=a.dashboard)
    except RuntimeError as e:
        print(f"멈췄습니다: {e}")
        return 1
    print(f"끝났습니다. 결과 폴더: {out}")
    if is_pdf:
        print(f"원문 대조 기록: {out / 'extraction.json'}")
    if a.dashboard:
        print(f"대시보드 데이터를 갱신했습니다: {a.dashboard}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
