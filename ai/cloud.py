# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 클라우드 두뇌 — 멀티 프로바이더(클로드 · GPT · OpenAI호환)
#   메인 COMET 대화의 '최종 답변'만 클라우드로 보낼 수 있게 한다.
#   문지기·도구선택 같은 내부 호출은 로컬(ollama) 유지 = 매 턴 과금 방지.
#
#   설계:
#    - 기본 = 로컬(active="local"). 키 없으면 무조건 로컬로 폴백(월 0원 유지).
#    - 토글로 active 를 claude/gpt/deepseek 로 바꾸면 그때만 클라우드.
#    - chat() 은 ollama 와 '같은 모양'으로 반환(dict / 제너레이터) → comet.py 답변
#      호출부를 안 고치고 끼운다.
#    - cost_guard 검문(돈 사고 방지)을 API 호출 전에 통과해야 함.
#   의존성 0(urllib). openai/anthropic SDK 불필요(llm.py 와 같은 raw HTTP 방식).
#   설정: cloud_config.json. 키는 환경변수.
# ═══════════════════════════════════════════════════════════════
import os
import json
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "cloud_config.json")    # 모델·base_url 등(비밀 아님, 공유 가능)
KEYS_FILE = os.path.join(HERE, "cloud_keys.json")        # ⚠️ API 키 저장(비밀·공유 금지)

UA = "COMET/1.0"
TIMEOUT = 300
DEFAULT_MAX_TOKENS = 1024     # comet 답은 짧다 → 폭주 과금 차단(상한)

