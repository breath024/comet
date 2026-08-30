# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 진행 트레이스 (trace) — "진짜 AI처럼" 검색·수집·추론을 단계로 보여줌
#   콘솔 전용(데몬/폰은 최종 답만 받음). 의존성 0(표준 ctypes/time만).
#   디자인 원칙: 조잡한 디버그 줄 금지 → 단계(◆)·세부(·)·결과 마크의 일관된 시각언어.
#   색은 Windows VT 켜질 때만(안 되면 자동으로 무색 평문 — 깨지지 않음).
# ═══════════════════════════════════════════════════════════════
import os
import sys
import time

_COLOR = None  # None=미초기화, True/False


def _enable():
    global _COLOR
    if _COLOR is not None:
        return _COLOR
    ok = False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            import ctypes as C
            mode = C.c_uint32()
            if k.GetConsoleMode(h, C.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                ok = True
        except Exception:
            ok = False
    else:
        ok = sys.stdout.isatty()
    _COLOR = ok
    return ok


def _c(code, s):
    return f"\x1b[{code}m{s}\x1b[0m" if _enable() else s


def dim(s):    return _c("38;5;245", s)   # 회색 보조
def bold(s):   return _c("1", s)
def cyan(s):   return _c("38;5;44", s)    # 단계 아이콘 강조
def green(s):  return _c("38;5;78", s)
def amber(s):  return _c("38;5;215", s)
def red(s):    return _c("38;5;203", s)


# 국가 라벨 → 깃발(렌더 안 되면 이름이 남으니 안전)
FLAG = {"한국": "🇰🇷", "미국/영어권": "🇺🇸", "미국": "🇺🇸", "중국": "🇨🇳",
        "대만": "🇹🇼", "일본": "🇯🇵", "독일": "🇩🇪", "프랑스": "🇫🇷"}


class Trace:
    """단계형 진행 로그. enabled=False면 완전 침묵."""

    def __init__(self, enabled=True, title=None):
        self.enabled = enabled
        self.t0 = time.time()
        if enabled and title:
            print("\n" + bold(cyan("⟢ ")) + bold(title))

    def step(self, title, meta=""):
        if not self.enabled:
            return
        line = "  " + cyan("◆ ") + bold(title)
        if meta:
            line += "  " + dim(meta)
        print(line, flush=True)

    def item(self, text, flag=None):
        if not self.enabled:
            return
        head = (FLAG.get(flag, "") + " ") if flag else ""
        print("      " + dim("· ") + head + dim(text), flush=True)

    def ok(self, text):
        if self.enabled:
            print("      " + green("✓ ") + dim(text), flush=True)

    def warn(self, text):
        if self.enabled:
            print("      " + amber("⚠ ") + dim(text), flush=True)

    def think(self, text):
        """비판적 사고 단계임을 드러내는 마크(역발상·검증)."""
        if self.enabled:
            print("  " + amber("◆ ") + bold(text), flush=True)

    def done(self, label="완료"):
        if not self.enabled:
            return
        dt = time.time() - self.t0
        t = f"{dt:.0f}초" if dt < 60 else f"{int(dt // 60)}분 {int(dt % 60)}초"
        print("  " + green("✔ ") + bold(green(label)) + dim(f"   ·   {t}"), flush=True)
