# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 말걸기 창 — Alt 두 번 누르면 화면 위 가운데에 내려온다
#
#  말로 해도 되고, 그냥 타이핑해도 된다.
#   · 키를 누르는 순간 마이크는 멈추고 입력 모드 (dir/typing 파일로 알림)
#   · 타이핑을 멈추고 3초 지나면 알아서 보낸다. Enter 는 바로. Esc 는 닫기.
#   · 진행 상황은 데몬이 dir/state.json 에 쓰고, 이 창은 그걸 읽어서 보여준다
#     (toast.py·orb.py 와 같은 '파일로 주고받기' — 데몬 스레드에서 tkinter 를 안 돌리려고)
#
#  python ask_box.py --dir <임시폴더>
# ═══════════════════════════════════════════════════════════════
import os
import sys
import json
import argparse
import ctypes
import tkinter as tk

from toast import _font, _round_rect, KEY, BG, FG, SUB

W = 560
H_IN = 118                      # 입력줄까지
IDLE_SEND_MS = 3000             # 타이핑 멈추고 이만큼 지나면 보냄
ACC_LISTEN, ACC_TYPE, ACC_THINK, ACC_DONE = "#30d158", "#0a84ff", "#ff9f0a", "#8e8e93"


def _write(d, name, text=""):
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(d, name))


def _force_foreground(root):
    """백그라운드 프로세스가 띄운 창은 윈도우가 포커스를 안 준다 — 스레드 입력을 잠깐 붙여서 가져온다."""
    try:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = u32.GetParent(root.winfo_id()) or root.winfo_id()
        fg = u32.GetForegroundWindow()
        fg_tid = u32.GetWindowThreadProcessId(fg, None)
        me = k32.GetCurrentThreadId()
        u32.AttachThreadInput(me, fg_tid, True)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(me, fg_tid, False)
    except Exception:
        pass


def main(d):
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", KEY)
    except tk.TclError:
        pass
    root.configure(bg=KEY)

    f_tag, f_in, f_body = _font(root, 9, "bold"), _font(root, 13), _font(root, 11)
    cv = tk.Canvas(root, width=W, height=H_IN, bg=KEY, highlightthickness=0, bd=0)
    cv.pack()

    st = {"h": H_IN, "typing": False, "sent": False, "idle": None, "closing": False,
          "stage": "", "close_at": None}

    entry = tk.Entry(root, font=f_in, bg="#2c2c2e", fg=FG, insertbackground=FG,
                     relief="flat", bd=0, highlightthickness=0)
    reply = tk.Label(root, text="", font=f_body, bg=BG, fg=FG, justify="left",
                     anchor="nw", wraplength=W - 48)

    def draw(accent, status):
        cv.delete("all")
        _round_rect(cv, 1, 1, W - 1, st["h"] - 1, 26, fill=BG, outline="#2c2c2e")
        cv.create_oval(22, 20, 32, 30, fill=accent, outline="")
        cv.create_text(40, 25, text="COMET", fill=SUB, font=f_tag, anchor="w")
        cv.create_text(W - 22, 25, text=status, fill=SUB, font=f_tag, anchor="e")
        _round_rect(cv, 18, 46, W - 18, 88, 14, fill="#2c2c2e", outline="")
        cv.create_text(W - 22, H_IN - 14, text="Enter 보내기 · Esc 닫기 · 멈추면 3초 뒤 자동",
                       fill="#636366", font=f_tag, anchor="e")
        cv.create_window(30, 67, window=entry, anchor="w", width=W - 60, height=30)

    sw = root.winfo_screenwidth()
    x, y = (sw - W) // 2, 14
    root.geometry(f"{W}x{H_IN}+{x}+{y}")
    draw(ACC_LISTEN, "듣는 중… 그냥 타이핑해도 돼")

    def resize(h):
        st["h"] = h
        cv.config(height=h)
        root.geometry(f"{W}x{h}+{x}+{y}")

    def close(_=None):
        if st["closing"]:
            return
        st["closing"] = True
        try:
            _write(d, "closed")
        except Exception:
            pass
        root.destroy()

    def send(_=None):
        text = entry.get().strip()
        if st["sent"] or not text:
            return "break"
        st["sent"] = True
        _write(d, "typed.txt", text)
        entry.config(state="readonly", readonlybackground="#2c2c2e")
        draw(ACC_THINK, "생각 중…")
        return "break"

    def on_key(e):
        if e.keysym in ("Escape", "Return", "KP_Enter"):
            return
        if not st["typing"]:
            st["typing"] = True
            _write(d, "typing")
            draw(ACC_TYPE, "입력 중")
        if st["idle"]:
            root.after_cancel(st["idle"])
        st["idle"] = root.after(IDLE_SEND_MS, send)

    entry.bind("<Key>", on_key)
    entry.bind("<Return>", send)
    entry.bind("<KP_Enter>", send)
    root.bind("<Escape>", close)

    def poll():
        if st["closing"]:
            return
        try:
            with open(os.path.join(d, "state.json"), encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        stage = s.get("stage", "")
        if stage != st["stage"]:
            st["stage"] = stage
            text = s.get("text", "")
            if stage == "heard":
                entry.delete(0, "end")
                entry.insert(0, text)
                entry.config(state="readonly", readonlybackground="#2c2c2e")
                draw(ACC_THINK, "생각 중…")
            elif stage == "loading":
                draw(ACC_DONE, "귀 준비 중… 타이핑은 바로 돼")
            elif stage == "listening" and not st["typing"]:
                draw(ACC_LISTEN, "듣는 중… 그냥 타이핑해도 돼")
            elif stage == "none" and not st["typing"]:
                draw(ACC_DONE, "못 들었어")
                st["close_at"] = 1500
            elif stage == "reply":
                reply.config(text=text[:600])
                reply.update_idletasks()
                rh = min(reply.winfo_reqheight(), 260)
                resize(H_IN + rh + 16)
                draw(ACC_DONE, "대답")
                cv.create_window(24, H_IN - 4, window=reply, anchor="nw", width=W - 48, height=rh)
            elif stage == "done":
                st["close_at"] = 6000
            elif stage == "error":
                draw("#ff453a", "오류 — " + text[:40])
                st["close_at"] = 4000
        if st["close_at"] is not None:
            root.after(st["close_at"], close)
            st["close_at"] = None
            return
        root.after(100, poll)

    root.update_idletasks()
    _force_foreground(root)
    entry.focus_force()
    _write(d, "ready")
    root.after(100, poll)
    root.after(300000, close)          # 안전장치 — 5분 넘게 떠 있지 않게
    root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    main(ap.parse_args().dir)