# ── 프로바이더 레지스트리 ────────────────────────────────────────
#  kind: "anthropic" = /v1/messages(x-api-key),  "openai" = /chat/completions(Bearer)
#  key_env: 그 프로바이더의 API 키를 담는 환경변수 이름.
#  ※ 구독($20/월)이 아니라 종량 과금 API 키다(console.anthropic.com / platform.openai.com).
PROVIDERS = {
    "claude": {
        "kind": "anthropic", "label": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-4-8",          # 가장 강함. 저렴하게=claude-haiku-4-5 / 중간=claude-sonnet-4-6
        "key_env": "ANTHROPIC_API_KEY",
    },
    "gpt": {
        "kind": "openai", "label": "GPT (OpenAI)",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",                   # ⚠️ 본인 계정에서 쓸 모델로 cloud_config.json 에서 바꿔라
        "key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "kind": "openai", "label": "DeepSeek (OpenAI호환·저렴)",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
}

DEFAULTS = {"active": "local", "providers": {}}    # providers = 사용자 오버라이드(모델/base_url 등)


def _load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def _save(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _provider(name):
    """레지스트리 기본값 + cloud_config.json 사용자 오버라이드 병합."""
    base = dict(PROVIDERS.get(name, {}))
    if not base:
        return None
    over = (_load().get("providers") or {}).get(name) or {}
    base.update({k: v for k, v in over.items() if v})
    base["name"] = name
    return base


def _load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_keys(keys):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _key(prov):
    """키 우선순위: 환경변수 → cloud_keys.json(파일). 파일은 매번 새로 읽어 재시작 불필요."""
    env = os.environ.get(prov.get("key_env", ""), "").strip()
    if env:
        return env
    return (_load_keys().get(prov.get("name", ""), "") or "").strip()


def _key_src(prov):
    if os.environ.get(prov.get("key_env", ""), "").strip():
        return "env"
    if (_load_keys().get(prov.get("name", ""), "") or "").strip():
        return "파일"
    return None


# 명령어용 프로바이더 별칭 해석 (클로드/지피티/딥시크 → 키 이름)
def resolve_provider(word):
    w = (word or "").strip().lower()
    aliases = {
        "클로드": "claude", "claude": "claude", "앤트로픽": "claude", "anthropic": "claude",
        "gpt": "gpt", "지피티": "gpt", "챗gpt": "gpt", "오픈ai": "gpt", "openai": "gpt",
        "딥시크": "deepseek", "deepseek": "deepseek",
    }
    return aliases.get(w)


def _mask(key):
    key = key.strip()
    return (key[:6] + "…" + key[-4:]) if len(key) > 12 else "설정됨"


def set_key(name, key):
    """API 키를 파일에 저장(재시작 불필요·즉시 적용). 응답은 마스킹(키 노출 안 함)."""
    if name not in PROVIDERS:
        return f"모르는 프로바이더: {name}"
    key = (key or "").strip()
    if not key:
        return "키 값이 비었어."
    keys = _load_keys(); keys[name] = key; _save_keys(keys)
    prov = _provider(name)
    return (f"{prov['label']} 키 저장 완료({_mask(key)}). '{name}로'(예: 클로드로) 하면 바로 쓴다. "
            "재시작 불필요. (키는 cloud_keys.json·대화기록엔 안 남김)")


def clear_key(name):
    if name not in PROVIDERS:
        return f"모르는 프로바이더: {name}"
    keys = _load_keys()
    keys.pop(name, None)
    _save_keys(keys)
    return f"{name} 파일 키 삭제. (환경변수 키가 있으면 그건 그대로 — 그땐 환경변수를 지워라)"


def set_model(name, model):
    """프로바이더 모델 교체(예: claude → claude-haiku-4-5). 재시작 불필요."""
    if name not in PROVIDERS:
        return f"모르는 프로바이더: {name}"
    model = (model or "").strip()
    if not model:
        return "모델 이름이 비었어."
    cfg = _load()
    provs = cfg.get("providers") or {}
    ov = provs.get(name) or {}
    ov["model"] = model
    provs[name] = ov
    cfg["providers"] = provs
    _save(cfg)
    return f"{name} 모델을 '{model}'로 바꿨어. 재시작 불필요."


def is_active():
    """지금 클라우드로 답해야 하면 프로바이더 dict, 아니면 None(=로컬).
       active 가 클라우드인데 키가 없으면 None(자동 로컬 폴백)."""
    cfg = _load()
    name = cfg.get("active", "local")
    if name == "local":
        return None
    prov = _provider(name)
    if not prov or not _key(prov):
        return None
    return prov


def status():
    """콘솔/폰 표시용 상태 문자열."""
    cfg = _load()
    name = cfg.get("active", "local")
    lines = [f"두뇌: {'로컬(ollama)' if name == 'local' else name}"]
    for n, p in PROVIDERS.items():
        prov = _provider(n)
        src = _key_src(prov)
        has = f"키✓({src})" if src else "키✗"
        mark = "← 현재" if n == name else ""
        lines.append(f"  · {n}: {prov['label']} [{prov['model']}] {has} {mark}".rstrip())
    if name != "local" and is_active() is None:
        lines.append("  ⚠️ 현재 프로바이더 키가 없어 실제로는 로컬로 답함.")
    lines.append("  · 키 끼우기: 키 클로드 sk-ant-…   모델 바꾸기: 모델 클로드 claude-haiku-4-5   "
                 "키 빼기: 키삭제 클로드  (재시작 불필요)")
    return "\n".join(lines)


def set_active(name):
    """두뇌 전환. local/claude/gpt/deepseek. 결과 메시지 반환."""
    name = (name or "").strip().lower()
    if name in ("로컬", "local", "ollama"):
        cfg = _load(); cfg["active"] = "local"; _save(cfg)
        return "두뇌를 로컬(ollama)로. 월 0원, 인터넷 없어도 작동."
    if name not in PROVIDERS:
        return f"모르는 두뇌: {name} (local/claude/gpt/deepseek 중에서)"
    prov = _provider(name)
    cfg = _load(); cfg["active"] = name; _save(cfg)
    if not _key(prov):
        return (f"두뇌를 {prov['label']}로 설정했지만 **키가 없다**. 환경변수 "
                f"{prov['key_env']} 에 API 키(종량 과금, 구독 아님)를 넣고 데몬을 재시작해라. "
                f"키 없으면 자동으로 로컬로 답한다.")
    return f"두뇌를 {prov['label']} [{prov['model']}]로. (종량 과금 — cost_guard 천장 적용)"


# ── 메시지 변환 ────────────────────────────────────────────────
def _split_system(messages):
    """[{role,content}] → (system_text, [user/assistant 메시지]).
       Anthropic 은 system 을 top-level 로 받고, 첫 메시지는 user 여야 한다."""
    sys_parts, conv = [], []
    for m in messages:
        role = m.get("role")
        if role == "system":
            sys_parts.append(m.get("content", ""))
        else:
            conv.append({"role": role, "content": m.get("content", "")})
    while conv and conv[0]["role"] != "user":    # 첫 턴은 user 강제(앞쪽 assistant 제거)
        conv.pop(0)
    return "\n\n".join(p for p in sys_parts if p), conv


# ── Anthropic /v1/messages (비스트림) ──────────────────────────
def _anthropic_chat(prov, messages, num_predict):
    key = _key(prov)
    system, conv = _split_system(messages)
    body = {
        "model": prov["model"],
        "max_tokens": num_predict or DEFAULT_MAX_TOKENS,
        "messages": conv or [{"role": "user", "content": "..."}],
    }
    if system:
        body["system"] = system
    # ⚠️ opus-4-8/4.7 은 temperature 보내면 400 → 아예 안 보낸다.
    req = urllib.request.Request(
        prov["base_url"].rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


# ── OpenAI 호환 /chat/completions ──────────────────────────────
def _openai_chat(prov, messages, stream, temperature, num_predict, on_token):
    key = _key(prov)
    body = {
        "model": prov["model"],
        "messages": messages,
        "max_tokens": num_predict or DEFAULT_MAX_TOKENS,
        "stream": bool(stream),
    }
    if temperature is not None:
        body["temperature"] = temperature
    req = urllib.request.Request(
        prov["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "Authorization": f"Bearer {key}", "User-Agent": UA},
    )
    if not stream:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"].get("content", "") or ""

    def _gen():
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
                except Exception:
                    delta = ""
                if delta:
                    if on_token:
                        on_token(delta)
                    yield {"message": {"content": delta}}
    return _gen()


# ── 공개 진입점 — ollama 와 같은 모양 반환 ──────────────────────
def chat(prov, messages, *, stream=False, temperature=None,
         num_predict=DEFAULT_MAX_TOKENS, on_token=None):
    """클라우드로 답한다. cost_guard 검문 통과 필수.
       stream=False → {"message":{"content": 전체텍스트}} (ollama 비스트림과 동형)
       stream=True  → {"message":{"content": 토큰}} 을 yield 하는 제너레이터."""
    import cost_guard
    # 각 답변 = 1콜짜리 독립 작업 → 과제별 카운터 초기화(코더용 상한이 답변 누적에 안 걸리게).
    # 진짜 지갑 가드는 하루 상한(api_calls_per_day · api_tokens_per_day)이 맡는다.
    cost_guard.reset_task()
    est = cost_guard.estimate_tokens(messages) + (num_predict or DEFAULT_MAX_TOKENS)
    ok, why = cost_guard.before_api_call(est)
    if not ok:
        raise cost_guard.Blocked(why)

    try:
        if prov["kind"] == "anthropic":
            if stream:
                gen = _anthropic_stream(prov, messages, num_predict, on_token)
                return _record_stream(gen, messages)
            text = _anthropic_chat(prov, messages, num_predict)
        else:
            if stream:
                gen = _openai_chat(prov, messages, True, temperature, num_predict, on_token)
                return _record_stream(gen, messages)
            text = _openai_chat(prov, messages, False, temperature, num_predict, on_token)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise RuntimeError(f"클라우드 API 오류 {e.code}: {detail}") from e

    cost_guard.record(cost_guard.estimate_tokens(messages, text))
    return {"message": {"content": text}}


def _anthropic_stream(prov, messages, num_predict, on_token):
    """Anthropic 스트리밍 제너레이터(위 _anthropic_chat 의 스트림 분기를 깔끔히 분리)."""
    key = _key(prov)
    system, conv = _split_system(messages)
    body = {"model": prov["model"], "max_tokens": num_predict or DEFAULT_MAX_TOKENS,
            "messages": conv or [{"role": "user", "content": "..."}], "stream": True}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        prov["base_url"].rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta" and d.get("text"):
                    if on_token:
                        on_token(d["text"])
                    yield {"message": {"content": d["text"]}}
            elif ev.get("type") == "message_stop":
                break


def _record_stream(gen, messages):
    """스트림을 흘려보내되 끝나면 cost_guard 에 사용량 기록."""
    import cost_guard
    parts = []
    for part in gen:
        parts.append(part["message"]["content"])
        yield part
    cost_guard.record(cost_guard.estimate_tokens(messages, "".join(parts)))
