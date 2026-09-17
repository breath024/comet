# -*- coding: utf-8 -*-
"""COMET 형체 — 말할 때 화면에 뜨는 자비스풍 HUD (PySide6, per-pixel alpha).

voice.Mouth 가 재생 직전에 이 프로세스를 띄운다:
    python orb.py --env <포락선.json>
포락선 = 재생할 PCM 에서 뽑은 RMS 열. 그래서 입모양이 실제 소리와 맞는다.
띄우기만 하고 기다리지 않는다(toast.py 와 같은 패턴).

혼자 돌리면 가짜 파형으로 시연한다:  python orb.py --demo 30
닫는 법: Esc / 포락선 끝 / --sec 초 / stop.flag 파일.
"""
import argparse
import ctypes
import json
import math
import os
import random
import sys
import time

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QPolygonF, QRadialGradient, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "orb_stop.flag")
FPS = 30
FADE = 0.45                 # 뜨고 질 때 알파를 밀어 올리고 내리는 시간(초)
MARGIN = 26                 # 화면 구석에서 띄울 여백(px)

CY = (255, 150, 44)         # 주황 — 주색
AM = (255, 218, 148)        # 밝은 금 — 강조
TAU = math.pi * 2


def c(rgb, a):
    q = QColor(*rgb)
    q.setAlphaF(max(0.0, min(1.0, a)))
    return q


def build_traces(count, r0, r1, step, sweep, seed):
    """원형 배선을 미리 만들어 둔다(매 프레임 만들면 모양이 떨린다).
    한 가닥 = [아크 → 방사형 점프 → 아크 → …] 를 (각도, 반지름) 표본점으로 펼친 것.
    바깥쪽은 반지름이 크니 같은 호 길이라도 각도를 작게 줘야 비슷해 보인다."""
    rng = random.Random(seed)
    traces = []
    for i in range(count):
        ang = i / count * TAU + rng.uniform(-0.04, 0.04)
        rad = r0
        pts = [(ang, rad)]
        for _ in range(rng.randint(3, 6)):
            if rad > r1 - 0.015:
                break
            sw = rng.choice((-1, 1)) * rng.uniform(*sweep)
            steps = max(2, int(abs(sw) / 0.045))
            for k in range(1, steps + 1):          # 아크 구간
                pts.append((ang + sw * k / steps, rad))
            ang += sw
            rad = min(r1, rad + rng.uniform(*step))
            pts.append((ang, rad))                 # 방사형 점프
        traces.append({"pts": pts, "dim": rng.uniform(0.55, 1.0)})
    return traces


def _sector_circuit(rng):
    """고리꼴 조각 하나 안에 들어갈 배선. 좌표는 0~1 비율로 두고 그릴 때 실제 크기로 편다."""
    lv_n = rng.randint(3, 5)
    levels = []
    for k in range(lv_n):
        segs = []
        for _ in range(rng.randint(1, 2)):
            a0 = rng.uniform(0.07, 0.52)
            segs.append((a0, min(0.93, a0 + rng.uniform(0.24, 0.44))))
        levels.append({"rf": (k + 1) / (lv_n + 1), "segs": segs})
    jogs = []
    for k in range(lv_n - 1):                      # 층 사이를 잇는 방사형 점프
        for _ in range(rng.randint(1, 2)):
            jogs.append({"af": rng.uniform(0.14, 0.86),
                         "r0": levels[k]["rf"], "r1": levels[k + 1]["rf"]})
    return {"levels": levels, "jogs": jogs}


