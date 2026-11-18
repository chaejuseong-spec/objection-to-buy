"""AI 호출. --mock 이면 API를 부르지 않고 mock/ 폴더의 정해진 답을 쓴다.
형식이 틀리거나 비면 한 번만 다시 부른다. 그래도 실패하면 None(기권).
서버 일시 오류는 _real_call 이 기다렸다가 다시 부르고, 끝내 안 되면 RuntimeError 로 알린다."""
from __future__ import annotations
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Type

from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent


def role_group(role: str) -> str:
    """config 의 roles 에서 찾을 이름. 배심원 다섯 명(juror_accountant 등)은 'juror' 하나로 묶는다."""
    return "juror" if role.startswith("juror") else role


def settings(cfg: dict, role: str = "") -> dict:
    """config.yaml 에서 지금 쓰는 회사의 설정을 꺼낸다.
    role 을 주면 roles 에 적힌 그 역할의 모델·생각 깊이를 쓰고, 없으면 기본값을 쓴다.
    단가는 prices 표에서 모델 이름으로 찾는다. 표에 없으면 기본 모델일 때만 price_per_mtok_* 를 쓴다."""
    name = cfg.get("provider", "claude")
    providers = cfg.get("providers") or {}
    if name not in providers:
        raise RuntimeError(f"config.yaml 의 provider '{name}' 설정이 없습니다. claude 또는 gemini 를 쓰세요.")
    p = providers[name]
    over = (p.get("roles") or {}).get(role_group(role)) or {} if role else {}
    model = over.get("model") or p["model"]
    price = (p.get("prices") or {}).get(model)
    if price is None and model == p["model"]:
        price = {"input": p.get("price_per_mtok_input_usd"), "output": p.get("price_per_mtok_output_usd")}
    price = price or {}
    p_in = float(price.get("input") or 0)
    return {"provider": name, "model": model, "key_env": p["key_env"], "role": role,
            "price_in": p_in,
            "price_out": float(price.get("output") or 0),
            "price_cache_read": float(price.get("cache_read") or p_in * 0.1),
            "token_factor": float(p.get("output_token_factor") or 1),
            "effort": over.get("effort", p.get("effort")),
            "fallbacks": list(p.get("fallback_models") or [])}


USAGE: List[dict] = []          # 실제 호출마다 쓴 토큰과 비용. runner 가 실행 폴더의 usage.json 으로 남긴다
_usage_lock = threading.Lock()


def _record_usage(st: dict, model: str, u) -> None:
    """한 번 호출에 쓴 토큰과 비용을 적는다. 캐시 쓰기는 입력 단가의 1.25배, 캐시 읽기는 cache_read 단가."""
    cin, cr, cw, out = (u.input_tokens or 0, getattr(u, "cache_read_input_tokens", 0) or 0,
                        getattr(u, "cache_creation_input_tokens", 0) or 0, u.output_tokens or 0)
    usd = (cin * st["price_in"] + cw * st["price_in"] * 1.25 + cr * st["price_cache_read"]
           + out * st["price_out"]) / 1e6
    with _usage_lock:
        USAGE.append({"role": st.get("role") or "", "model": model, "effort": st.get("effort"),
                      "input": cin, "cache_read": cr, "cache_write": cw, "output": out,
                      "usd": round(usd, 4)})


