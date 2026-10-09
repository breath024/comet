# -*- coding: utf-8 -*-
"""원격(텔레그램)에서 온 말을 처리하는 동안 켜지는 표시 (2026-10-10 · RawCut 보안 점검 T-18).

텔레그램 대화방은 호윤 계정 하나에만 기대는 문이다 — 계정이 털리면 그 방으로 PC 를 조종할 수 있었다
(「코딩 …」 한 줄 = 자동승인 셸 · 위임 · 파일 쓰기). 그래서 텔레그램에서 온 말을 처리하는 동안은
코딩 · 셸 · 위임 · 파일 쓰기를 막고 대화 · 조회만 한다. 그런 일은 PC 앞(콘솔 · 폰 웹)에서.
"""
import threading
from contextlib import contextmanager

_local = threading.local()

REFUSE = "그건 텔레그램에선 안 해 — 코딩 · 셸 · 위임 · 파일 고치기는 PC 앞(콘솔이나 폰 웹)에서 시켜 줘."


def active() -> bool:
    return bool(getattr(_local, "on", False))


@contextmanager
def on():
    prev = active()
    _local.on = True
    try:
        yield
    finally:
        _local.on = prev