def build_sectors(seed=23):
    """중앙에서 바깥으로 뻗는 '줄기' 몇 개. 줄기마다 층(반지름 띠)별로 고리꼴 조각 뭉치를 둔다.
    줄기가 통째로 돌기 때문에 층이 달라도 한 줄기로 뻗어 나가는 게 읽힌다."""
    LAYERS = [(0.150, 0.208), (0.228, 0.300), (0.318, 0.398),
              (0.498, 0.582), (0.628, 0.730), (0.772, 0.846)]
    rng = random.Random(seed)
    rays = []
    for i in range(6):
        base = i / 6 * TAU + rng.uniform(-0.12, 0.12)
        spin = rng.choice((-1, 1)) * rng.uniform(0.020, 0.065)
        blocks = []
        for r0, r1 in LAYERS:
            if rng.random() < 0.26:               # 층을 군데군데 비운다
                continue
            n = rng.randint(1, 2)                 # 잘게 쪼개지 않는다
            blocks.append({"r0": r0, "r1": r1, "n": n,
                           "span": rng.uniform(0.34, 0.62),  # 뭉치 전체 각폭(rad)
                           # 층마다 제 속도·제 방향으로 돈다. 한 줄기로 묶어 돌리면
                           # 통째로 도는 게 눈에 띄어서 오히려 심심해진다.
                           "spin": spin * rng.uniform(0.30, 2.4) * rng.choice((-1, 1)),
                           "off": rng.uniform(-0.85, 0.85),
                           "pat": [_sector_circuit(rng) for _ in range(n)]})
        rays.append({"base": base, "spin": spin, "blocks": blocks})
    return rays


