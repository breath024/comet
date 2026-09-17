# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 자율/상주 모드 레이어  (comet.py 비대화 격리 — 토글만 comet 에서)
#
#  모드(켜고 끄는 단위):
#   · proactive : 주기적으로 뉴스를 보고 '알릴 만한가' 판단 → 텔레그램 푸시
#                 ("먼저 말 걸기"). 조각 재활용: news.collect + notify.send.
#                 모델 호출 0 = GPU 안 씀(게임 중에도 안 끊김).
#   · brief     : 매일 아침(미장 마감 후) 미국 증시 일일 브리핑 → 텔레그램 푸시.
#                 조각 재활용: market_brief.generate + notify.send. 27b(무거움)라
#                 GPU 가 게임 등으로 바쁘면 미룬다(하루 1회라 급할 것 없음).
#   · wake      : '코멧' 깨우는 말 상시 대기 → 들으면 응답 ("부르면 듣기").
#                 wake-word 엔진(openwakeword) 필요 — 없으면 available()=False.
#
#  켠 모드는 autonomy_config.json 에 저장(재시작해도 복원).
#  ※ always-on(프로세스가 죽으면 부활)은 프로세스 밖 일이라 모드가 아님 → watchdog 런처 별도.
#  ※ 각 모드는 자기 스레드에서 돌고 예외를 잡아 데몬 본체를 안 죽인다.
# ═══════════════════════════════════════════════════════════════
import os
import json
import threading

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_DIR, "autonomy_config.json")

DEFAULTS = {
    "proactive": {"enabled": False, "interval_sec": 600, "min_general": 5},
    "brief":     {"enabled": False, "hour": 7, "check_sec": 600, "last_date": ""},
    "wake":      {"enabled": False, "phrase": "코멧"},
    # issues: AI·경제 소식을 강도(굵직/볼만함/잔잔)로 걸러 화면 배너 + 음성. 로직은 issues.py
    #   modes 값 = 끔 | 굵직 | 보통 | 전부.  quiet = [시작시, 끝시] 이 시간엔 소리 없이 배너만(없으면 [])
    "issues":    {"enabled": False, "interval_sec": 120,
                  "modes": {"ai": "굵직", "econ": "굵직"}, "quiet": []},
    # talk: Alt 두 번 → 말 걸기. gap = 두 번 사이 최대 간격(초)
    "talk":      {"enabled": True, "gap": 0.45},
}


def _load_cfg():
    cfg = json.loads(json.dumps(DEFAULTS))   # 깊은 복사
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in cfg and isinstance(v, dict):
                    cfg[k].update(v)
        except Exception:
            pass
    return cfg


def _save_cfg(cfg):
    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  모드 베이스 — 스레드 + 예외 격리 + 가용성 게이트
