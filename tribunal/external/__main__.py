"""이미 끝난 분석에 공식 데이터 대조를 더한다 (Gemini 를 부르지 않는다).

python -m tribunal.external <분석 결과 폴더>              # 키가 있는 것 모두 (DART, KRX, ECOS, KOSIS)
python -m tribunal.external <분석 결과 폴더> --only krx   # 하나만
python -m tribunal.external <분석 결과 폴더> --only dart --pool   # DART + 공시 통째 짝 찾기 (Haiku 호출, 유료)
python -m tribunal.external <분석 결과 폴더> --only dart --saved  # 저장된 대조 결과로 다시 합치기 (무료, 지금 규칙으로 틀린 재료 짝을 뺀다)
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import common, dart_check, ecos_check, kosis_check, krx_check
from .dart import Dart
from .ecos import Ecos
from .kosis import Kosis
from .krx import Krx

ALL = {"dart": (dart_check, Dart), "krx": (krx_check, Krx), "ecos": (ecos_check, Ecos), "kosis": (kosis_check, Kosis)}


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    names = [only] if only else list(ALL)
    if only and only not in ALL:
        print(f"--only 는 {', '.join(ALL)} 중 하나입니다.")
        return 2
    offline = "--offline" in argv
    checks = {n: (ALL[n][0], ALL[n][1](offline=offline)) for n in names}
    exts = common.augment(Path(args[0]), checks, pool="--pool" in argv, saved="--saved" in argv)   # --pool: DART 통째 짝 찾기 (AI 호출, 유료)
    for n, ext in exts.items():
        common.print_report(n, ext)
    return 1 if any(e.get("error") and e.get("enabled") for e in exts.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
