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
#                 GPU 가 게임 등으로 바쁘면 구독 CLI(클로드→아스트라)로 우회해서 그냥 보낸다
#                 (2026-09-18 전까지는 미뤘다 — 바쁜 날은 브리핑이 아예 안 왔음).
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
    # telegram: 텔레그램으로 물으면 답한다(호윤 대화방만). 호윤이 만들어 달라고 해서 처음부터 켜 둔다(10/07)
    "telegram":  {"enabled": True},
    # upgrade: 하루 한 번 hour 시 이후 고칠 거리를 찾아 제안 + 막힌 업그레이드 다시 시도. 로직은 upgrade.py
    #   호윤이 「스스로 찾아서 제안」을 골라서(10/13) 처음부터 켜 둔다. 반영은 호윤 「업그레이드 적용」 때만.
    "upgrade":   {"enabled": True, "hour": 13, "check_sec": 1800, "last_date": ""},
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

    def running(self):
        """켜져 있고 끄는 중도 아니다. is_on() 은 '끄라고 했는데 아직 점검·말하는 중'인 스레드도 참이다."""
        return self.is_on() and not self._stop.is_set()

    def start(self):
        ok, why = self.available()
        if not ok:
            return False, why
        if self.is_on():
            if not self._stop.is_set():
                return True, "이미 켜져 있음"
            #  ★끄는 중(점검·말하는 중이라 아직 안 끝난 스레드)에 다시 켬 — 버튼을 빨리 껐다 켜면 생긴다.
            #    예전엔 '이미 켜져 있음' 으로 돌아가 버려서, 그 스레드가 끝나는 순간 설정은 켜짐인데 실제론 꺼졌다.
            self._stop.clear()                   # 그 스레드를 그대로 살린다
            self._thread.join(0.2)               # 막 빠져나가던 참이었으면 끝나길 잠깐 기다렸다가
            if self.is_on():
                return True, "다시 켬"
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
    desc = "매일 아침 미증시 브리핑을 폰으로 (클로드·아스트라 먼저, 막히면 로컬)"

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
                import notify                  # 아침 할 일은 텔레그램용 HTML — 배너엔 태그 벗긴 맨글로
                plain = [l for l in notify._plain(digest_text).splitlines()[1:] if l.strip()]
                body = "\n".join(plain[:4])
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
        self.log(f"[brief] 가동 — 매일 {hour}시 이후 미증시 브리핑(클로드·아스트라 먼저, 막히면 로컬)")
        while not self._stop.wait(check):
            try:
                today = self._today()
                if datetime.now().hour >= hour:
                    self._push_todos(today)          # ★ 할 일은 GPU·모델 게이트 밖에서 먼저
                if self.cfg.get("last_date") == today:
                    continue                         # 오늘 이미 보냄
                if datetime.now().hour < hour:
                    continue                         # 아직 이른 시각
                # 두뇌: 구독 CLI(클로드→아스트라)가 먼저, 둘 다 실패하면 로컬 26b(generate 안에서).
                #  ★2026-10-07 로컬 먼저 → 클라우드 먼저로 뒤집었다. 아침마다 로컬 26b(13GB)를 올리면
                #    아야카·Whisper 와 16GB 를 다투다 아야카가 굳었다(호윤: "TTS 개에반데"). 호윤 지시도
                #    "claude 나 gpt 로 해결할 수 있는 건 그쪽 cli 로". (9/18: "로컬로만 안 돌아도 괜찮음")
                via = "cloud"
                if not notify.configured():
                    self.log("[brief] 텔레그램 미설정 — 브리핑 보류")
                    continue
                # 주말·휴장일엔 새 장이 없다 — 이미 보낸 장이면 오늘은 건너뛴다. 예전엔 토·일·월
                # 아침마다 같은 금요일 장이 세 번 왔다(호윤 10/07). 장 날짜를 못 구하면 그냥 진행.
                ses = market_brief.latest_session()
                if ses and self.cfg.get("last_session") == str(ses["date"]):
                    self.cfg["last_date"] = today    # 오늘은 더 볼 것 없음 — 하루 한 번만 확인
                    if self.save_fn:
                        self.save_fn()
                    self.log(f"[brief] {ses['date']} 장은 이미 보냄 — 오늘은 건너뜀(주말·휴장)")
                    continue
                self.log("[brief] 미증시 브리핑 생성 중"
                         + ("(클로드/아스트라)…" if via == "cloud" else "(로컬 26b)…"))
                r = market_brief.generate(via=via, log=self.log)
                if not r.get("ok"):
                    self.log(f"[brief] 생성 실패: {r.get('msg')} — 다음 점검 재시도")
                    continue
                res = notify.send(r["text"])
                if res.get("ok"):
                    self.cfg["last_date"] = today    # 전송 성공 때만 잠금
                    if r.get("session"):
                        self.cfg["last_session"] = r["session"]
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
        #  다음에 켤 때 지금 떠 있는 소식을 '본 것'으로만 등록한다 — 껐다 켜면 그동안 쌓인 게 한꺼번에 쏟아졌다.
        #  호윤이 켤 때(알림 켜·소식 듣기 버튼)만 세운다. 데몬 재시작(restore)은 예전 그대로.
        self.rebase = False

    def check_now(self, dry=False):
        import issues
        return issues.check(dict(self.cfg.get("modes") or {}), gpu_busy_fn=self.gpu_busy_fn,
                            quiet_hours=self.cfg.get("quiet") or None, log=self.log, dry=dry,
                            alive=lambda: not self._stop.is_set())    # 점검 도중 꺼지면 알리지 않는다

    def interval(self):
        # 한 바퀴 수집이 ~10초(2026-09-17 실측: AI 3.1 + 경제 6.5). 1분 밑으로는 안 내린다
        # — 레딧·구글뉴스 RSS 는 키 없이 자주 치면 막힌다(막혀도 그 소스만 0건, _safe).
        return max(60, int(self.cfg.get("interval_sec", 120)))

    MUTEX = "Local\\COMET_ISSUES"

    def _single(self):
        """이 PC 에서 소식 알림은 한 COMET 만. 잡으면 핸들, 남이 잡고 있으면 False, 윈도우가 아니면 None.
           ★데몬 + 콘솔(run.bat)이 같이 뜨면 둘 다 restore() 로 소식을 돌린다 — 같은 소식이 두 번 울리고 목소리도 겹친다.
             (9/18 11:16·11:17 같은 8건이 재시작 기록 없이 90초 간격으로 두 번 — 이것으로 보인다. 로그가 없어 확인은 못 함)
             말걸기(TalkMode)와 같은 이름 있는 뮤텍스 방식."""
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        h = k32.CreateMutexW(None, False, self.MUTEX)
        if ctypes.get_last_error() == 183:              # ERROR_ALREADY_EXISTS
            k32.CloseHandle(wintypes.HANDLE(h))
            return False
        return h

    def _run(self):
        import issues
        mutex = self._single()
        if mutex is False:
            self.log("[issues] 다른 COMET 이 이미 소식 알림을 돌리고 있음 — 여기선 안 돎")
            return
        try:
            try:
                if self.rebase or not issues._load(issues.SEEN_FILE, []):
                    first = not self.rebase
                    self.rebase = False
                    issues.check(dict(self.cfg.get("modes") or {}), log=self.log, baseline=True)
                    self.log(f"[issues] {'처음 켬' if first else '다시 켬'} — 지금 떠 있는 소식은 본 것으로 등록"
                             "(다음 점검부터 새 소식만)")
            except Exception as e:
                self.log(f"[issues] 베이스라인 실패: {e}")
            self.log(f"[issues] 가동 — {self.interval() // 60}분마다 · {self.cfg.get('modes')}")
            while not self._stop.wait(self.interval()):     # 매 바퀴 다시 읽는다 — '알림 주기' 가 바로 먹게
                try:
                    self.check_now()
                except Exception as e:
                    self.log(f"[issues] 점검 오류: {e}")
        finally:
            if mutex:
                import ctypes
                ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(mutex))


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

    def __init__(self, cfg, log, respond_fn=None, speak_fn=None, cancel_fn=None):
        super().__init__(cfg, log)
        self.respond_fn = respond_fn      # respond(text) -> reply, Esc 로 끊겼으면 None
        self.speak_fn = speak_fn
        self.cancel_fn = cancel_fn        # 생성 중단 신호
        self.lock = threading.Lock()   # 데몬이 자기 요청 락으로 갈아끼운다(대화기록 동시쓰기 방지)
        self._busy = threading.Lock()  # 듣는 중에 또 톡톡 → 무시
        self._tid = None
        self._ear = None
        self._unload_timer = None

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
    # Whisper 는 켤 때 미리 안 올리고 처음 Alt 두 번 때 올린다 · 마지막 말걸기 뒤 이만큼(초) 안 쓰면 내린다(10/16).
    #   호윤 「7기가 안 잡게」 — turbo 를 올려 두면 데몬이 7GB(GPU 몫 예약) · 내리면 2.4GB(torch 는 한 번 올리면 못 내림).
    #   9/18 뒤로 말로 한 적이 없는데 켤 때마다 올려 두고 있었다. 대신 첫 말걸기는 올리는 동안 4~6초 기다린다.
    #   설정 talk.unload_after(초) · 0 이면 안 내림.
    IDLE_UNLOAD = 600

    def _trace(self, msg):
        """데몬 창은 내가(클로드가) 못 본다 — 어디서 멈췄는지 파일에도 남긴다."""
        self.log(f"[talk] {msg}")
        try:
            from datetime import datetime
            with open(self.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")
        except Exception:
            pass

    def _schedule_unload(self):
        if self._unload_timer:
            self._unload_timer.cancel()
        sec = float(self.cfg.get("unload_after", self.IDLE_UNLOAD))
        if sec <= 0 or self._ear is None:
            return
        self._unload_timer = threading.Timer(sec, self._unload)
        self._unload_timer.daemon = True
        self._unload_timer.start()

    def _unload(self):
        if not self._busy.acquire(blocking=False):    # 그 사이 또 말 거는 중 — 끝나면 다시 잰다
            return
        try:
            if self._ear is None:
                return
            self._ear = None
            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            self._trace("귀 내림 — 한동안 안 씀(다음 말걸기 때 다시 올림)")
        finally:
            self._busy.release()

    def _preload(self):
        """Whisper 는 처음 올리는 데 몇 초 — 첫 말걸기 때 올린다(켤 때 미리 올리던 건 10/16 뺌)."""
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
        ducked = False
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
            import voice
            voice.ducker.remember_front()     # 창이 맨 앞을 가져가기 전에 — 이 앱 소리는 안 줄인다
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
                voice.ducker.on()             # 내가 말하는 동안 다른 소리 낮춤
                ducked = True
                heard = self._ear.listen(stop_fn=lambda: has("typing") or has("closed"),
                                         start_timeout=self.START_TIMEOUT)
                self._trace(f"듣기 끝 {self._ear.last_stats} → {heard!r}")
                if not heard:                 # 말로 물었으면 대답 끝까지 낮춘 채로(오르내리면 거슬린다)
                    voice.ducker.off()
                    ducked = False
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
            # 3) 대답 — 글로 보여주고 소리로도. 그동안 창에서 Esc(=closed) 오면 생성·음성을 끊는다
            import issues
            asked_at = time.time()
            live = {"gen": False, "end": False}

            def watch_esc():
                while not live["end"]:
                    if has("closed"):
                        if live["gen"] and self.cancel_fn:
                            self.cancel_fn()
                        voice.stop_speaking()
                        return
                    time.sleep(0.1)
            threading.Thread(target=watch_esc, name="talk-esc", daemon=True).start()
            try:
                with self.lock:
                    if has("closed"):
                        reply = None
                    else:
                        live["gen"] = True    # 락을 쥔 뒤에만 — 폰 요청 생성은 안 끊게
                        try:
                            reply = self.respond_fn(text)
                        finally:
                            live["gen"] = False
                if reply is None or has("closed"):
                    self._trace("Esc — 취소함")
                    return
                self._trace(f"대답 {len(reply)}자")
                state("reply", reply)
                if reply and self.speak_fn and issues._last_say < asked_at:   # '뉴스'처럼 이미 읽었으면 또 안 읽음
                    self.speak_fn(reply)
                state("done")
            finally:
                live["end"] = True
        except Exception as e:
            self._trace(f"오류: {e!r}")
            state("error", str(e))
        finally:
            if ducked:
                voice.ducker.off()
            self._schedule_unload()
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
#  telegram — 텔레그램으로 물으면 답한다 (폰에서 코멧에게 말 걸기, 2026-10-07)
#   호윤: "내가 텔레그램으로 얘한테 질문해도 받았던가" → 원래는 보내기만 했다(받는 건 처음 연결할 때 chat_id 찾기뿐).
#   getUpdates 를 길게 기다려(long polling) 새 말을 받는다 — 서버를 밖에 안 열어도 되고 키도 이미 있다.
#   ★저장된 호윤 chat_id 에서 온 글만 받는다 — 봇 이름을 알아낸 남이 말을 걸어도 무시.
#   ★한 PC 에 하나만 — 같은 봇 토큰을 두 곳이 기다리면 텔레그램이 409 로 서로 끊는다(데몬 + run.bat). 뮤텍스.
#   ★꺼져 있는 동안 온 말은 STALE_SEC 안의 것만 답한다 — 몇 시간 전 질문에 뜬금없이 답하지 않게.
#   답은 글로만(폰으로 물었으면 PC 앞에 없다 — 스피커로 안 읽는다). 생각하는 동안 '입력 중…', 오래 걸리면 한 줄 먼저.
#   오간 말은 대화창에도 말풍선으로(issues.post_note — 소식과 같은 /news 길).
# ═══════════════════════════════════════════════════════════════
def _note(cat, text):
    try:
        import issues
        issues.post_note(cat, text)
    except Exception:
        pass


# 답 단계 시각 — comet.respond 가 모델을 고른 순간을 여기 찍는다(텔레그램 답이 늦는 원인 찾기용, 10/14).
#   respond 는 텔레그램 받기 스레드에서 그대로 돌아서 스레드별로 둔다 — 대화창 · 말걸기 쪽 찍기는 그냥 버려진다.
_stamps = threading.local()


def stamp_routed():
    import time
    _stamps.routed = time.time()
    cb = getattr(_stamps, "on_routed", None)          # 텔레그램 답 중이면 그 자리에서 로그 한 줄
    if cb:
        cb()


class TelegramMode(_Mode):
    name = "telegram"
    desc = "텔레그램으로 물으면 답함 (호윤 대화방만)"
    MUTEX = "Local\\COMET_TELEGRAM"
    LONG_POLL = 25          # 초 — 텔레그램이 새 말이 올 때까지 붙잡고 있다가 돌려준다(끄면 이만큼 안에 멈춤)
    STALE_SEC = 600         # 이보다 오래된 말은 답하지 않는다(꺼져 있을 때 온 것)
    SLOW_SEC = 30           # 이보다 오래 생각하면 "끝나면 보낼게" 한 줄 먼저

    def __init__(self, cfg, log, respond_fn=None):
        super().__init__(cfg, log)
        self.respond_fn = respond_fn      # respond(text) -> reply (PC 에서 소리 안 냄)
        self.lock = None                  # 데몬이 _lock 을 꽂는다 — 폰 웹·대화창·Alt 두 번과 같은 줄에 선다
        self._t_in = 0.0                  # 지금 답하는 말을 받은 시각 — _mark 가 +초 를 여기서 잰다

    def _mark(self, what):
        """단계 하나를 그 자리에서 바로 찍는다 — 생성·보내기가 멈춰도 어디까지 왔는지 남게(10/14)."""
        import time
        try:
            self.log(f"[텔레그램]   {what} +{time.time() - self._t_in:.1f}s")
        except Exception:
            pass

    def available(self):
        import notify
        if self.respond_fn is None:
            return False, "응답 연결(respond_fn)이 없음"
        if not notify.configured():
            return False, "텔레그램 미설정(token/chat_id) — notify.py 참고"
        return True, ""

    def _answer(self, text):
        import time
        import notify
        done = threading.Event()

        def busy():                       # 생각하는 동안 '입력 중…'(5초짜리라 4초마다), 오래 걸리면 한 줄 먼저
            t0, told = time.time(), False
            while not done.wait(4):
                notify.typing()
                if not told and time.time() - t0 > self.SLOW_SEC:
                    notify.send("생각이 좀 걸리네 — 끝나면 바로 보낼게.", html_mode=False)
                    told = True

        notify.typing()                      # 이것도 urlopen 한 번(받기 스레드에서) — 단계 로그에 따로 찍는다
        st = self._times = {"typed": time.time()}   # + 대화 줄 잡음(lock) · 모델 고름(routed) · 답 끝(gen) — 요약 줄용
        self._mark("입력 중 표시")
        threading.Thread(target=busy, name="telegram-typing", daemon=True).start()
        import remote_guard
        _stamps.routed = None
        _stamps.on_routed = lambda: self._mark("모델 고름")
        try:
            with remote_guard.on():          # 원격 표시 — 코딩 · 셸 · 위임 · 파일 쓰기는 막힌다(10/10 T-18)
                if self.lock:
                    with self.lock:
                        st["lock"] = time.time()
                        self._mark("줄 잡음")
                        reply = self.respond_fn(text)
                else:
                    st["lock"] = time.time()
                    self._mark("줄 잡음")
                    reply = self.respond_fn(text)
        except Exception as e:
            reply = f"[오류] {e}"
        finally:
            done.set()
            st["gen"] = time.time()
            st["routed"] = _stamps.routed
            _stamps.on_routed = None
            self._mark("답 끝")
        return (reply or "").strip() or "(할 말이 없대 — 빈 답)"

    def _handle(self, u):
        import time
        import notify
        msg = u.get("message") or {}
        if not msg:
            return
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != notify.chat_id():
            self.log(f"[텔레그램] 모르는 대화방({chat})에서 온 말 — 무시")
            return
        text = (msg.get("text") or "").strip()
        # 남의 글을 전달(forward)한 것 · 다른 봇을 거친 글은 명령으로 받지 않는다 — 호윤이 쓴 글처럼 돌았다(10/10 T-18)
        if any(k in msg for k in ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name",
                                  "forward_date", "via_bot", "is_automatic_forward")):
            self.log(f"[텔레그램] 전달된 글이라 건너뜀: {text[:40]}")
            notify.send("전달된 글은 명령으로 안 받아 — 직접 써 줘.", html_mode=False)
            return
        if time.time() - msg.get("date", 0) > self.STALE_SEC:
            self.log(f"[텔레그램] 꺼져 있을 때 온 말이라 건너뜀: {text[:40]}")
            return
        if not text:
            notify.send("지금은 글만 알아들어 — 사진·음성은 아직 못 받아.", html_mode=False)
            return
        # 받은 말 · 답 전문을 콘솔과 대화창에도 — 예전엔 받은 말 60자 · 답 글자 수만 찍혀서 PC 앞에선 뭐가 오갔는지 몰랐다(10/14)
        t_in = self._t_in = time.time()
        self.log(f"\n[텔레그램] 받음(폰에서 보낸 지 {t_in - msg.get('date', 0):.0f}s): {text[:500]}")
        _note("tg_me", text)
        reply = self._answer(text)
        t_send = time.time()
        self._mark("보내기 시작")              # 조각 · 시도마다는 notify.send 가 그 자리에서 찍는다
        r = notify.send_long(reply, record=False)      # 대화 답은 이미 대화 기록에 있다(outbox 겹침 방지)
        t_end = time.time()
        self._mark("보내기 끝")
        self.log(f"[텔레그램] 답: {reply[:500]}" + (f" …(전체 {len(reply)}자)" if len(reply) > 500 else ""))
        self.log(f"[텔레그램] 답함 {len(reply)}자 · {r.get('parts')}통 — {r.get('msg', '')[:60]}")
        try:                                  # 시각 찍기가 틀려도 대화창 말풍선은 그대로 나가게
            self._log_times(t_in, self._times, t_send, t_end, r)
        except Exception as e:
            self.log(f"[텔레그램] 답 시간 기록 실패: {e}")
        _note("tg_ai", reply)

    def _log_times(self, t_in, st, t_send, t_end, r):
        """답 하나 한 줄 요약 — 단계는 _mark · notify.send 가 그때그때 찍었다(10/14 — 늦는 원인 찾기용, 동작은 안 바꾼다)."""
        import notify
        times = r.get("times") or []
        lock = st.get("lock") or t_in
        self.log(f"[텔레그램] 답 시간: 대기 {lock - t_in:.1f}s · 생성 {(st.get('gen') or lock) - lock:.1f}s"
                 f" · 보내기 {t_end - t_send:.1f}s(시도 {sum(len(t[2]) for t in times)}번,"
                 f" urlopen 타임아웃 {notify.TIMEOUT}초)")

    def _run(self):
        import urllib.error
        import notify
        mutex = _named_mutex(self.MUTEX)
        if mutex is False:
            self.log("[텔레그램] 다른 COMET 이 이미 받고 있음 — 여기선 안 받음")
            return
        try:
            self.log("[텔레그램] 가동 — 호윤 대화방에서 온 말에 답함")
            offset, wait = None, 5
            while not self._stop.is_set():
                try:
                    ups = notify.updates(offset, timeout=self.LONG_POLL)
                    wait = 5
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        self.log("[텔레그램] 봇 토큰이 틀림(401) — 받기 멈춤")
                        return
                    wait = 60 if e.code == 409 else min(wait * 2, 120)   # 409 = 다른 곳이 같은 봇을 기다리는 중
                    self.log(f"[텔레그램] 받기 실패 {e.code} — {wait}초 뒤 다시")
                    self._stop.wait(wait)
                    continue
                except Exception as e:
                    self.log(f"[텔레그램] 받기 실패: {str(e)[:80]} — {wait}초 뒤 다시")
                    self._stop.wait(wait)
                    wait = min(wait * 2, 120)
                    continue
                for u in ups:
                    offset = u.get("update_id", 0) + 1     # 먼저 올려 둔다 — 답하다 죽어도 같은 말을 또 받지 않게
                    if self._stop.is_set():
                        break
                    try:
                        self._handle(u)
                    except Exception as e:
                        self.log(f"[텔레그램] 처리 오류: {e}")
        finally:
            if mutex:
                import ctypes
                ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(mutex))


