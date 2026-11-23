# 이 저장소에서 일하는 규칙 (Claude Code용)

## 무엇인가
기업분석 리포트 검토 에이전트 (리스크 검토자, 배심원, 판사). 리포트를 평가만 하고 고치지 않는다.

## 반드시 지킬 것
- AI는 숫자를 만들지 않는다. 판정·집계·리포트 평가는 `tribunal/rules.py`의 코드가 정한다.
- 원본 리포트 파일을 고치지 않는다. 결과는 `.tribunal/<run_id>/`에만 쓴다.
- AI 회사(claude, gemini)는 `config.yaml`의 `provider`로 고른다. 호출은 `tribunal/llm.py`의 `_real_call()` 한 곳을 거친다.
- API 키는 `tribunal/llm.py`의 `get_key()`로만 읽는다. 화면·로그·에러에 키를 찍지 않는다.
- `.env`와 `.tribunal/`은 커밋하지 않는다.
- 테스트와 데모는 `--mock`으로 한다. 실제 API는 사용자가 허락할 때만 부른다.
- 다른 파일을 지우거나 `git push`를 하지 않는다.
- 팀 배포 레포의 `collect.py`, `engine.py`는 건드리지 않는다. 이 에이전트는 개인 레포에 따로 둔다.

## 고칠 때
- 역할 지시문은 `prompts/*.md`에 있다. 형식은 목적 / 데이터 범위 / 쓰기 규칙 / 넘길 것 / 멈출 조건.
- 규칙을 바꾸면 `tests/test_rules.py`에 테스트를 같이 고치고 `pytest`를 돌린다.
- 사용자는 비개발자다. 무엇을 왜 바꿨는지 한두 줄로 쉬운 말로 알린다. 결정이 필요한 것은 추측하지 말고 묻는다.

## 자주 쓰는 명령
- `python -m tribunal demo/sample_report.pdf --mock --demo --dashboard dashboard`
- `pytest`