# ═══════════════════════════════════════════════════════════════
class _Mode:
    name = "?"
    desc = ""

    def __init__(self, cfg, log):
        self.cfg = cfg              # 이 모드의 설정 dict (autonomy_config.json 의 한 칸)
        self.log = log             # log(str) — 서버 콘솔용
        self._thread = None
        self._stop = threading.Event()

    def available(self):
        """이 모드를 켤 수 있는가 → (가능여부, 사유)."""
        return True, ""

    def is_on(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        ok, why = self.available()
        if not ok:
            return False, why
        if self.is_on():
            return True, "이미 켜져 있음"
        self._stop.clear()
        self._thread = threading.Thread(target=self._guard, name=f"mode-{self.name}",
                                        daemon=True)
        self._thread.start()
        return True, "시작됨"

    def stop(self):
        self._stop.set()
        return True, "중지 요청"

    def _guard(self):
        try:
            self._run()
        except Exception as e:
            self.log(f"[{self.name}] 스레드 중단(예외): {e}")

    def _run(self):
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
#  proactive — 뉴스 감시 → 판단 → 텔레그램 푸시 (먼저 말 걸기)
# ═══════════════════════════════════════════════════════════════
class ProactiveMode(_Mode):
    name = "proactive"
    desc = "주기적으로 뉴스 보고 알릴 만하면 폰으로 먼저 알림"

    def _run(self):
        import news
        import notify
        interval = max(60, int(self.cfg.get("interval_sec", 600)))
        min_general = int(self.cfg.get("min_general", 5))

        # 최초 1회: 기존 기사를 '본 것'으로만 등록(폭주 방지 베이스라인)
        try:
            base = news.collect(mark_only=True)
            if not base.get("ok"):
                self.log(f"[proactive] {base.get('msg', 'watchlist 비어 있음')} "
                         "— 키워드 등록(add_watch) 후 알림 시작")
        except Exception as e:
            self.log(f"[proactive] 베이스라인 실패: {e}")

        self.log(f"[proactive] 가동 — {interval}초마다 점검")
        while not self._stop.wait(interval):
            try:
                r = news.collect()
                if not r.get("ok"):
                    continue
                new = r.get("new", [])
                urgent = r.get("urgent", [])
                if not new:
                    continue
                # 판단 게이트: 긴급(법정·제재 등)은 즉시, 일반은 min_general 이상 모이면
                if not (urgent or len(new) >= min_general):
                    continue
                msg = notify.format_digest(new, urgent)
                if not msg:
                    continue
                if notify.configured():
                    res = notify.send(msg)
                    self.log(f"[proactive] 푸시 신규{len(new)}/긴급{len(urgent)} → {res.get('msg')}")
                else:
                    self.log(f"[proactive] 알릴 거리 신규{len(new)}/긴급{len(urgent)} 있으나 "
                             "텔레그램 chat_id 미설정 — 보류(notify.get_chat_id 로 연결)")
            except Exception as e:
                self.log(f"[proactive] 점검 오류: {e}")


# ═══════════════════════════════════════════════════════════════
#  brief — 매일 아침 미국 증시 일일 브리핑 → 텔레그램 푸시 (먼저 말 걸기)
#   하루 1회: 설정 시각(hour) 이후 첫 점검에서 생성·전송하고 last_date 로 잠근다.
#   27b 무거운 작업이라 GPU 가 바쁘면(게임 등) 미룬다 — gpu_busy_fn 주입(comet._gpu_busy).
#   전송 성공 때만 last_date 를 박아, 실패하면 다음 점검에서 자동 재시도.
# ═══════════════════════════════════════════════════════════════
class BriefMode(_Mode):
    name = "brief"
    desc = "매일 아침 미증시 브리핑을 폰으로 (GPU 한가할 때, 게임 중이면 미룸)"

    def __init__(self, cfg, log, gpu_busy_fn=None, save_fn=None):
        super().__init__(cfg, log)
        self.gpu_busy_fn = gpu_busy_fn    # () -> (busy:bool, info:str). 없으면 게이트 없이 진행.
        self.save_fn = save_fn            # last_date 영속화(컨트롤러가 전체 cfg 저장).

    def _today(self):
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")

    def _push_todos(self, today):
        """오늘 할 일·마감을 아침에 읊어준다.

        ★증시 브리핑과 한 푸시로 묶지 않는다 — 브리핑은 27b 를 돌려서 게임 중이면 미뤄지고
        생성이 실패할 수도 있는데, 할 일은 모델이 전혀 필요 없다(DB 읽기뿐). 묶으면 GPU 사정에
        내 일정이 끌려다닌다. 그래서 게이트 바깥에서 따로, 날짜 잠금도 따로 건다.
        보낼 게 없는 날은 아예 안 보낸다(빈 알림이 쌓이면 알림을 안 보게 된다).
        """
        if self.cfg.get("todo_last_date") == today:
            return
        import notify
        import todo_box
        text = todo_box.digest()
        if not text:
            self.cfg["todo_last_date"] = today       # 오늘은 읊을 게 없음 — 하루 한 번만 확인
            if self.save_fn:
                self.save_fn()
            return
        sent = False
        if notify.configured():
            res = notify.send(text)
            sent = bool(res.get("ok"))
            if not sent:
                self.log(f"[brief] 할 일 전송 실패: {res.get('msg')} — 다음 점검 재시도")
        else:
            self.log("[brief] 텔레그램 미설정 — 폰 푸시는 건너뜀")

        # 화면 배너 + 음성 — 호윤이 폰을 안 보고 있어도 듣게. 끄려면 brief.todo_speak=false
        spoken = self._say_todos(text) if self.cfg.get("todo_speak", True) else False

        if sent or spoken:
            self.cfg["todo_last_date"] = today
            if self.save_fn:
                self.save_fn()
            self.log(f"[brief] 할 일 알림 완료 — {today} (폰 {'O' if sent else 'X'} / 소리 "
                     f"{'O' if spoken else 'X'})")

    def _say_todos(self, digest_text):
        """배너는 issues.popup 을 그대로 쓴다(한 곳에서만 띄우게). 게임 중이면 배너 생략·소리만
        — issues 알림과 같은 규칙. TTS 는 Edge-TTS(네트워크)라 GPU 를 안 쓴다."""
        try:
            import todo_box
            line = todo_box.speech()
            if not line:
                return False
            busy = False
            if self.gpu_busy_fn:
                busy, _ = self.gpu_busy_fn()
            if not busy:
                import issues
                body = "\n".join(digest_text.splitlines()[1:5])
                issues.popup("COMET · 오늘 할 일", line[:60], body, level=2, sec=10)
            import voice
            voice.Mouth().speak(line)
            return True
        except Exception as e:
            self.log(f"[brief] 할 일 음성 실패: {e}")
            return False

    def _run(self):
        import market_brief
        import notify
        from datetime import datetime
        hour = int(self.cfg.get("hour", 7))
        check = max(60, int(self.cfg.get("check_sec", 600)))
        self.log(f"[brief] 가동 — 매일 {hour}시 이후 미증시 브리핑(27b, GPU 한가할 때)")
        while not self._stop.wait(check):
            try:
                today = self._today()
                if datetime.now().hour >= hour:
                    self._push_todos(today)          # ★ 할 일은 GPU·모델 게이트 밖에서 먼저
                if self.cfg.get("last_date") == today:
                    continue                         # 오늘 이미 보냄
                if datetime.now().hour < hour:
                    continue                         # 아직 이른 시각
                # GPU 양보: 게임 등으로 바쁘면 미룬다(다음 점검에 재시도)
                if self.gpu_busy_fn:
                    busy, info = self.gpu_busy_fn()
                    if busy:
                        self.log(f"[brief] GPU 바쁨({info}) — 브리핑 미룸(다음 점검 재시도)")
                        continue
                if not notify.configured():
                    self.log("[brief] 텔레그램 미설정 — 브리핑 보류")
                    continue
                self.log("[brief] 미증시 브리핑 생성 중(27b)…")
                r = market_brief.generate()
                if not r.get("ok"):
                    self.log(f"[brief] 생성 실패: {r.get('msg')} — 다음 점검 재시도")
                    continue
                res = notify.send(r["text"])
                if res.get("ok"):
                    self.cfg["last_date"] = today    # 전송 성공 때만 잠금
                    if self.save_fn:
                        self.save_fn()
                    self.log(f"[brief] 푸시 완료 — {today}")
                else:
                    self.log(f"[brief] 전송 실패: {res.get('msg')} — 다음 점검 재시도")
            except Exception as e:
                self.log(f"[brief] 점검 오류: {e}")


# ═══════════════════════════════════════════════════════════════
#  issues — AI·경제 이슈를 강도로 걸러 화면 위 배너 + 음성으로 (먼저 말 걸기)
#   처음 켤 때는 지금 떠 있는 소식을 '본 것'으로만 등록한다(켜자마자 수십 건 쏟아지는 것 방지).
#   GPU 바쁨(게임) = 배너 생략·소리만 / quiet 시간 = 소리 생략·배너만 — issues.check 가 처리.
# ═══════════════════════════════════════════════════════════════
class IssuesMode(_Mode):
    name = "issues"
    desc = "AI·경제 이슈를 강도별로 걸러 화면 배너 + 음성으로 알림"

    def __init__(self, cfg, log, gpu_busy_fn=None):
        super().__init__(cfg, log)
        self.gpu_busy_fn = gpu_busy_fn

    def check_now(self, dry=False):
        import issues
        return issues.check(dict(self.cfg.get("modes") or {}), gpu_busy_fn=self.gpu_busy_fn,
                            quiet_hours=self.cfg.get("quiet") or None, log=self.log, dry=dry)

    def interval(self):
        # 한 바퀴 수집이 ~10초(2026-09-17 실측: AI 3.1 + 경제 6.5). 1분 밑으로는 안 내린다
        # — 레딧·구글뉴스 RSS 는 키 없이 자주 치면 막힌다(막혀도 그 소스만 0건, _safe).
        return max(60, int(self.cfg.get("interval_sec", 120)))

    def _run(self):
        import issues
        try:
            if not issues._load(issues.SEEN_FILE, []):
                issues.check(dict(self.cfg.get("modes") or {}), log=self.log, baseline=True)
                self.log("[issues] 처음 켬 — 지금 떠 있는 소식은 본 것으로 등록(다음 점검부터 새 소식만)")
        except Exception as e:
            self.log(f"[issues] 베이스라인 실패: {e}")
        self.log(f"[issues] 가동 — {self.interval() // 60}분마다 · {self.cfg.get('modes')}")
        while not self._stop.wait(self.interval()):     # 매 바퀴 다시 읽는다 — '알림 주기' 가 바로 먹게
            try:
                self.check_now()
            except Exception as e:
                self.log(f"[issues] 점검 오류: {e}")


# ═══════════════════════════════════════════════════════════════
#  wake — '코멧' 깨우는 말 상시 대기 → 응답 (부르면 듣기)
#   wake-word 엔진(openwakeword) 이 깔려 있어야 켜진다. 없으면 설치 안내.
#   엔진 + 마이크가 준비되면 _run 의 리스너를 연결한다.
# ═══════════════════════════════════════════════════════════════
class WakeMode(_Mode):
    name = "wake"
    desc = "'코멧' 부르면 마이크가 깨서 듣고 응답 (wake-word 엔진 필요)"

    def __init__(self, cfg, log, respond_fn=None, speak_fn=None):
        super().__init__(cfg, log)
        self.respond_fn = respond_fn      # respond(text) -> reply
        self.speak_fn = speak_fn          # speak(text) (선택)

    def available(self):
        try:
            import openwakeword  # noqa: F401
        except Exception:
            return (False, "wake-word 엔진 미설치 — `pip install openwakeword` 후 다시. "
                           "(오프라인·무료. 대안: pvporcupine=정확하나 키 필요)")
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False, "마이크 라이브러리(sounddevice) 미설치 — voice 환경 확인 필요."
        if self.respond_fn is None:
            return False, "응답 연결(respond_fn)이 없음 — comet 에서 주입 필요."
        return True, ""

    def _run(self):
        # 엔진/마이크/respond 가 모두 준비됐을 때만 여기 도달(available 통과).
        # openwakeword 로 상시 마이크에서 깨우는 말 탐지 → voice STT → respond_fn → speak.
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model

        phrase = self.cfg.get("phrase", "코멧")
        oww = Model()                       # 기본 사전학습 wake-word 묶음
        self.log(f"[wake] 가동 — '{phrase}' 대기 중")
        sr = 16000
        block = 1280                        # 80ms 프레임
        with sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                            blocksize=block) as stream:
            while not self._stop.is_set():
                data, _ = stream.read(block)
                pcm = np.frombuffer(data, dtype=np.int16)
                scores = oww.predict(pcm)
                if any(v > 0.5 for v in scores.values()):
                    self.log("[wake] 깨움 감지 → 듣기")
                    try:
                        import voice
                        heard = voice.Ear().listen()
                    except Exception as e:
                        self.log(f"[wake] STT 실패: {e}")
                        continue
                    if not heard:
                        continue
                    reply = self.respond_fn(heard)
                    if self.speak_fn and reply:
                        try:
                            self.speak_fn(reply)
                        except Exception:
                            pass