# ═══════════════════════════════════════════════════════════════
#  upgrade — 코멧 자가 업그레이드 제안 (upgrade.py)
#   하루 1회 hour 시 이후: 열린 이슈 · 반복 오류 · HANDOFF 를 보고 고칠 거리 제안(배너 + 텔레그램).
#   점검마다: 모델이 막혀 기다리는 업그레이드 다시 · 중간에 꺼진 업그레이드 이어서.
#   데몬 + 콘솔이 같이 떠도 한 COMET 만(이름 있는 뮤텍스).
# ═══════════════════════════════════════════════════════════════
class UpgradeMode(_Mode):
    name = "upgrade"
    desc = "하루 한 번 고칠 거리를 찾아 업그레이드 제안 · 막힌 업그레이드 다시 시도"
    MUTEX = "Local\\COMET_UPGRADE"

    def __init__(self, cfg, log, save_fn=None):
        super().__init__(cfg, log)
        self.save_fn = save_fn
        self._mutex = None

    def _run(self):
        from datetime import datetime
        if self._mutex is None:                  # 껐다 켜도 이 프로세스가 잡은 건 그대로 쓴다
            h = _named_mutex(self.MUTEX)
            if h is False:
                self.log("[upgrade] 다른 COMET 이 맡고 있음 — 여기선 안 돎")
                return
            self._mutex = h
        import upgrade
        hour = int(self.cfg.get("hour", 13))
        check = max(300, int(self.cfg.get("check_sec", 1800)))
        self.log(f"[upgrade] 가동 — 매일 {hour}시 이후 업그레이드 제안 · {check // 60}분마다 막힌 것 다시")
        while not self._stop.wait(check):
            try:
                upgrade.tick(log=self.log)
                today = datetime.now().strftime("%Y-%m-%d")
                if self.cfg.get("last_date") == today or datetime.now().hour < hour:
                    continue
                # 이슈 소스 생존 점검도 같이 — 소스 13개 부르는 데 수십 초라 따로 돈다(하루 한 번은 check_sources 가 지킨다)
                import issues
                threading.Thread(target=issues.check_sources, kwargs={"log": self.log},
                                 name="source-health", daemon=True).start()
                upgrade.propose(push=True, log=self.log)
                self.cfg["last_date"] = today     # 제안할 게 없어도 하루 한 번만 본다
                if self.save_fn:
                    self.save_fn()
            except Exception as e:
                self.log(f"[upgrade] 점검 오류: {e}")


