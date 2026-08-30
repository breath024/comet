# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 모델 백엔드 (로컬 기본 · API 승급)
#
#  한 함수 chat() 로 두 백엔드를 같은 모양으로 부른다:
#   - 로컬(ollama):  키 없으면 무조건 이쪽. 월 0원, 인터넷 끊겨도 작동.
#   - API:           COMET_API_KEY 가 있으면 그 모델로 자동 승급(코딩 훨씬 좋음).
#                    DeepSeek / Qwen(DashScope) 등 OpenAI 호환 엔드포인트.
#
#  의존성: ollama(이미 씀) + urllib(표준). openai 패키지 불필요.
#  설정 파일: 같은 폴더 coder_config.json (없으면 기본값). 키는 환경변수 우선.
# ═══════════════════════════════════════════════════════════════
import os
import re
import json
import urllib.request
import urllib.error

import ollama

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "coder_config.json")

# ── 기본 설정 ────────────────────────────────────────────────────
#  local_model: 코딩용 로컬 모델. qwen3-coder:30b 권장(없으면 자동 폴백).
#  api_*      : 키가 있을 때만 사용. base_url 은 OpenAI 호환 /chat/completions.
DEFAULTS = {
    "backend":      "auto",                 # auto | local | api
    # 2026 기준 16GB VRAM 에이전트 코딩 최선. 없으면 폴백 순서대로.
    #  qwen3-coder:30b = MoE(30B/3.3B활성) 코딩 1위급·툴콜 강함(살짝 흘러도 빠름)
    #  devstral        = Mistral 에이전트 전용 24B·툴콜 형식 가장 안정(완전 적재)
    "local_model":  "qwen3-coder:30b",
    "local_fallbacks": ["devstral", "gemma4:26b",
                        "gemma4:31b", "gemma4:12b"],
    "keep_alive":   "10m",
    # 한 번 호출의 출력 토큰 상한(로컬=num_predict, API=max_tokens). 폭주 차단.
    #  · 로컬: 한 스텝이 만 토큰씩 토하며 29분 걸리는 사고 방지(시간).
    #  · API : 한 콜이 끝까지 토하고 다 과금되는 사고 방지(돈). ★ 파산 방지 핵심.
    #  에이전트 JSON 한 스텝은 보통 수백 토큰 → 2048은 넉넉. 큰 파일 쓰다 잘리면 상향.
    "max_output_tokens": 2048,
    # 입력 컨텍스트 상한(로컬 전용, ollama `num_ctx`).
    # ★안 주면 ollama 기본 4096으로 돈다. 그런데 `dev_tools.MAX_READ`가 20,000자라
    #   **read_file 한 번이 6,000토큰을 넘어 4096을 통째로 넘긴다.** 게다가 `agent.py`는
    #   히스토리를 안 자르고 계속 쌓는다(`messages = list(messages) + [...]`).
    # ⚠️ 넘치면 ollama가 **에러 없이 앞을 조용히 자른다.** 2026-08-18 실측: 같은 입력이
    #   num_ctx=2048에선 1,026토큰, 16384에선 8,194토큰으로 들어갔다. 경고 한 줄 없다.
    #   즉 4096이면 에이전트가 원래 지시와 먼저 읽은 파일을 소리 없이 잊는다 —
    #   증상은 "모델이 멍청하다"로 보이지 트렁케이션으로 안 보인다.
    # 값은 속도와 맞바꾼다. 2026-08-18 실측(5070 Ti 16GB, qwen3-coder:30b):
    #     4096  81.5 tok/s (24% CPU) | 16384 69.7 (29%) | 32768 57.8 (34%)
    #     65536 45.1 (42%)           | 131072 31.2 (54%)
    #   KV 캐시가 VRAM을 밀어내 CPU 몫이 늘어서 느려진다. **크게 잡을수록 좋은 게 아니다.**
    #   32768 = 속도 29% 내주고 조용한 절단을 막는 지점. 파일 여러 개 읽으면 이게 남는 장사다.
    "num_ctx": 32768,
    "api_base":     "https://api.deepseek.com",
    "api_model":    "deepseek-chat",
    "api_key_env":  "COMET_API_KEY",
    # 자동 승급(backend=auto 일 때): 로컬 먼저, 막히면 API 재시도
    "auto_escalate": True,
    "max_steps_local": 24,   # 5단계(조사·계획·구현·검증·비판) 흐름 + 검증반송 여유
    "max_steps_api":   14,
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


_CFG = load_config()


def _installed_models():
    try:
        data = ollama.list()
        out = []
        for m in data.get("models", []):
            out.append(m.get("model") or m.get("name") or "")
        return [x for x in out if x]
    except Exception:
        return []


def _resolve_local(cfg):
    """설정한 로컬 모델이 깔려있으면 그걸, 없으면 폴백 순서대로 고른다."""
    installed = _installed_models()
    want = cfg["local_model"]
    if any(want.split(":")[0] in m for m in installed):
        # 정확히 있거나(qwen3-coder:30b) 같은 계열이 있으면 want 사용
        if want in installed:
            return want
        for m in installed:
            if want.split(":")[0] in m:
                return m
    for fb in cfg["local_fallbacks"]:
        for m in installed:
            if fb.split(":")[0] in m and (fb in installed or True):
                if fb in installed:
                    return fb
                if fb.split(":")[0] in m:
                    return m
    return installed[0] if installed else want


def _api_key(cfg):
    return os.environ.get(cfg["api_key_env"], "").strip()


def active_backend():
    """지금 어느 백엔드로 도는지 (UI 표시용)."""
    cfg = _CFG
    if cfg["backend"] == "local":
        return "local", _resolve_local(cfg)
    if cfg["backend"] == "api":
        return "api", cfg["api_model"]
    # auto: 키 있으면 api, 없으면 local
    if _api_key(cfg):
        return "api", cfg["api_model"]
    return "local", _resolve_local(cfg)


# ═══════════════════════════════════════════════════════════════
#  API 호출 (OpenAI 호환 /chat/completions, urllib)
# ═══════════════════════════════════════════════════════════════
def _api_chat(messages, cfg, temperature, stream, on_token):
    key = _api_key(cfg)
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["api_model"],
        "messages": messages,
        "temperature": temperature,
        "stream": bool(stream),
        "max_tokens": cfg["max_output_tokens"],   # ★ 출력 상한 = 폭주 과금 차단
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    if not stream:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"] or ""
    # 스트리밍: SSE 라인 파싱
    chunks = []
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
            except Exception:
                delta = ""
            if delta:
                chunks.append(delta)
                if on_token:
                    on_token(delta)
    return "".join(chunks)


# ═══════════════════════════════════════════════════════════════
#  로컬 호출 (ollama)
# ═══════════════════════════════════════════════════════════════
def _local_chat(messages, cfg, model, temperature, stream, on_token):
    opts = {"temperature": temperature, "num_predict": cfg["max_output_tokens"],
            "num_ctx": cfg["num_ctx"]}
    if not stream:
        r = ollama.chat(model=model, messages=messages,
                        keep_alive=cfg["keep_alive"], options=opts)
        return r["message"].get("content", "") or ""
    chunks = []
    for part in ollama.chat(model=model, messages=messages, stream=True,
                            keep_alive=cfg["keep_alive"], options=opts):
        tok = part["message"]["content"]
        chunks.append(tok)
        if on_token:
            on_token(tok)
    return "".join(chunks)


# ═══════════════════════════════════════════════════════════════
#  공개 진입점
# ═══════════════════════════════════════════════════════════════
def chat(messages, temperature=0.2, stream=False, on_token=None, model=None, backend=None):
    """messages(OpenAI 형식) → 답변 텍스트. backend=None 이면 설정/키로 자동 선택,
       'local'/'api' 로 강제 가능(자동 승급 오케스트레이션용).
       API 경로는 cost_guard 검문을 통과해야만 호출된다(돈 사고방지)."""
    cfg = _CFG
    if backend is None:
        backend, chosen = active_backend()
    else:
        chosen = cfg["api_model"] if backend == "api" else _resolve_local(cfg)

    if backend == "api":
        import cost_guard
        # 사전 검문은 출력 상한까지 비관적으로 잡아 계산(최악 = 입력 + max_tokens).
        # 이래야 하루 토큰 천장이 폭주 한 콜까지 포함한 진짜 천장이 된다.
        est = cost_guard.estimate_tokens(messages) + cfg["max_output_tokens"]
        ok, why = cost_guard.before_api_call(est)
        if not ok:
            raise cost_guard.Blocked(why)
        try:
            out = _api_chat(messages, cfg, temperature, stream, on_token)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            raise RuntimeError(f"API 오류 {e.code}: {detail}") from e
        cost_guard.record(cost_guard.estimate_tokens(messages, out))
        return out
    # local
    use = model or chosen
    return _local_chat(messages, cfg, use, temperature, stream, on_token)


def strip_think(text):
    """qwen3 등 사고과정 <think>…</think> 누수 제거."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S)
    return text.strip()


def extract_json(text):
    """모델 답에서 첫 JSON 객체를 뽑는다(에이전트 도구 호출 파싱용).
       코드펜스/잡설에 둘러싸여 있어도 중괄호 균형으로 찾아낸다."""
    text = strip_think(text)
    # ```json … ``` 펜스 우선
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    # 중괄호 균형 스캔
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


if __name__ == "__main__":
    b, m = active_backend()
    print(f"백엔드: {b}  모델: {m}")
    print("설치된 ollama 모델:", _installed_models())
    print("테스트 호출…")
    print(chat([{"role": "user", "content": "한 줄로 자기소개해."}]))
