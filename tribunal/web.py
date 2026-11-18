"""웹 대시보드가 부르는 입구. 분석 순서와 규칙은 runner.py 를 그대로 쓴다.

python -m tribunal.web estimate <PDF> <provider>
    호출 수, 토큰 상한, 모델, 쪽수를 JSON 한 줄로 출력한다. AI를 부르지 않는다(비용 0).

python -m tribunal.web run <보관함 폴더> <provider> [--mock]
    <보관함 폴더>/report.pdf 를 분석해 <보관함 폴더>/run/ 에 결과를 쓰고,
    끝나면 <보관함 폴더>/done.json 에 성공 여부를 남긴다. 대시보드의 기존 데이터는 건드리지 않는다.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

from .runner import load_config, plan, run_pdf


def _write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _cfg(provider: str) -> dict:
    cfg = load_config()
    if provider:
        cfg["provider"] = provider
    return cfg


def estimate(pdf: Path, provider: str) -> int:
    from .extract import read_pages
    cfg = _cfg(provider)
    try:
        est = plan(pdf, cfg, cfg.get("jurors", 5))
        est["pages"] = len(read_pages(pdf))
        print(json.dumps({"ok": True, **est}, ensure_ascii=False))
    except Exception as e:  # PDF가 깨졌거나 읽을 수 없을 때
        print(json.dumps({"ok": False, "error": f"PDF를 읽지 못했습니다 ({type(e).__name__})"}, ensure_ascii=False))
    return 0


def run(entry: Path, provider: str, mock: bool) -> int:
    done = entry / "done.json"
    now = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731
    try:
        run_pdf(entry / "report.pdf", mock=mock, cfg=_cfg(provider), out_root=entry, run_id="run")
        _write(done, {"ok": True, "finished_at": now()})
        return 0
    except RuntimeError as e:          # llm.py 가 키를 빼고 쉬운 말로 만든 오류
        msg = str(e)
        saved = entry / "run"
        if (saved / "claims.json").exists() or (saved / "steps").exists():
            msg += " 끝난 단계는 저장해 두었으니, 다시 시도하면 멈춘 곳부터 이어서 합니다."
        _write(done, {"ok": False, "error": msg, "finished_at": now()})
    except Exception as e:             # 그 밖의 오류는 종류만 남긴다
        _write(done, {"ok": False, "error": f"예상하지 못한 오류로 멈췄습니다 ({type(e).__name__})", "finished_at": now()})
    return 1


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[0] == "estimate":
        return estimate(Path(argv[1]), argv[2] if len(argv) > 2 else "")
    if len(argv) >= 2 and argv[0] == "run":
        return run(Path(argv[1]), argv[2] if len(argv) > 2 else "", "--mock" in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
