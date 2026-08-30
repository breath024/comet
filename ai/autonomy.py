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
                "'자율모드 brief 켜/꺼' · '자율모드 wake 켜/꺼'")


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