def get_key(env_name: str = "ANTHROPIC_API_KEY") -> str:
    """API 키는 이 함수로만 읽는다. 키를 화면이나 로그에 찍지 않는다."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise RuntimeError(f"API 키가 없습니다. .env.example 을 .env 로 복사하고 {env_name}= 뒤에 키를 넣어주세요.")
    return key


def load_common() -> str:
    return (ROOT / "prompts" / "_common.md").read_text(encoding="utf-8")


def load_role(role: str, extra: str = "") -> str:
    text = (ROOT / "prompts" / f"{role}.md").read_text(encoding="utf-8")
    if extra:                          # 코드가 고른 업종별 모듈 (tribunal/modules.py)
        text += "\n\n" + extra
    if role.startswith("juror_"):      # 다섯 배심원이 같이 따르는 판단 순서·표 기준·이유 쓰기
        text += "\n\n" + (ROOT / "prompts" / "_juror.md").read_text(encoding="utf-8")
    return text


def load_prompt(role: str, extra: str = "") -> str:
    return load_role(role, extra) + "\n\n" + load_common()


def document_text(pages: List[dict]) -> str:
    """리스크 검토자·배심원에게 줄 리포트 원문. 쪽 번호를 붙여 인용할 때 쪽을 적을 수 있게 한다."""
    body = "\n\n".join(f"=== {p['page']}쪽 ===\n{p['text'].strip()}" for p in pages)
    return "# 리포트 원문 (PDF에서 읽은 글, 쪽 번호 포함)\n\n" + body


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def claude_content(user: str, doc: Optional[str] = None) -> list:
    """Claude 에 보낼 사용자 메시지. 원문(doc)이 있으면 맨 앞에 두고 캐시 표시를 붙인다.
    리스크 검토자·배심원이 같은 원문을 받으므로, 두 번째 호출부터 원문 부분은 캐시에서 싸게 읽힌다.
    캐시는 앞부분이 글자까지 같아야 맞으므로 원문 뒤에 역할별 지시를 둔다."""
    if not doc:
        return [{"type": "text", "text": user}]
    return [{"type": "text", "text": doc, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user}]


def _call_claude(system: str, user: str, st: dict, max_tokens: int, timeout: int, doc: Optional[str] = None) -> str:
    """한 번만 부른다. 다시 시도는 _real_call 이 한다.
    답이 길 수 있어 스트리밍으로 받는다(긴 요청이 중간에 끊기지 않게). 생각(thinking) 블록은 버리고 글만 쓴다.
    안전 거절(refusal)이면 서버가 다른 모델로 이어 답하게 한다(fallbacks: default)."""
    import anthropic
    client = anthropic.Anthropic(api_key=get_key(st["key_env"]), timeout=timeout, max_retries=0)
    extra = {"output_config": {"effort": st["effort"]}} if st.get("effort") else {}
    with client.beta.messages.stream(model=st["model"], max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": claude_content(user, doc)}],
                                     betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                     **extra) as stream:
        msg = stream.get_final_message()
    u = msg.usage
    cache = f", 캐시 읽기 {u.cache_read_input_tokens or 0:,} / 캐시 쓰기 {u.cache_creation_input_tokens or 0:,}" if doc else ""
    _log(f"[{msg.model}] 토큰 사용: 입력 {u.input_tokens:,} / 출력 {u.output_tokens:,} (생각 포함){cache}")
    _record_usage(st, msg.model, u)
    if msg.stop_reason == "refusal":
        raise RuntimeError("AI가 이 요청에 답하지 않았습니다(안전 거절).")
    if msg.stop_reason == "max_tokens":
        _log(f"[{st['model']}] 출력 한도({max_tokens}토큰)에 닿아 답이 잘렸을 수 있습니다")
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _call_gemini(system: str, user: str, st: dict, max_tokens: int, timeout: int, doc: Optional[str] = None) -> str:
    """한 번만 부른다. 다시 시도는 _real_call 이 한다.
    timeout 은 구글 서버에도 마감 시간으로 전달된다. 넘기면 서버가 504 DEADLINE_EXCEEDED 를 돌려준다."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=get_key(st["key_env"]),
                          http_options=types.HttpOptions(timeout=timeout * 1000))
    config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens,
                                         response_mime_type="application/json")
    res = client.models.generate_content(model=st["model"], contents=(doc + "\n\n" + user) if doc else user, config=config)
    return res.text or ""


_CALLERS = {"claude": _call_claude, "gemini": _call_gemini}

RETRY_CODES = {429, 500, 502, 503, 504}   # 잠시 뒤 다시 하면 될 수 있는 오류
_slot_lock = threading.Lock()
_slot: Optional[threading.BoundedSemaphore] = None
_slot_n = 0


