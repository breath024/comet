# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  로컬 전용 설정 (local_config.json)
#   개인 경로·계정처럼 저장소에 올리면 안 되는 값만 여기서 읽는다.
#   파일이 없으면 전부 기본값으로 동작한다 — 코드는 그대로 돈다.
#   채워 넣을 항목은 local_config.example.json 참고.
# ═══════════════════════════════════════════════════════════════
import os
import json

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_config.json")
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def get(key, default=None):
    v = _load().get(key)
    return default if v is None else v
