# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 비용 사고방지 프로토콜
#
#  API(유료) 호출이 무분별하게/무한히 일어나 돈이 새는 걸 막는다.
#   · 과제당 API 호출 상한 (한 작업이 폭주 못 하게)
#   · 하루 API 호출 수 / 토큰 상한 (날짜별 누적, 넘으면 차단)
#   · 호출 전 검문(before_api_call) → 넘으면 거부, 기록(record)으로 누적
#  무한루프(같은 동작 반복) 자체는 agent.py 루프에서 차단한다.
#
#  설정은 coder_config.json 에서 덮어쓸 수 있다(아래 DEFAULTS 키).
#  사용량은 comet_usage.json 에 날짜별로 쌓인다(직접 열어 확인 가능).
# ═══════════════════════════════════════════════════════════════
import os
import json
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "coder_config.json")
USAGE_FILE = os.path.join(HERE, "comet_usage.json")

DEFAULTS = {
    "api_calls_per_task": 30,        # 한 작업에서 API 모델 호출 최대 횟수
    "api_calls_per_day": 300,        # 하루 API 호출 총량
    "api_tokens_per_day": 1_500_000, # 하루 토큰 상한(대략). DeepSeek 기준 수백원대
}


def _cfg():
    c = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                c.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except Exception:
            pass
    return c


_task_calls = 0          # 현재 작업에서의 API 호출 수(메모리)


def reset_task():
    """새 작업 시작 시 과제별 카운터 초기화 (agent.run 이 호출)."""
    global _task_calls
    _task_calls = 0


def _load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _today(usage):
    key = date.today().isoformat()
    return key, usage.get(key, {"calls": 0, "tokens": 0})


def estimate_tokens(messages, response=""):
    chars = sum(len(m.get("content", "")) for m in messages) + len(response or "")
    return max(1, chars // 4)        # 대략 4자 ≈ 1토큰


def before_api_call(est_tokens):
    """API 호출 직전 검문. (허용여부, 사유) 반환."""
    cfg = _cfg()
    if _task_calls + 1 > cfg["api_calls_per_task"]:
        return False, (f"이 작업의 API 호출이 {cfg['api_calls_per_task']}회 상한에 도달 — "
                       "폭주 방지로 중단. (coder_config.json 의 api_calls_per_task 로 조정)")
    usage = _load_usage()
    _, today = _today(usage)
    if today["calls"] + 1 > cfg["api_calls_per_day"]:
        return False, f"오늘 API 호출이 하루 상한({cfg['api_calls_per_day']}회)에 도달 — 차단."
    if today["tokens"] + est_tokens > cfg["api_tokens_per_day"]:
        return False, (f"오늘 API 토큰이 하루 상한({cfg['api_tokens_per_day']:,})에 도달 — 차단. "
                       "(돈 새는 것 방지. 내일 풀리거나 coder_config.json 에서 상향)")
    return True, ""


def record(tokens):
    """실제 호출 후 사용량 누적."""
    global _task_calls
    _task_calls += 1
    usage = _load_usage()
    key, today = _today(usage)
    today["calls"] += 1
    today["tokens"] += int(tokens)
    usage[key] = today
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(usage, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def today_summary():
    _, today = _today(_load_usage())
    cfg = _cfg()
    return (f"오늘 API: {today['calls']}/{cfg['api_calls_per_day']}회 · "
            f"{today['tokens']:,}/{cfg['api_tokens_per_day']:,} 토큰")


class Blocked(RuntimeError):
    """비용 가드가 호출을 막을 때."""
    pass