def _named_mutex(name):
    """이 PC 에서 하나만 — 잡으면 핸들, 남이 잡고 있으면 False, 윈도우가 아니면 None(IssuesMode._single 과 같은 방식)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    h = k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:              # ERROR_ALREADY_EXISTS
        k32.CloseHandle(wintypes.HANDLE(h))
        return False
    return h


# ═══════════════════════════════════════════════════════════════
#  컨트롤러 — comet 이 토글/상태만 호출
# ═══════════════════════════════════════════════════════════════
class Autonomy:
    def __init__(self, respond_fn=None, speak_fn=None, log=None, gpu_busy_fn=None,
                 talk_respond_fn=None, cancel_fn=None):
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
                                  respond_fn=talk_respond_fn or (lambda t: respond_fn(t, speak=False)),
                                  speak_fn=speak_fn, cancel_fn=cancel_fn),
            "telegram":  TelegramMode(self.cfg["telegram"], self.log,
                                      respond_fn=(lambda t: respond_fn(t, speak=False)) if respond_fn else None),
            "upgrade":   UpgradeMode(self.cfg["upgrade"], self.log, save_fn=lambda: _save_cfg(self.cfg)),
        }

    # 저장된 enabled 모드 복원(데몬/콘솔 시작 시 호출)
    def restore(self):
        import headless
        for name, m in self.modes.items():
            if self.cfg[name].get("enabled"):
                if headless.HEADLESS and name in headless.NO_START:      # 로그인 전(세션 0) — 마이크 · 키보드 없음. 설정은 그대로
                    self.log(f"[자율모드] {name} — 로그인 전이라 안 켬(로그인하면 켜짐)")
                    continue
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
            if want and not m.running():           # running — 끄는 중인 스레드는 꺼진 걸로 본다
                m.rebase = True                    # 켤 때는 그동안 쌓인 소식을 쏟지 않는다
                self.enable("issues")
            elif not want and m.running():
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

    # 대화창 「소식 듣기」 버튼 — '알림 켜/끔' 과 같은 스위치(끄기 전 강도 기억·켤 때 베이스라인)를 그대로 쓴다
    def issues_state(self):
        c = self.cfg["issues"]
        return {"on": any(v != "끔" for v in c["modes"].values()), "modes": dict(c["modes"])}

    def issues_toggle(self, on):
        msg = self.issues_command("알림 켜" if on else "알림 끔")
        return dict(self.issues_state(), msg=msg)

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
                "'자율모드 brief 켜/꺼' · '자율모드 wake 켜/꺼' · '자율모드 talk 켜/꺼'(Alt 두 번 말걸기) · "
                "'자율모드 telegram 켜/꺼'(텔레그램으로 물으면 답)")


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
