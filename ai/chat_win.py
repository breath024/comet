# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 대화창 — 돌고 있는 데몬에 붙어서 글자로 말 거는 창 (PySide6)
#
#   · 데몬(run_daemon.bat)의 POST /chat 을 그대로 쓴다 = COMET 인스턴스는 하나.
#     run.bat 콘솔처럼 두 번째 인스턴스를 띄우지 않으니 대화기록이 안 꼬인다.
#   · 열 때 comet_history.json 의 최근 대화를 먼저 그려준다(코멧이 기억하는 것과 같은 것).
#   · Enter 전송 / Shift+Enter 줄바꿈 / Esc 는 입력 비우기. 창은 계속 떠 있는다.
#   · 인증은 daemon_token.txt 를 읽어 X-Token 헤더로. (폰은 Tailscale 헤더, 여긴 토큰)
#
#   실행:  python chat_win.py     콘솔 없이:  run_chat.bat
#   · 대답은 소리로도 읽어준다(머리말 🔊 로 끄기) — 요청에 speak 를 실으면 데몬이
#     아야카 목소리로 읽는다. 글자가 먼저 오고 읽기는 뒤에서 돈다.
#   · 머리말 「소식 듣기」 = AI·경제 소식 알림 켜기/끄기(데몬 POST /news, '알림 켜/끔' 과 같은 스위치).
#     몇 초마다 데몬에 물어 상태를 맞춘다 — 콘솔에서 '알림 끔' 해도 버튼이 따라 바뀐다.
#   · 소식이 알려지면 같은 순간 여기에도 글로 남는다(왼쪽 색 띠 말풍선 — 띠 색 = 배너와 같은 강도 색).
#     창을 연 뒤에 온 것만. 대화 기록에는 안 들어간다(이유는 issues.py 의 FEED 머리말).
# ═══════════════════════════════════════════════════════════════
import json
import os
import sys
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy,
                               QTextEdit, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8765"
TOKEN_FILE = os.path.join(HERE, "daemon_token.txt")
HISTORY = os.path.join(HERE, "comet_history.json")
STATE = os.path.join(HERE, "chat_win_state.json")   # 창 크기·위치만
ICON = os.path.join(HERE, "web", "icon-192.png")

BACKLOG = 20          # 열 때 보여줄 지난 대화 수(메시지 개수)
TIMEOUT = 600         # 27b·분석이면 몇 분 걸린다 — 넉넉히
BUBBLE_MAX = 0.78     # 말풍선 최대 너비(창 대비)
NEWS_POLL_MS = 3000   # 소식 듣기 상태를 데몬에 묻는 간격

BG, CARD, LINE = "#0e0f12", "#1c1e24", "#23252b"
FG, SUB, ME = "#e8e8ea", "#6b6e76", "#2a6df4"
NEWS_ACCENT = {3: "#ff453a", 2: "#ff9f0a", 1: "#8e8e93"}   # 굵직 / 볼만함 / 잔잔 — toast.py 배너와 같은 색


