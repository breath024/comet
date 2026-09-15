# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 화면 알림 — 화면 위 가운데에 내려오는 알약형 배너 (애플 알림 느낌)
#
#  별도 프로세스로 띄운다: python toast.py --tag AI --title "..." --body "..." --level 3
#   · tkinter 는 자기 스레드에서 mainloop 를 돌려야 해서, 데몬 스레드 안에서 띄우면
#     꼬인다. 프로세스를 따로 띄우면 데몬은 안 기다리고 바로 돌아간다.
#   · 포커스를 뺏지 않는다(WS_EX_NOACTIVATE) — 타이핑·게임 중에 입력이 안 끊기게.
#   · 클릭하면 바로 닫힌다. 아니면 --sec 초 뒤 위로 올라가며 사라진다.
# ═══════════════════════════════════════════════════════════════
import sys
import argparse
import ctypes
import tkinter as tk
import tkinter.font as tkfont

W, H_MIN = 460, 76
RADIUS = 26
KEY = "#010203"                     # 투명 처리할 색(모서리 바깥)
BG, FG, SUB = "#1c1c1e", "#f5f5f7", "#a1a1a6"
ACCENT = {3: "#ff453a", 2: "#ff9f0a", 1: "#8e8e93"}   # 굵직 / 볼만 / 잔잔


def _font(root, size, weight="normal"):
    fams = set(tkfont.families(root))
    for f in ("Pretendard", "Pretendard Variable", "Apple SD Gothic Neo", "Malgun Gothic"):
        if f in fams:
            return (f, size, weight)
    return ("Segoe UI", size, weight)


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _no_activate(root):
    try:
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                            st | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def show(tag, title, body="", level=2, sec=8.0):
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", KEY)
    except tk.TclError:
        pass
    root.configure(bg=KEY)

    f_tag, f_title, f_body = _font(root, 9, "bold"), _font(root, 12, "bold"), _font(root, 10)
    wrap = W - 64

    # 높이 = 본문 줄 수에 맞춘다
    probe = tk.Label(root, text=body, font=f_body, wraplength=wrap, justify="left")
    probe.update_idletasks()
    body_h = probe.winfo_reqheight() if body else 0
    probe.destroy()
    h = max(H_MIN, 52 + body_h + 14)

    cv = tk.Canvas(root, width=W, height=h, bg=KEY, highlightthickness=0, bd=0)
    cv.pack()
    _round_rect(cv, 1, 1, W - 1, h - 1, RADIUS, fill=BG, outline="#2c2c2e")
    acc = ACCENT.get(level, ACCENT[2])
    cv.create_oval(22, 20, 32, 30, fill=acc, outline="")
    cv.create_text(40, 25, text=tag, fill=SUB, font=f_tag, anchor="w")
    cv.create_text(W - 22, 25, text="지금", fill=SUB, font=f_tag, anchor="e")
    cv.create_text(22, 46, text=title, fill=FG, font=f_title, anchor="w",
                   width=wrap + 20)
    if body:
        cv.create_text(22, 62, text=body, fill=SUB, font=f_body, anchor="nw", width=wrap + 20)

    sw = root.winfo_screenwidth()
    x = (sw - W) // 2
    y_show, y_hide = 14, -h - 10
    root.geometry(f"{W}x{h}+{x}+{y_hide}")
    root.update_idletasks()
    _no_activate(root)

    state = {"closing": False}

    def slide(y_from, y_to, steps, done=None, i=0):
        t = i / steps
        ease = 1 - (1 - t) ** 3
        y = int(y_from + (y_to - y_from) * ease)
        root.geometry(f"{W}x{h}+{x}+{y}")
        if i < steps:
            root.after(12, slide, y_from, y_to, steps, done, i + 1)
        elif done:
            done()

    def close(_=None):
        if state["closing"]:
            return
        state["closing"] = True
        slide(y_show, y_hide, 16, root.destroy)

    cv.bind("<Button-1>", close)
    slide(y_hide, y_show, 22)
    root.after(int(sec * 1000), close)
    root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="COMET")
    ap.add_argument("--title", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--sec", type=float, default=8.0)
    a = ap.parse_args()
    show(a.tag, a.title, a.body, a.level, a.sec)