def _limit(cfg: dict) -> threading.BoundedSemaphore:
    """동시에 AI를 부르는 수를 config 의 max_concurrent_calls 로 묶는다 (1이면 하나씩 차례로)."""
    global _slot, _slot_n
    n = max(1, int(cfg.get("max_concurrent_calls", 2)))
    with _slot_lock:
        if _slot is None or _slot_n != n:
            _slot, _slot_n = threading.BoundedSemaphore(n), n
        return _slot


def _code(e: Exception):
    return getattr(e, "code", None) or getattr(e, "status_code", None)


def _daily_quota(e: Exception) -> Optional[str]:
    """하루 한도(무료 등급의 하루 요청 수 등)에 걸렸으면 쉬운 안내문, 아니면 None.
    하루 한도는 기다려도 그날 안에는 풀리지 않으므로 다시 시도하지 않는다."""
    if _code(e) != 429:
        return None
    text = json.dumps(getattr(e, "details", None), ensure_ascii=False, default=str) + str(getattr(e, "message", ""))
    if "PerDay" not in text:
        return None
    limit = re.search(r'"quotaValue":\s*"(\d+)"', text)
    delay = re.search(r'"retryDelay":\s*"(\d+)', text)
    free = "무료 등급의 " if "FreeTier" in text or "free_tier" in text else ""
    msg = f"{free}하루 사용 한도" + (f"(하루 {limit.group(1)}번)" if limit else "") + "를 다 썼습니다."
    if delay:
        h, m = divmod(int(delay.group(1)) // 60, 60)
        msg += f" 약 {h}시간 {m}분 뒤에 다시 쓸 수 있습니다."
    return msg + " 더 많이 쓰려면 Google AI Studio 에서 결제를 켜 등급을 올리세요."


def _retryable(e: Exception) -> bool:
    if _daily_quota(e):
        return False
    return _code(e) in RETRY_CODES or "Timeout" in type(e).__name__


def _log(msg: str) -> None:
    """진행 상황을 log.txt 로 남긴다 (웹에서 돌릴 때 stderr 가 log.txt 로 간다). 키는 찍지 않는다."""
    print(msg, file=sys.stderr, flush=True)


def _friendly(provider: str, model: str, e: Exception, tried: str = "") -> RuntimeError:
    """에러 메시지에 키가 섞이지 않게 종류와 번호만 알린다."""
    code, status = _code(e), getattr(e, "status", None)
    detail = " ".join(str(x) for x in (type(e).__name__, code, status) if x)
    daily = _daily_quota(e)
    if daily:
        return RuntimeError(f"API 호출 실패({provider}, {model}): {daily}")
    hint = ""
    if code == 504 or "Timeout" in type(e).__name__:
        hint = " - 정해진 시간 안에 답이 오지 않았습니다. AI 회사 서버가 바쁘거나 요청이 큽니다. 잠시 뒤 다시 시도하세요."
    elif code in (500, 502, 503):
        hint = " - AI 회사 서버가 바쁜 일시 오류입니다. 잠시 뒤 다시 시도하세요."
    elif code == 429:
        hint = " - 사용 한도(요청 수/요금제)에 걸렸습니다. 잠시 뒤 또는 한도를 확인하세요."
    elif code in (401, 403):
        hint = " - API 키가 맞지 않거나 권한이 없습니다. .env 의 키를 확인하세요."
    elif code == 404:
        hint = " - 모델 이름을 찾지 못했습니다. config.yaml 의 model 을 확인하세요."
    return RuntimeError(f"API 호출 실패({provider}, {model}{tried}): {detail}{hint}")


def _real_call(system: str, user: str, cfg: dict, role: str = "", doc: Optional[str] = None) -> str:
    """회사가 달라도 여기 한 곳을 거친다. 나머지 코드는 어느 회사인지 모른다.
    일시 오류(429, 5xx, 시간 초과)는 기다리는 시간을 두 배씩 늘리며 최대 retries 번 다시 부르고,
    그래도 안 되면 fallback_models 의 다음 모델로 바꿔 같은 일을 한다."""
    st = settings(cfg, role)
    if st["provider"] not in _CALLERS:
        raise RuntimeError(f"아직 지원하지 않는 provider 입니다: {st['provider']}")
    caller = _CALLERS[st["provider"]]
    max_tokens = int(cfg["max_output_tokens"] * st["token_factor"])
    timeout = int(cfg.get("timeout_sec", 300))
    retries = max(0, int(cfg.get("retries", 3)))
    base = float(cfg.get("retry_base_sec", 5))
    models = [st["model"]] + [m for m in st["fallbacks"] if m != st["model"]]
    who = f"[{role}] " if role else ""
    last: Optional[Exception] = None
    for i, model in enumerate(models):
        for attempt in range(retries + 1):
            try:
                with _limit(cfg):
                    return caller(system, user, dict(st, model=model), max_tokens, timeout, **({"doc": doc} if doc else {}))
            except RuntimeError:
                raise                      # 키 없음 같은 안내는 그대로 보여준다
            except ImportError:
                raise RuntimeError(f"{st['provider']} 용 패키지가 없습니다. pip install -r requirements.txt 를 실행하세요.") from None
            except Exception as e:
                if not _retryable(e):
                    if i > 0 and last is not None:          # 예비 모델 자체의 문제(없는 모델 등)면 원래 오류를 알린다
                        _log(f"{who}예비 모델 {model} 을(를) 쓸 수 없습니다 ({_code(e) or type(e).__name__})")
                        break
                    raise _friendly(st["provider"], model, e) from None
                last = e
                if attempt < retries:
                    start = max(base, 30.0) if _code(e) == 429 else base    # 사용 한도는 분 단위라 오래 쉰다 (30, 60, 120초)
                    wait = start * (2 ** attempt) + random.uniform(0, base / 2)
                    _log(f"{who}{model} {_code(e) or type(e).__name__}: {wait:.0f}초 뒤 다시 시도 ({attempt + 1}/{retries})")
                    time.sleep(wait)
        if i + 1 < len(models):
            _log(f"{who}{model} 이(가) 계속 응답하지 않아 {models[i + 1]} 로 바꿔 시도합니다")
    tried = f", 시도한 모델 {len(models)}개" if len(models) > 1 else ""
    raise _friendly(st["provider"], models[0], last, tried) from None  # type: ignore[arg-type]


def call(role: str, payload: dict, schema: Type[BaseModel], cfg: dict, mock: bool,
         mock_dir: Optional[Path] = None, document: Optional[str] = None, extra: str = "") -> Optional[BaseModel]:
    """role: prosecutor / juror_<key> / judge. 실패하면 None.
    document(리포트 원문)를 주면 공통 규칙 -> 원문 -> 역할 지시서 -> 사건 파일 순서로 보낸다.
    앞의 두 부분이 모든 역할에서 같아서 Claude 캐시가 맞는다."""
    if mock:
        path = (mock_dir or ROOT / "mock") / f"{role}.json"
        try:
            return schema.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None
    case = ("아래 JSON이 사건 파일이다. 지시문의 '넘길 것' 형식에 맞는 JSON 하나만 답하라.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1))
    if document:
        system = load_common()
        user = load_role(role, extra) + "\n\n" + case
    else:
        system, user = load_prompt(role, extra), case
    text = ""
    for attempt in range(2):  # 처음 1번 + 재시도 1번
        text = _real_call(system, user, cfg, role, doc=document)
        try:
            data = _normalize(_extract_json(text))
        except (ValueError, json.JSONDecodeError):
            _log(f"[{role}] 답이 JSON이 아닙니다 ({attempt + 1}/2)")
            continue
        try:
            return schema.model_validate(data)
        except ValidationError as e:
            fixed, n_drop = _salvage(data, schema)
            if fixed is not None:          # 틀린 항목만 빼고 나머지를 쓴다 (표 전체를 버리지 않는다)
                _log(f"[{role}] 형식이 틀린 항목 {n_drop}개를 빼고 나머지를 씁니다: {_brief(e)}")
                return fixed
            _log(f"[{role}] 형식이 맞지 않습니다 ({attempt + 1}/2): {_brief(e)}")
    FAILED_RAW[role] = text                # 원인을 볼 수 있게 마지막 답을 남긴다 (runner 가 파일로 저장)
    return None


FAILED_RAW: dict = {}

# AI가 띄어쓰기나 어미를 바꿔 쓴 값을 정해진 값으로 되돌린다. 뜻이 다른 말은 바꾸지 않는다.
_CANON = ["숫자의 기준", "기간 선택", "가정의 근거", "논리 연결", "빠진 위험", "근거의 독립성",
          "사실 오류", "해석 차이", "타당", "과장", "확인 불가",
          "산업", "기업·재무", "밸류에이션", "센터장", "크다", "작다"]
_ALIAS = {c.replace(" ", ""): c for c in _CANON}
_ALIAS.update({"타당함": "타당", "타당하다": "타당", "과장됨": "과장", "과장이다": "과장",
               "확인불가능": "확인 불가", "판단불가": "확인 불가", "기업재무": "기업·재무", "기업/재무": "기업·재무"})
_ENUM_KEYS = {"doubt_type", "kind", "vote", "fix_part", "impact"}


def _normalize(v, key: str = ""):
    if isinstance(v, dict):
        return {k: _normalize(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_normalize(x, key) for x in v]
    if key in _ENUM_KEYS and isinstance(v, str):
        return _ALIAS.get(v.replace(" ", "").strip(".").strip(), v)
    return v


def _salvage(data: dict, schema: Type[BaseModel]):
    """목록 안의 틀린 항목, 틀린 선택 항목(예: 배심원의 추가 이의)만 빼고 다시 맞춰 본다.
    빠진 표는 그 이의에 대한 기권으로 처리된다. 꼭 있어야 할 칸이 틀렸으면 (None, 0)."""
    from typing import get_args, get_origin
    from pydantic import TypeAdapter
    if not isinstance(data, dict):
        return None, 0
    out, dropped = dict(data), 0
    for name, field in schema.model_fields.items():
        if name not in out:
            continue
        ann = field.annotation
        if get_origin(ann) in (list, List):
            item = TypeAdapter(get_args(ann)[0])
            kept = []
            for x in out[name] or []:
                try:
                    item.validate_python(x)
                    kept.append(x)
                except ValidationError:
                    dropped += 1
            out[name] = kept
        elif not field.is_required():
            try:
                TypeAdapter(ann).validate_python(out[name])
            except ValidationError:
                out[name] = field.get_default(call_default_factory=True)
                dropped += 1
    if not dropped:
        return None, 0
    try:
        return schema.model_validate(out), dropped
    except ValidationError:
        return None, 0


def _brief(e: ValidationError) -> str:
    """어느 칸이 왜 틀렸는지 짧게 (답 내용은 길게 찍지 않는다)."""
    errs = e.errors()[:2]
    return "; ".join(".".join(str(p) for p in x["loc"]) + " " + x["type"] for x in errs) + (" …" if len(e.errors()) > 2 else "")


def estimate(payloads_chars: int, n_calls: int, cfg: dict, roles: Optional[List[str]] = None) -> dict:
    """아주 거친 비용 어림. 한글은 글자당 약 1토큰으로 넉넉하게 잡는다. 단가가 비어 있으면 금액은 None.
    roles 를 주면(호출마다 역할 하나) 역할별 모델의 단가로 따로 계산한다. 입력은 호출 수로 나눠 어림한다."""
    roles = roles or (["prosecutor"] + [""] * (n_calls - 1))
    tin, tout, usd, priced, models = payloads_chars, 0, 0.0, True, []
    for r in roles:
        st = settings(cfg, r)
        limit = cfg.get("prosecutor_max_output_tokens", cfg["max_output_tokens"]) if r == "prosecutor" else cfg["max_output_tokens"]
        o = int(limit * st["token_factor"])                 # 리스크 검토자는 한도가 더 크다
        tout += o
        priced = priced and (st["price_in"] > 0 or st["price_out"] > 0)
        usd += payloads_chars / len(roles) / 1e6 * st["price_in"] + o / 1e6 * st["price_out"]
        if st["model"] not in models:
            models.append(st["model"])
    st = settings(cfg)
    return {"calls": len(roles), "input_tokens_max": tin, "output_tokens_max": tout,
            "usd_max": round(usd, 2) if priced else None,
            "provider": st["provider"], "model": ", ".join(models)}