def _token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _post(path, payload, timeout=TIMEOUT):
    """데몬에 POST. (성공여부, 본문) — 한글은 반드시 utf-8 로 인코딩해서 보낸다."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, headers={
        "Content-Type": "application/json", "X-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "[인증 실패] daemon_token.txt 를 못 읽었거나 데몬이 다른 토큰으로 떠 있다."
        return False, f"[데몬 오류 {e.code}] {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, f"[연결 안 됨] {e}  —  데몬(run_daemon.bat)이 떠 있는지 확인."


def _alive():
    try:
        with urllib.request.urlopen(BASE + "/ping", timeout=3) as r:
            return json.loads(r.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def _backlog():
    """대화기록 뒤쪽 몇 개. 코멧이 실제로 기억하는 것과 같은 파일을 읽는다(읽기만)."""
    try:
        with open(HISTORY, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        return []
    out = []
    for m in hist[-BACKLOG:]:
        role, text = m.get("role"), (m.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            out.append(("me" if role == "user" else "ai", text))
    return out


class Bridge(QObject):
    done = Signal(bool, object)
    news = Signal(bool, object, bool)     # (성공, /news 답, 버튼으로 바꾼 것인가)


class Bubble(QLabel):
    """말풍선.

    ★줄바꿈(wordWrap) 라벨을 그냥 세로 Preferred 로 두면 레이아웃이 sizeHint 밑으로
    눌러서 **긴 답의 아랫줄이 잘린다**(실측: 필요 213px 인데 153px 만 받음).
    세로를 Minimum + heightForWidth 로 두면 레이아웃이 '그때 그 너비에 필요한 높이'를
    직접 물어본다.

    ★★높이를 setMinimumHeight 로 직접 박지 말 것. '…' 대기 풍선은 폭이 50px 라
    그 폭으로 잰 높이(558px)를 바닥으로 박아버리면, 폭이 310px 로 넓어져도 바닥이
    안 풀려 **말풍선 아래가 텅 빈 채로 남는다**(2026-09-18 실제로 그렇게 났다).
    minimumHeight 가 박히면 heightForWidth 까지 그 값으로 끌려와서 검사도 속는다.
    """

    def __init__(self, text):
        super().__init__(text)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setFont(QFont("Malgun Gothic", 10))
        sp = QSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        sp.setHeightForWidth(True)              # 높이는 레이아웃이 그때 폭으로 물어본다
        self.setSizePolicy(sp)

    def setMaximumWidth(self, w):
        super().setMaximumWidth(w)
        self.updateGeometry()

    def setText(self, t):
        super().setText(t)                       # '…' 를 대답으로 갈아끼울 때
        self.updateGeometry()                    # 높이를 다시 물어보게만 한다


class Input(QTextEdit):
    """Enter = 보내기, Shift+Enter = 줄바꿈. 내용에 따라 높이가 자란다."""
    send = Signal()

    def __init__(self):
        super().__init__()
        self.setAcceptRichText(False)
        self.setPlaceholderText("메시지…   (Enter 전송 · Shift+Enter 줄바꿈)")
        self.setFixedHeight(46)
        self.textChanged.connect(self._grow)

    def _grow(self):
        h = int(self.document().size().height()) + 16
        self.setFixedHeight(max(46, min(150, h)))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("COMET")
        if os.path.exists(ICON):
            self.setWindowIcon(QIcon(ICON))
        self.resize(560, 720)
        self.setStyleSheet(f"background:{BG}; color:{FG};")
        self._busy = False
        self._pending = None          # 대답을 채워 넣을 말풍선
        self._secs = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 머리말: 이름 + 상태 ──
        head = QWidget()
        head.setStyleSheet(f"border-bottom:1px solid {LINE};")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        name = QLabel("COMET")
        name.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        name.setStyleSheet("letter-spacing:2px; border:none;")
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{SUB}; border:none;")
        self.speak = True                      # 대답을 소리로도 읽어줄지(데몬이 아야카로 읽는다)
        self.spk = QPushButton("🔊")
        self.spk.setFixedSize(32, 26)
        self.spk.setCursor(Qt.PointingHandCursor)
        self.spk.setToolTip("읽어주기 켜기/끄기")
        self.spk.setStyleSheet("QPushButton{background:transparent; border:none; font-size:14px;}")
        self.spk.clicked.connect(self._toggle_speak)
        self.news_on = None                    # 소식 듣기 — 데몬에서 받아 온 값. 모르면 None
        self._news_busy = False                # 묻는 중(겹쳐 묻지 않게)
        self._feed_last = None                 # 마지막으로 그린 소식 id — None = 아직 안 물어봄(지난 건 안 그린다)
        self.newsb = QPushButton()
        self.newsb.setFixedHeight(26)
        self.newsb.setCursor(Qt.PointingHandCursor)
        self.newsb.setToolTip("AI·경제 소식 알림 켜기/끄기 — 배너·목소리 전부\n"
                              "켤 때 그동안 쌓인 소식은 건너뛰고 새 소식부터")
        self.newsb.clicked.connect(self._toggle_news)
        self._paint_news()
        hl.addWidget(name)
        hl.addStretch(1)
        hl.addWidget(self.status)
        hl.addWidget(self.newsb)
        hl.addWidget(self.spk)
        root.addWidget(head)

        # ── 대화 영역 ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollBar:vertical{background:transparent;width:8px;}"
            f"QScrollBar::handle:vertical{{background:{LINE};border-radius:4px;}}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}")
        inner = QWidget()
        self.log = QVBoxLayout(inner)
        self.log.setContentsMargins(16, 16, 16, 16)
        self.log.setSpacing(10)
        self.log.addStretch(1)
        self.scroll.setWidget(inner)
        root.addWidget(self.scroll, 1)

        # ── 입력줄 ──
        foot = QWidget()
        foot.setStyleSheet(f"border-top:1px solid {LINE};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 12, 12, 12)
        fl.setSpacing(8)
        self.input = Input()
        self.input.setStyleSheet(
            f"background:#15171c; border:1px solid #2c2f37; border-radius:12px;"
            f"padding:10px; color:{FG}; font-size:14px;")
        self.btn = QPushButton("전송")
        self.btn.setFixedSize(72, 46)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(
            f"QPushButton{{background:{ME}; color:#fff; border:none; border-radius:12px;"
            "font-weight:600; font-size:14px;}"
            "QPushButton:disabled{background:#2c3444; color:#8b8f99;}")
        fl.addWidget(self.input, 1)
        fl.addWidget(self.btn)
        root.addWidget(foot)

        self.btn.clicked.connect(self.send)
        self.input.send.connect(self.send)
        QShortcut(QKeySequence("Esc"), self, activated=self.input.clear)

        self.bridge = Bridge()
        self.bridge.done.connect(self._arrived)
        self.bridge.news.connect(self._news_arrived)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.ntick = QTimer(self)
        self.ntick.timeout.connect(self._poll_news)
        self.ntick.start(NEWS_POLL_MS)
        QTimer.singleShot(300, self._poll_news)

        self._restore()
        self._fill_backlog()
        QTimer.singleShot(50, self._check_daemon)
        self.input.setFocus()

    # ── 화면 ──
    def _bubble(self, text, who, level=2):
        lab = Bubble(text)
        lab.setMaximumWidth(int(self.width() * BUBBLE_MAX))
        if who == "me":
            lab.setStyleSheet(f"background:{ME}; color:#fff; padding:9px 12px;"
                              "border-radius:14px; border-bottom-right-radius:4px;")
        elif who == "ai":
            lab.setStyleSheet(f"background:{CARD}; color:{FG}; padding:9px 12px;"
                              "border-radius:14px; border-bottom-left-radius:4px;")
        elif who == "news":                      # 소식 — 코멧 쪽, 왼쪽 색 띠로 대답과 구분
            lab.setTextFormat(Qt.PlainText)      # 기사 제목의 < > 가 태그로 먹히지 않게
            lab.setStyleSheet(f"background:{CARD}; color:{FG}; padding:9px 12px;"
                              "border-radius:14px; border-bottom-left-radius:4px;"
                              f"border-left:3px solid {NEWS_ACCENT.get(level, NEWS_ACCENT[2])};")
        else:                                    # sys
            lab.setStyleSheet(f"color:{SUB}; font-size:12px;")

        # ★정렬을 insertWidget 의 align 인자로 주면 안 된다 — 정렬이 걸린 레이아웃 항목은
        #   heightForWidth 를 무시하고 sizeHint 크기로만 놓여서, 긴 글이 한 줄 높이(33px)로
        #   납작하게 잘린다(2026-09-18 실측: 2500자가 449x33). 대신 행 위젯으로 감싸고
        #   좌/우에 여백(stretch)을 넣어 밀어준다 — 이러면 높이는 레이아웃이 제대로 물어본다.
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        if who == "me":
            rl.addStretch(1)
            rl.addWidget(lab)
        elif who in ("ai", "news"):
            rl.addWidget(lab)
            rl.addStretch(1)
        else:
            rl.addStretch(1)
            rl.addWidget(lab)
            rl.addStretch(1)
        self.log.insertWidget(self.log.count() - 1, row)
        QTimer.singleShot(0, self._to_bottom)
        return lab

    def _to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = int(self.width() * BUBBLE_MAX)
        for b in self.findChildren(Bubble):
            b.setMaximumWidth(w)

    def _fill_backlog(self):
        past = _backlog()
        if past:
            self._bubble(f"— 지난 대화 {len(past)}개 —", "sys")
            for who, text in past:
                self._bubble(text, who)

    def _check_daemon(self):
        if _alive():
            self.status.setText("데몬 연결됨")
        else:
            self.status.setText("데몬 꺼짐")
            self._bubble("데몬이 안 떠 있다 — run_daemon.bat 을 먼저 띄울 것.", "sys")

    # ── 주고받기 ──
    def send(self):
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self._bubble(text, "me")
        self._pending = self._bubble("…", "ai")
        self._busy = True
        self.btn.setEnabled(False)
        self._secs = 0
        self.status.setText("생각 중… 0초")
        self.tick.start(1000)
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _toggle_speak(self):
        self.speak = not self.speak
        self.spk.setText("🔊" if self.speak else "🔇")

    # ── 소식 듣기 (AI·경제 알림 스위치) ──
    def _paint_news(self):
        on = self.news_on
        if on is None:
            txt, css = "소식 듣기 …", f"background:transparent; color:{SUB}; border:1px solid {LINE};"
        elif on:
            txt, css = "소식 듣기 · 켜짐", f"background:{ME}; color:#fff; border:1px solid {ME};"
        else:
            txt, css = "소식 듣기 · 꺼짐", f"background:transparent; color:{SUB}; border:1px solid #2c2f37;"
        self.newsb.setText(txt)
        self.newsb.setStyleSheet("QPushButton{" + css + "border-radius:13px; padding:0 12px;"
                                 "font-size:12px;} QPushButton:disabled{color:#8b8f99;}")

    def _news_call(self, payload, toggled):
        if self._feed_last is not None:
            payload = dict(payload, after=self._feed_last)    # 그 뒤로 알린 소식도 같이 받는다

        def run():
            ok, res = _post("/news", payload, timeout=30)
            self.bridge.news.emit(ok, res, toggled)
        self._news_busy = True
        threading.Thread(target=run, daemon=True).start()

    def _poll_news(self):
        if not self._news_busy:
            self._news_call({}, False)

    def _toggle_news(self):
        if self.news_on is None or self._news_busy:
            return                              # 아직 상태를 모른다 — 데몬이 안 떠 있거나 묻는 중
        self.newsb.setEnabled(False)
        self._news_call({"on": not self.news_on}, True)

    def _news_arrived(self, ok, res, toggled):
        self._news_busy = False
        self.newsb.setEnabled(True)
        if ok and isinstance(res, dict) and "on" in res:
            self.news_on = bool(res["on"])
            for e in res.get("feed") or []:                    # 새로 알린 소식 → 말풍선
                self._bubble(e.get("text", ""), "news", e.get("level", 2))
                self._feed_last = max(self._feed_last or 0, int(e.get("id", 0)))
            if self._feed_last is None and isinstance(res.get("last"), int):
                self._feed_last = res["last"]                  # 처음 물었을 땐 여기서부터
            if toggled:
                self._bubble("소식 듣기 켬 — 지금 떠 있는 건 건너뛰고 새 소식부터" if self.news_on
                             else "소식 듣기 끔 — 다시 켤 때까지 소식 알림 안 함", "sys")
        else:
            self.news_on = None
            if toggled:
                self._bubble(f"소식 듣기 못 바꿈 — {res}", "sys")
        self._paint_news()

    def _worker(self, text):
        ok, res = _post("/chat", {"text": text, "speak": self.speak})
        self.bridge.done.emit(ok, res)

    def _tick(self):
        self._secs += 1
        self.status.setText(f"생각 중… {self._secs}초")

    def _arrived(self, ok, res):
        self.tick.stop()
        if ok:
            reply = (res.get("reply") or res.get("error") or "(빈 응답)").strip()
            self.status.setText(f"{self._secs}초")
        else:
            reply = res if isinstance(res, str) else str(res)
            self.status.setText("실패")
        if self._pending is not None:
            self._pending.setText(reply)
            self._pending = None
        self._busy = False
        self.btn.setEnabled(True)
        self.input.setFocus()
        QTimer.singleShot(0, self._to_bottom)

    # ── 창 크기·위치 기억 ──
    def _restore(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                s = json.load(f)
            self.resize(s["w"], s["h"])
            self.move(s["x"], s["y"])
        except Exception:
            pass

    def closeEvent(self, e):
        g = self.geometry()
        try:
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump({"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}, f)
        except Exception:
            pass
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("COMET")
    win = ChatWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