class Hud:
    def __init__(self):
        self.tilt = 0.34            # 0=정면. 이게 링을 타원으로 만든다
        self.yaw = 0.0
        self.labels = [f"{random.randint(0, 255):02X}" for _ in range(12)]
        self.traces = build_traces(20, 0.118, 0.400, (0.045, 0.085),
                                   (0.13, 0.46), seed=7)
        self.outer = build_traces(26, 0.500, 0.840, (0.040, 0.075),
                                  (0.07, 0.24), seed=11)
        self.rays = build_sectors()

    def update(self, t):
        self.tilt = 0.30 + math.sin(t * 0.21) * 0.13      # 천천히 기울기가 흔들린다
        self.yaw = math.sin(t * 0.13) * 0.16

    # 기울어진 원반 평면의 (u, v) → 화면 좌표
    def pp(self, u, v):
        y = v * math.cos(self.tilt)
        cw, sw = math.cos(self.yaw), math.sin(self.yaw)
        return QPointF(u * cw - y * sw, u * sw + y * cw)

    def pt(self, ang, rad):
        return self.pp(math.cos(ang) * rad, math.sin(ang) * rad)

    def ring_rect(self, rad, ct):
        return QRectF(-rad, -rad * ct, rad * 2, rad * ct * 2)

    def draw(self, p, t, R, amp):
        ct = math.cos(self.tilt)
        pen = QPen()
        pen.setCapStyle(Qt.RoundCap)

        def stroke(rgb, a, w):
            pen.setColor(c(rgb, a))
            pen.setWidthF(w)
            p.setPen(pen)

        def ring(rad, rgb, a, w, start=None, span=None):
            stroke(rgb, a, w)
            p.setBrush(Qt.NoBrush)
            rect = self.ring_rect(rad, ct)
            if start is None:
                p.drawEllipse(rect)
            else:
                p.drawArc(rect, int(start * 16), int(span * 16))

        def dot(q, rgb, a, rr):
            gg = QRadialGradient(q, rr)
            gg.setColorAt(0.0, c(rgb, a))
            gg.setColorAt(0.45, c(rgb, a * 0.75))
            gg.setColorAt(1.0, c(rgb, 0.0))
            p.setPen(Qt.NoPen)
            p.setBrush(gg)
            p.drawEllipse(q, rr, rr)

        def wires(group, a0, a1, w, via, pad):
            """배선 한 벌을 그린다 — 선 + 꺾임점 비아 + 끝단 패드."""
            for tr in group:
                pts = tr["pts"]
                stroke(CY, a0 + tr["dim"] * a1, max(1.0, R * w))
                p.setBrush(Qt.NoBrush)
                p.drawPolyline(QPolygonF([self.pt(a, R * r) for a, r in pts]))
                for k in range(1, len(pts) - 1):
                    if abs(pts[k + 1][1] - pts[k][1]) > 1e-6:   # 반지름이 바뀌는 지점
                        dot(self.pt(pts[k][0], R * pts[k][1]), CY, 0.45,
                            max(1.3, R * via))
                dot(self.pt(pts[-1][0], R * pts[-1][1]), AM, 0.55, max(1.6, R * pad))

        # ── 1. 글로우 바닥 ────────────────────────────────────────
        g = QRadialGradient(QPointF(0, 0), R * 1.05)
        g.setColorAt(0.0, c(CY, 0.20 + amp * 0.16))
        g.setColorAt(0.55, c(CY, 0.07 + amp * 0.06))
        g.setColorAt(1.0, c(CY, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.save()
        p.scale(1.0, max(ct, 0.25))
        p.drawEllipse(QPointF(0, 0), R * 1.05, R * 1.05)
        p.restore()

        # ── 2. 바깥 테두리 + 미세 눈금 192개 ──────────────────────
        ring(R, CY, 0.30, 1.2)
        ring(R * 0.965, CY, 0.16, 1.0)
        for i in range(192):
            ang = i / 192 * TAU + t * 0.04
            major = i % 8 == 0
            ln = R * (0.030 if major else 0.014)
            stroke(CY, 0.46 if major else 0.20, 1.4 if major else 0.9)
            p.drawLine(self.pt(ang, R * 0.965 - ln), self.pt(ang, R * 0.965))

        # ── 3. 끊긴 두꺼운 링 — 6토막, 천천히 돈다 ────────────────
        for k in range(6):
            base = math.degrees(-t * 0.22) + k * 60
            ring(R * 0.915, CY, 0.55, max(1.5, R * 0.011), base + 6, 48)

        # ── 4. 잔 파선 링 — 반대로 돈다 ───────────────────────────
        for i in range(120):
            ang = i / 120 * TAU - t * 0.10
            stroke(CY, 0.22, 1.0)
            p.drawLine(self.pt(ang, R * 0.862), self.pt(ang + 0.018, R * 0.862))

        # ── 4-B. 바깥 회로층 — 얇고 어둡게 깔아 배경 배선처럼
        wires(self.outer, 0.13, 0.13, 0.0042, 0.0050, 0.0070)

        # ── 4-C. 고리꼴 뭉치 — 중앙에서 뻗어 나가는 줄기, 층마다 한 덩이씩
        def sector(a0, a1, r0, r1):
            steps = max(3, int(abs(a1 - a0) / 0.045))
            path = QPainterPath()
            out = [self.pt(a0 + (a1 - a0) * k / steps, R * r1) for k in range(steps + 1)]
            inn = [self.pt(a1 + (a0 - a1) * k / steps, R * r0) for k in range(steps + 1)]
            path.moveTo(out[0])
            for q in out[1:] + inn:
                path.lineTo(q)
            path.closeSubpath()
            return path

        for ray in self.rays:
            for b in ray["blocks"]:
                n, span = b["n"], b["span"]
                gap = span * 0.14 / n
                wdt = (span - gap * (n - 1)) / n
                start = ray["base"] + b["off"] + b["spin"] * t - span / 2
                for k in range(n):
                    a0 = start + k * (wdt + gap)
                    a1 = a0 + wdt
                    pth = sector(a0, a1, b["r0"], b["r1"])
                    p.setPen(Qt.NoPen)
                    p.setBrush(c(CY, 0.10 + amp * 0.07))
                    p.drawPath(pth)
                    stroke(AM, 0.40, max(1.0, R * 0.0045))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(pth)

                    # 조각 안쪽 배선
                    pat = b["pat"][k]
                    dr = b["r1"] - b["r0"]
                    stroke(AM, 0.30, max(0.9, R * 0.0035))
                    for lv in pat["levels"]:
                        rr = b["r0"] + dr * lv["rf"]
                        for f0, f1 in lv["segs"]:
                            aa0, aa1 = a0 + wdt * f0, a0 + wdt * f1
                            steps = max(2, int(abs(aa1 - aa0) / 0.035))
                            p.drawPolyline(QPolygonF(
                                [self.pt(aa0 + (aa1 - aa0) * j / steps, R * rr)
                                 for j in range(steps + 1)]))
                    for jg in pat["jogs"]:
                        aa = a0 + wdt * jg["af"]
                        q0 = self.pt(aa, R * (b["r0"] + dr * jg["r0"]))
                        q1 = self.pt(aa, R * (b["r0"] + dr * jg["r1"]))
                        stroke(AM, 0.30, max(0.9, R * 0.0035))
                        p.drawLine(q0, q1)
                        dot(q0, AM, 0.45, max(1.2, R * 0.0045))
                        dot(q1, AM, 0.45, max(1.2, R * 0.0045))

        # ── 5. 16진수 라벨 (계기반 느낌) ──────────────────────────
        p.setFont(QFont("Consolas", max(7, int(R * 0.030))))
        for i, s in enumerate(self.labels):
            ang = i / len(self.labels) * TAU + t * 0.06
            q = self.pt(ang, R * 0.80)
            p.setPen(c(CY, 0.34))
            p.drawText(QRectF(q.x() - 18, q.y() - 8, 36, 16), Qt.AlignCenter, s)

        # ── 6. 큰 호 3겹 — 서로 다른 속도·방향 ────────────────────
        for rad, dirn, spd, span in ((0.735, 1, 0.34, 92), (0.680, -1, 0.52, 64),
                                     (0.625, 1, 0.26, 128)):
            for k in range(2):
                base = math.degrees(t * spd * dirn) + k * 180
                ring(R * rad, CY, 0.62, max(1.6, R * 0.014), base, span)

        # ── 7. 바퀴살 24개 ────────────────────────────────────────
        for i in range(24):
            ang = i / 24 * TAU - t * 0.05
            stroke(CY, 0.15, 1.0)
            p.drawLine(self.pt(ang, R * 0.50), self.pt(ang, R * 0.585))

        # ── 8. 소리 반응 막대 — 얘가 '말하는 중'을 보여준다 ───────
        bars = 144
        for i in range(bars):
            ang = i / bars * TAU + t * 0.02
            band = (abs(math.sin(i * 0.37 + t * 5.2)) * 0.6
                    + abs(math.sin(i * 1.13 - t * 3.1)) * 0.4)
            ln = R * (0.012 + amp * band * 0.135)
            stroke(CY, 0.25 + amp * band * 0.6, max(1.0, R * 0.0055))
            p.drawLine(self.pt(ang, R * 0.435), self.pt(ang, R * 0.435 + ln))
        ring(R * 0.425, CY, 0.28, 1.1)

        # ══ 안쪽 = 원형 회로 ══════════════════════════════════════
        # ── 9. 버스 링 — 곳곳이 끊긴 동심 배선
        for rad, segs, gap, spd in ((0.200, 5, 18, 0.07), (0.285, 7, 14, -0.05),
                                    (0.365, 4, 26, 0.09)):
            for k in range(segs):
                base = math.degrees(t * spd) + k * (360 / segs)
                ring(R * rad, CY, 0.30, max(1.0, R * 0.006),
                     base + gap / 2, 360 / segs - gap)

        # ── 10. 배선 가닥 — 아크와 방사형 점프가 번갈아 이어진다
        wires(self.traces, 0.26, 0.22, 0.0065, 0.0075, 0.010)

        # ── 11. 가운데 다이 — 동심 링 + 방사형 핀
        for k in range(5):
            ring(R * (0.050 + k * 0.014), CY, 0.34 - k * 0.03, max(0.9, R * 0.0045))
        for i in range(32):
            ang = i / 32 * TAU + t * 0.08
            stroke(CY, 0.40, max(0.9, R * 0.005))
            p.drawLine(self.pt(ang, R * 0.106), self.pt(ang, R * 0.118))
        stroke(AM, 0.50, max(1.2, R * 0.007))
        p.setBrush(Qt.NoBrush)
        tri = [self.pt(k / 3 * TAU - t * 0.46, R * 0.155) for k in range(3)]
        for k in range(3):
            p.drawLine(tri[k], tri[(k + 1) % 3])

        # ── 13. 코어 — 작게
        cr = R * 0.042 + amp * R * 0.018
        core = QRadialGradient(QPointF(0, 0), cr * 2.2)
        core.setColorAt(0.0, c((255, 255, 255), 0.95))
        core.setColorAt(0.30, c((255, 196, 110), 0.90))
        core.setColorAt(0.55, c(CY, 0.85 + amp * 0.15))
        core.setColorAt(1.0, c(CY, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(core)
        p.save()
        p.scale(1.0, max(ct, 0.3))
        p.drawEllipse(QPointF(0, 0), cr * 2.2, cr * 2.2)
        p.restore()


class Overlay(QWidget):
    def __init__(self, env, hop, sec, size_frac=0.19):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 오른쪽 아래 구석에 작게. availableGeometry 라서 작업표시줄을 피한다.
        scr = QGuiApplication.primaryScreen().availableGeometry()
        side = int(min(scr.width(), scr.height()) * size_frac)
        self.setGeometry(scr.right() - side - MARGIN, scr.bottom() - side - MARGIN,
                         side, side)
        self.R = side * 0.44          # 글로우가 1.05R 까지 번지므로 여유를 둔다
        self.hud = Hud()
        self.env, self.hop = env, hop
        self.dur = len(env) * hop if env else sec
        self.sec = sec
        self.amp = 0.0
        self._target, self._next = 0.0, 0.0
        self.t0 = time.time()
        self.frames = 0
        self.ready_cb = None
        tm = QTimer(self)
        tm.timeout.connect(self.tick)
        tm.start(int(1000 / FPS))

    def level(self, t):
        """소리 세기. 포락선이 있으면 그걸 읽고, 없으면 흉내낸다."""
        if self.env:
            i = int(t / self.hop)
            raw = self.env[i] if 0 <= i < len(self.env) else 0.0
        else:
            if t > self._next:
                talking = random.random() > 0.18
                self._target = random.uniform(0.35, 1.0) if talking else 0.02
                self._next = t + (random.uniform(0.07, 0.19) if talking
                                  else random.uniform(0.18, 0.44))
            raw = self._target
        self.amp += (raw - self.amp) * 0.30
        return self.amp

    def fade(self, t):
        """뜰 때/질 때 통째로 투명도를 민다 — 툭 나타나고 툭 사라지지 않게."""
        if t < FADE:
            return t / FADE
        left = self.dur + FADE - t
        return max(0.0, min(1.0, left / FADE))

    def tick(self):
        t = time.time() - self.t0
        self.level(t)
        self.update()
        if (t > self.dur + FADE or t > self.sec or os.path.exists(STOP)
                or ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000):
            QApplication.quit()

    def paintEvent(self, _):
        self.frames += 1
        t = time.time() - self.t0
        self.hud.update(t)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setOpacity(self.fade(t))
        p.translate(self.width() / 2, self.height() / 2)
        self.hud.draw(p, t, self.R, self.amp)
        if self.frames == 2 and self.ready_cb:      # 첫 그림이 실제로 올라온 뒤
            self.ready_cb()
            self.ready_cb = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="RMS 포락선 json (voice.py 가 만든다)")
    ap.add_argument("--ready", help="첫 프레임을 그린 뒤 만들 파일 — 소리 재생 신호")
    ap.add_argument("--sec", type=float, default=60.0, help="안전 상한")
    ap.add_argument("--demo", type=float, default=0.0, help="가짜 파형으로 N초 시연")
    ap.add_argument("--size", type=float, default=0.19,
                    help="화면 짧은 변 대비 한 변 비율 (0.19 = 작게 구석에)")
    a = ap.parse_args()

    env, hop = [], 0.02
    if a.env and os.path.exists(a.env):
        with open(a.env, encoding="utf-8") as f:
            d = json.load(f)
        env, hop = d.get("env") or [], float(d.get("hop") or 0.02)

    if os.path.exists(STOP):
        os.remove(STOP)
    app = QApplication(sys.argv)
    w = Overlay(env, hop, a.demo or a.sec, a.size)
    if a.ready:
        w.ready_cb = lambda: open(a.ready, "w").close()
    w.show()
    code = app.exec()
    if a.env:                                       # 뒷정리 — 폴더째 지운다
        import shutil
        shutil.rmtree(os.path.dirname(a.env), ignore_errors=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