# ═══════════════════════════════════════════════════════════════
#  talk — Alt 두 번 톡톡 → 듣기 → 답하고 소리내기 (맥의 받아쓰기 단축키처럼)
#   키 라이브러리 설치 없이 윈도우 저수준 키보드 훅(ctypes)으로 잡는다. 키를 막지는 않는다(보기만).
#   ★Alt+Tab 같은 조합은 무시 — Alt 가 눌린 사이 다른 키가 끼면 톡이 아니다.
#   ★오른쪽 Alt 는 한글 키보드에선 한/영 키라 안 올 수 있다 — 왼쪽 Alt 기준.
#   ★콘솔(run.bat)과 데몬이 같이 떠도 한쪽만 잡게 이름 있는 뮤텍스로 막는다(두 번 대답 방지).
# ═══════════════════════════════════════════════════════════════
class DoubleTap:
    """키 이벤트를 넣으면 'Alt 두 번'인지 판정한다(훅과 분리 — 테스트하려고)."""
    ALTS = (0xA4, 0xA5, 0x12)          # VK_LMENU, VK_RMENU, VK_MENU

    def __init__(self, gap=0.45, hold=0.35):
        self.gap, self.hold = gap, hold
        self.down_at = None             # Alt 누른 시각(떼기 전)
        self.dirty = False              # Alt 누른 사이 다른 키가 끼었나
        self.last_tap = None

    def feed(self, vk, down, t):
        if vk not in self.ALTS:
            if down:
                self.dirty = True       # Alt+Tab 등
                self.last_tap = None
            return False
        if down:
            if self.down_at is None:    # 누르고 있으면 자동반복으로 down 이 계속 온다
                self.down_at, self.dirty = t, False
            return False
        if self.down_at is None:
            return False
        tap = not self.dirty and (t - self.down_at) <= self.hold
        self.down_at = None
        if not tap:
            self.last_tap = None
            return False
        if self.last_tap is not None and t - self.last_tap <= self.gap:
            self.last_tap = None
            return True
        self.last_tap = t
        return False


class TalkMode(_Mode):
    name = "talk"
    desc = "Alt 두 번 → 말 걸기 (듣고 답하고 소리로)"
    MUTEX = "Local\\COMET_TALK_HOTKEY"

    def __init__(self, cfg, log, respond_fn=None, speak_fn=None):
        super().__init__(cfg, log)
        self.respond_fn = respond_fn
        self.speak_fn = speak_fn
        self.lock = threading.Lock()   # 데몬이 자기 요청 락으로 갈아끼운다(대화기록 동시쓰기 방지)
        self._busy = threading.Lock()  # 듣는 중에 또 톡톡 → 무시
        self._tid = None
        self._ear = None

    def available(self):
        if os.name != "nt":
            return False, "윈도우 전용"
        if self.respond_fn is None:
            return False, "응답 연결(respond_fn)이 없음"
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False, "마이크 라이브러리(sounddevice) 없음"
        return True, ""

    def stop(self):
        self._stop.set()
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
        return True, "중지 요청"

    LOG_FILE = os.path.join(_DIR, "talk.log")
    START_TIMEOUT = 6          # 창 뜨고 이 안에 말도 타이핑도 없으면 닫는다

    def _trace(self, msg):
        """데몬 창은 내가(클로드가) 못 본다 — 어디서 멈췄는지 파일에도 남긴다."""
        self.log(f"[talk] {msg}")
        try:
            from datetime import datetime
            with open(self.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    def _preload(self):
        """Whisper 는 처음 올리는 데 몇 초 — 모드 켤 때 미리 올려둔다."""
        try:
            import voice
            ear = voice.Ear()
            ear._load()
            self._ear = ear
            self._trace("귀 준비 완료")
        except Exception as e:
            self._trace(f"귀 준비 실패: {e}")

    def _converse(self):
        if not self._busy.acquire(blocking=False):
            return
        import sys
        import time
        import tempfile
        import subprocess
        d = tempfile.mkdtemp(prefix="comet_ask_")
        has = lambda name: os.path.exists(os.path.join(d, name))  # noqa: E731

        def state(stage, text=""):
            try:
                tmp = os.path.join(d, "state.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"stage": stage, "text": text}, f, ensure_ascii=False)
                os.replace(tmp, os.path.join(d, "state.json"))
            except Exception:
                pass

        try:
            py = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(py):
                py = sys.executable
            subprocess.Popen([py, os.path.join(_DIR, "ask_box.py"), "--dir", d],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dead = time.time() + 3
            while time.time() < dead and not has("ready"):
                time.sleep(0.05)

            # 1) 말 — 타이핑을 시작하거나 창을 닫으면 바로 멈춘다
            heard = ""
            if self._ear is None:
                state("loading")
                self._preload()
            if self._ear is not None and not (has("typing") or has("closed")):
                state("listening")
                heard = self._ear.listen(stop_fn=lambda: has("typing") or has("closed"),
                                         start_timeout=self.START_TIMEOUT)
                self._trace(f"듣기 끝 {self._ear.last_stats} → {heard!r}")
            if has("closed"):
                return
            # 2) 타이핑 — 보낼 때까지(3초 멈춤 / Enter) 기다린다
            if has("typing"):
                dead = time.time() + 300
                while time.time() < dead and not has("typed.txt") and not has("closed"):
                    time.sleep(0.1)
                if not has("typed.txt"):
                    return
                with open(os.path.join(d, "typed.txt"), encoding="utf-8") as f:
                    text = f.read().strip()
                self._trace(f"입력: {text!r}")
            else:
                text = heard
                if not text:
                    state("none")
                    return
                state("heard", text)
            # 3) 대답 — 글로 보여주고 소리로도
            import issues
            asked_at = time.time()
            with self.lock:
                reply = self.respond_fn(text, speak=False) or ""
            self._trace(f"대답 {len(reply)}자")
            state("reply", reply)
            if reply and self.speak_fn and issues._last_say < asked_at:   # '뉴스'처럼 이미 읽었으면 또 안 읽음
                self.speak_fn(reply)
            state("done")
        except Exception as e:
            self._trace(f"오류: {e!r}")
            state("error", str(e))
        finally:
            self._busy.release()

    def _run(self):
        import time
        import ctypes
        from ctypes import wintypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        k32.CreateMutexW.restype = wintypes.HANDLE
        mutex = k32.CreateMutexW(None, False, self.MUTEX)
        if ctypes.GetLastError() == 183:                  # ERROR_ALREADY_EXISTS
            self.log("[talk] 다른 COMET 이 이미 Alt 두 번을 잡고 있음 — 여기선 안 잡음")
            k32.CloseHandle(mutex)
            return

        LRESULT = ctypes.c_ssize_t

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        PROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u32.SetWindowsHookExW.restype = wintypes.HHOOK
        u32.SetWindowsHookExW.argtypes = (ctypes.c_int, PROC, wintypes.HINSTANCE, wintypes.DWORD)
        u32.CallNextHookEx.restype = LRESULT
        u32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)

        det = DoubleTap(gap=float(self.cfg.get("gap", 0.45)))
        hook = None

        def on_key(code, wparam, lparam):
            try:
                if code == 0:
                    kb = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents
                    if not kb.flags & 0x10:                  # 프로그램이 흉내 낸 키(LLKHF_INJECTED)는 무시
                        down = wparam in (0x100, 0x104)      # WM_KEYDOWN / WM_SYSKEYDOWN
                        if det.feed(kb.vkCode, down, time.monotonic()):
                            threading.Thread(target=self._converse, name="talk-once",
                                             daemon=True).start()
            except Exception:
                pass
            return u32.CallNextHookEx(hook, code, wparam, lparam)

        proc = PROC(on_key)            # 참조를 쥐고 있어야 GC 가 콜백을 안 치운다
        hook = u32.SetWindowsHookExW(13, proc, None, 0)     # WH_KEYBOARD_LL
        if not hook:
            self.log(f"[talk] 키보드 훅 실패 (오류 {ctypes.GetLastError()})")
            k32.CloseHandle(mutex)
            return
        self._tid = k32.GetCurrentThreadId()
        self._trace("가동 — Alt 두 번 누르면 말걸기 창")
        if self._ear is None:
            threading.Thread(target=self._preload, name="talk-preload", daemon=True).start()
        try:
            msg = wintypes.MSG()
            while not self._stop.is_set() and u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        finally:
            u32.UnhookWindowsHookEx(hook)
            k32.CloseHandle(mutex)
            self._tid = None
            self.log("[talk] 멈춤")


# ═══════════════════════════════════════════════════════════════
#  컨트롤러 — comet 이 토글/상태만 호출
# ═══════════════════════════════════════════════════════════════
class Autonomy:
    def __init__(self, respond_fn=None, speak_fn=None, log=None, gpu_busy_fn=None):
        self.log = log or (lambda *_: None)
        self.cfg = _load_cfg()
        self.modes = {
            "proactive": ProactiveMode(self.cfg["proactive"], self.log),
            "brief":     BriefMode(self.cfg["brief"], self.log,
                                   gpu_busy_fn=gpu_busy_fn,
                                   save_fn=lambda: _save_cfg(self.cfg)),
            "wake":      WakeMode(self.cfg["wake"], self.log,
                                  respond_fn=respond_fn, speak_fn=speak_fn),
            "issues":    IssuesMode(self.cfg["issues"], self.log, gpu_busy_fn=gpu_busy_fn),
            "talk":      TalkMode(self.cfg["talk"], self.log,
                                  respond_fn=respond_fn, speak_fn=speak_fn),
        }

    # 저장된 enabled 모드 복원(데몬/콘솔 시작 시 호출)
    def restore(self):
        for name, m in self.modes.items():
            if self.cfg[name].get("enabled"):
                ok, why = m.start()
                self.log(f"[자율모드] {name} 복원 → {'ON' if ok else '실패: ' + why}")

    def _set_enabled(self, name, on):
        self.cfg[name]["enabled"] = bool(on)
        _save_cfg(self.cfg)

    def enable(self, name):
        m = self.modes.get(name)
        if not m:
            return f"그런 모드 없음: {name} (proactive | wake)"
        ok, why = m.start()
        if ok:
            self._set_enabled(name, True)
            return f"{name} 켬 — {m.desc}"
        return f"{name} 못 켬 — {why}"

    def disable(self, name):
        m = self.modes.get(name)
        if not m:
            return f"그런 모드 없음: {name}"
        m.stop()
        self._set_enabled(name, False)
        return f"{name} 끔"

    def status(self):
        lines = ["자율모드 상태:"]
        for name, m in self.modes.items():
            on = "🟢 ON" if m.is_on() else "⚪ off"
            avail, why = m.available()
            tail = "" if (avail or m.is_on()) else f"  (불가: {why})"
            lines.append(f"  · {name:9s} {on} — {m.desc}{tail}")
        return "\n".join(lines)

    # comet 의 "알림 ..." 한 줄 명령 파서 (low = 공백 뺀 소문자)
    #   알림                → 상태
    #   알림 ai 굵직|보통|전부|끔 / 알림 경제 ...   → 분야별 모드 (하나라도 켜지면 자율모드 issues 도 켬)
    #   알림 켜             → 끄기 전 강도로 되살림(없으면 굵직) / 알림 끔 → 전부 끔
    #   알림 지금 → 바로 한 번 점검 / 알림 테스트 → 배너+음성 샘플
    #   알림 조용 2-8       → 2시~8시엔 소리 없이 배너만 / 알림 조용 끔
    #   알림 기록           → 최근 알린 것
    def issues_command(self, low):
        import issues
        body = low.replace("알림", "", 1).strip()
        m = self.modes["issues"]
        c = self.cfg["issues"]
        cats = {"ai": "ai", "에이아이": "ai", "경제": "econ", "econ": "econ"}

        def _sync():
            want = any(issues.MODE_MIN.get(v, 99) <= 3 for v in c["modes"].values())
            if want and not m.is_on():
                self.enable("issues")
            elif not want and m.is_on():
                self.disable("issues")
            else:
                _save_cfg(self.cfg)

        if body in ("", "상태", "status"):
            q = c.get("quiet") or []
            return ("이슈 알림: " + ("켜짐" if m.is_on() else "꺼짐") + " — "
                    + " · ".join(f"{issues.CAT_NAME[k]} {v}" for k, v in c["modes"].items())
                    + f" · {m.interval() // 60}분마다"
                    + (f" · 조용한 시간 {q[0]}~{q[1]}시" if q else "")
                    + "\n바꾸기: 알림 켜 / 알림 끔 / 알림 ai 보통 / 알림 경제 굵직 / 알림 지금 / 알림 테스트 / 알림 기록"
                    + "\n다시 듣기: 알림 다시 (최근 3건) · 지금 뉴스 모아보기: 뉴스")
        if body in ("끔", "꺼", "off"):
            # 끄기 전 강도를 적어둔다 — '알림 켜' 로 그대로 되살리려고
            if any(v != "끔" for v in c["modes"].values()):
                c["last_modes"] = dict(c["modes"])
            for k in c["modes"]:
                c["modes"][k] = "끔"
            _sync()
            return "이슈 알림 전부 끔. (다시 켜기: 알림 켜)"
        if body in ("켜", "켬", "온", "on"):
            prev = c.get("last_modes") or {}
            for k in c["modes"]:
                c["modes"][k] = prev.get(k, "끔")
            if all(v == "끔" for v in c["modes"].values()):   # 되살릴 게 없으면 기본값
                for k in c["modes"]:
                    c["modes"][k] = "굵직"
            _sync()
            return ("이슈 알림 켬 — "
                    + " · ".join(f"{issues.CAT_NAME[k]} {v}" for k, v in c["modes"].items())
                    + f" · {m.interval() // 60}분마다. (끄기: 알림 끔)")
        if body in ("지금", "점검", "now"):
            r = m.check_now()
            n = sum(r.values())
            return f"지금 점검 — 새로 알린 것 {n}건." if n else "지금 점검 — 새로 알릴 만한 건 없어."
        if body in ("테스트", "test"):
            issues.deliver("ai", [{"level": 3, "title": "테스트 알림", "ko": "알림 테스트야, 잘 보이면 된 거야",
                                   "why": "COMET 이슈 알림 테스트"}], log=self.log)
            return "테스트 배너·음성 보냈어."
        if body.startswith(("다시", "again")):
            mm = __import__("re").search(r"\d+", body)
            return issues.replay(min(10, int(mm.group())) if mm else 3, log=self.log)
        if body in ("뉴스", "뉴스ko", "뉴스en", "모아", "news"):
            lang = body[2:] if body.startswith("뉴스") else ""
            return issues.news_digest(gpu_busy_fn=m.gpu_busy_fn, log=self.log, lang=lang or None)
        if body in ("기록", "로그", "log"):
            rows = issues._load(issues.LOG_FILE, [])[-8:]
            if not rows:
                return "아직 알린 기록 없음."
            return "최근 알림:\n" + "\n".join(
                f"  {r['at'][5:16]} [{issues.CAT_NAME.get(r['cat'], r['cat'])}·{issues.LEVEL_NAME.get(r['level'])}] "
                f"{r.get('ko') or r['title']}" for r in reversed(rows))
        if body.startswith("주기"):
            mm = __import__("re").match(r"(\d+)(초|분)?", body[2:])
            if not mm:
                return f"지금 {m.interval() // 60}분마다. 바꾸기: 알림 주기 2 (분) / 알림 주기 90초 — 최소 1분"
            sec = int(mm.group(1)) * (1 if mm.group(2) == "초" else 60)
            c["interval_sec"] = max(60, sec)
            _save_cfg(self.cfg)
            return f"이슈 알림 {c['interval_sec']}초마다 확인 (다음 바퀴부터)."
        if body.startswith("조용"):
            rest = body[2:]
            if rest in ("끔", "꺼", "해제", "off"):
                c["quiet"] = []
                _save_cfg(self.cfg)
                return "조용한 시간 해제 — 언제든 소리 냄."
            mm = __import__("re").match(r"(\d{1,2})[-~](\d{1,2})", rest)
            if not mm:
                return "예: 알림 조용 2-8 (2시~8시엔 소리 없이 배너만) / 알림 조용 끔"
            c["quiet"] = [int(mm.group(1)) % 24, int(mm.group(2)) % 24]
            _save_cfg(self.cfg)
            return f"조용한 시간 {c['quiet'][0]}~{c['quiet'][1]}시 — 그 사이엔 배너만."
        for key, cat in cats.items():
            if body.startswith(key):
                level = body[len(key):]
                if level not in issues.MODE_MIN:
                    return f"{issues.CAT_NAME[cat]} 알림 강도: 굵직 / 보통 / 전부 / 끔 (예: 알림 {key} 굵직)"
                c["modes"][cat] = level
                _sync()
                desc = {"굵직": "굵직한 것만", "보통": "볼만한 것부터", "전부": "잔잔한 것까지 전부", "끔": "안 알림"}
                return f"{issues.CAT_NAME[cat]} 알림 → {level} ({desc[level]})."
        return ("알림 사용법: 알림 / 알림 켜 / 알림 끔 / 알림 ai 굵직|보통|전부|끔 / 알림 경제 … / "
                "알림 지금 / 알림 테스트 / 알림 기록 / 알림 다시 / 알림 조용 2-8 / 뉴스")

    # comet 의 "자율모드 ..." 한 줄 명령 파서
    def command(self, low):
        body = low.replace("자율모드", "", 1).replace("자율 모드", "", 1).strip()
        if not body or body in ("상태", "status"):
            return self.status()
        for name in self.modes:
            if body.startswith(name):
                rest = body[len(name):].strip()
                if rest in ("켜", "켜기", "on"):
                    return self.enable(name)
                if rest in ("꺼", "끄기", "off", "해제"):
                    return self.disable(name)
                return f"{name}: '켜' 또는 '꺼'로 (예: 자율모드 {name} 켜)"
        return ("자율모드 사용법: '자율모드'(상태) · '자율모드 proactive 켜/꺼' · "
                "'자율모드 brief 켜/꺼' · '자율모드 wake 켜/꺼' · '자율모드 talk 켜/꺼'(Alt 두 번 말걸기)")


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    a = Autonomy(respond_fn=lambda t: f"(테스트응답:{t})", log=print,
                 gpu_busy_fn=lambda: (False, "테스트 GPU 한가"))
    print(a.status())
    print("---")
    print(a.command("자율모드 wake 켜"))      # 엔진 없으면 불가 사유 출력
    print(a.command("자율모드 proactive 켜"))  # watchlist 비어도 가동(베이스라인 로그)
    print(a.command("자율모드 brief 켜"))      # 매일 아침 브리핑 스레드 가동
    print(a.status())
    print("---")
    print(a.command("자율모드 brief 꺼"))
