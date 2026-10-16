# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 음성 모듈
#   - Ear  : 듣기 (Whisper, 로컬, CPU)
#   - Mouth: 말하기 (Edge-TTS, InJoon, 매끄럽게 정리 후 출력)
#  무거운 의존성(whisper/torch)은 실제로 쓸 때만 로딩한다.
# ═══════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import asyncio
import subprocess
import tempfile
import time
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
import edge_tts

# ── 말하기 설정 ──────────────────────────────────────────────
TTS_VOICE  = "ko-KR-SunHiNeural"
TTS_RATE   = "+10%"
TTS_GAIN   = 0.19          # 출력 음량 배수 (1.0=원본). 0.32는 너무 컸다 (2026-09-17)

# ── 아야카 목소리 (GPT-SoVITS 로 학습, 2026-09-17) ───────────
#   별도 폴더·별도 venv(A.B.B.Y.S/gpt-sovits)의 api_v2.py 서버를 HTTP 로 부른다.
#   서버가 안 떠 있으면 조용히 Edge-TTS(SunHi)로 떨어진다 — 말이 끊기지는 않는다.
TTS_ENGINE   = "ayaka"     # "ayaka" | "edge"
AYAKA_URL    = "http://127.0.0.1:9880/tts"
AYAKA_REF    = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                   os.path.abspath(__file__)))), "gpt-sovits", "mydata", "ayaka_ref.json")
AYAKA_BOOST  = 1.7         # 아야카 출력이 Edge 보다 4.7dB 작다(-22.4 vs -17.7 LUFS) → 체감 음량 맞춤

# ── 형체(화면 HUD) 설정 ──────────────────────────────────────
SHOW_ORB  = True           # 말할 때 orb.py 를 띄운다. 끄려면 False
ORB_HOP   = 0.02           # 포락선 한 칸 = 20ms (50Hz)
_DIR      = os.path.dirname(os.path.abspath(__file__))

# ── 듣기 설정 ────────────────────────────────────────────────
STT_MODEL    = "turbo"     # large-v3-turbo. small 은 한국어를 자주 틀렸다("뉴스 거야"→"뉴스드럼이야")
#   2026-09-18 실측(정답 있는 한국어 12클립, GPU): small 글자오류 3.7% · medium 2.5% · turbo 1.3%(클립당 0.17초)
#   GPU 에선 반정밀도로 올려 상주 1.5GB (불러오는 순간만 4.6GB)
STT_PROMPT   = "코멧, 뉴스, 알림, 주식봇, 코인봇, 할일, 일정, 브리핑"   # 자주 쓰는 말 — 인식 쪽으로 기울인다
SAMPLE_RATE  = 16000


# ═══════════════════════════════════════════════════════════════
#  말하기 전 텍스트 정리 — 자비스처럼 흐르게
#  (쉼표·마크다운·코드블록이 끊김을 만들기 때문에 다듬는다)
# ═══════════════════════════════════════════════════════════════
def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " 자세한 코드는 화면을 봐. ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", "", text)              # 인라인 코드
    text = re.sub(r"https?://[^\s)\]]+", " 링크 ", text)   # URL — 닫는 괄호는 안 먹는다(링크 표기 복구용)
    #  LaTeX — 페르소나로 막아도 모델이 샌다(2026-09-18 로그). 소리로는 "달러 케이 일" 로 읽혀 제일 심하다.
    #  자주 나오는 것만 말로 바꾸고, 나머지 \명령·수식 구분자는 지운다. ★$100 같은 금액은 살린다.
    for pat, word in ((r"\\times|\\cdot", " 곱하기 "), (r"\\div", " 나누기 "),
                      (r"\\pmod|\\bmod", " 나머지 "), (r"\\rightarrow|\\to\b|\\Rightarrow", " 는 "),
                      (r"\\leq|\\le\b", " 이하 "), (r"\\geq|\\ge\b", " 이상 "),
                      (r"\\neq|\\ne\b", " 아님 "), (r"\\approx", " 대략 ")):
        text = re.sub(pat, word, text)
    text = re.sub(r"\\[a-zA-Z]+\s*\{?|\\[,;!]", " ", text)   # 남은 \명령
    text = re.sub(r"\$([^$\d][^$]*?)\$", r" \1 ", text)      # $수식$ — 구분자만 벗긴다($100 은 안 건드림)
    text = re.sub(r"\\\(|\\\)|\\\[|\\\]", " ", text)         # \( \) \[ \]
    text = re.sub(r"[{}^]", " ", text)
    #  괄호는 기호만 지우면 안엣말이 그대로 읽힌다 — "일 퍼센트 움직였어 급등", "출처 일 사".
    #  ★2026-09-22 호윤: *"괄호에 있는건 안 읽는게 낫지 않냐, 사람같이 말하는것 같지가 않아."*
    #  괄호 안은 곁다리(부연·출처)라 소리로는 통째로 버린다. 링크 표기는 글자만 살린다.
    text = re.sub(r"\[([^\]]*)\]\s*\(\s*링크\s*\)", r"\1", text)   # [제목](링크) → 제목
    for _ in range(2):                                             # 한 겹 중첩까지
        text = re.sub(r"[(（][^()（）]{0,120}[)）]", " ", text)
        text = re.sub(r"\[[^\[\]]{0,120}\]", " ", text)
    text = re.sub(r"[#*_>~|\[\]()（）\-–—•·]", " ", text)  # 남은 짝 없는 괄호·마크다운 기호·불릿·대시
    text = text.replace("\n", " ")
    text = text.replace(",", " ").replace("、", " ")  # 쉼표 제거 → 끊김 완화
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ═══════════════════════════════════════════════════════════════
#  다른 소리 줄이기 (덕킹) — 코멧이 말하거나 내가 말하는 동안 다른 앱 볼륨을 낮춘다
#   윈도우 앱별 볼륨(pycaw). 겹쳐 불러도 되게 횟수를 센다(말걸기 창 안에서 말하기 등).
#   ★줄인 값을 파일에도 적어둔다 — 데몬이 도중에 죽으면 다음 시작 때 되돌린다.
#   ★되돌릴 때 지금 볼륨이 우리가 줄인 값 그대로일 때만 — 그 사이 내가 손댔으면 안 건드린다.
# ═══════════════════════════════════════════════════════════════
DUCK       = True
DUCK_LEVEL = 0.2          # 다른 앱을 원래 볼륨의 20% 로
_DUCK_FILE = os.path.join(_DIR, "duck_state.json")
#   ★안 줄이는 앱 — 맨 앞 창의 앱 + 이 목록(exe 이름, 소문자).
#     2026-09-23 호윤: 디코 통화 중에 상대 목소리까지 줄였다. "내가 선택한 창의 소리는 안 줄어들게".
#     게임하면서 통화하면 맨 앞은 게임이라 디코는 목록으로 따로 뺀다.
#     pid 가 아니라 이름으로 본다 — 크롬·디코는 창 프로세스와 소리 내는 프로세스가 다르다(이름은 같다).
DUCK_KEEP  = {"discord.exe"}
_OWN_EXE   = {"python.exe", "pythonw.exe"}   # 코멧 자기 창(말걸기 창·배너·형체) — 맨 앞이어도 사용자 창이 아니다


def _foreground_exe() -> str:
    """맨 앞 창의 exe 이름(소문자). 모르면 ''."""
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""


class _Ducker:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.count = 0
        self.saved = []             # [(session, 원래볼륨, 줄인볼륨)]
        self.front = ""             # 마지막으로 본 사용자 쪽 맨 앞 앱(코멧 창 제외)

    def remember_front(self) -> str:
        """지금 맨 앞 앱을 기억한다. 코멧 창이 앞이면 전에 기억한 것을 그대로 쓴다.
           말걸기 창은 뜨면서 맨 앞을 가져가므로, 창을 띄우기 전에 한 번 불러둔다."""
        exe = _foreground_exe()
        if exe and exe not in _OWN_EXE:
            self.front = exe
        return self.front

    @staticmethod
    def _sessions():
        import comtypes
        try:
            comtypes.CoInitialize()     # 데몬 스레드마다 COM 초기화가 필요하다
        except OSError:
            pass
        from pycaw.pycaw import AudioUtilities
        me = os.getpid()
        return [s for s in AudioUtilities.GetAllSessions()
                if s.Process and s.ProcessId not in (0, me)]

    @staticmethod
    def _fade(pairs, steps=6, dt=0.025):
        for i in range(1, steps + 1):
            for vol, a, b in pairs:
                try:
                    vol.SetMasterVolume(a + (b - a) * i / steps, None)
                except Exception:
                    pass
            time.sleep(dt)

    def on(self):
        if not DUCK:
            return
        with self.lock:
            self.count += 1
            if self.count > 1:
                return
            try:
                keep = DUCK_KEEP | {self.remember_front()}
                saved, pairs = [], []
                for s in self._sessions():
                    try:
                        if s.Process.name().lower() in keep:
                            continue
                    except Exception:
                        pass
                    v = s.SimpleAudioVolume
                    cur = v.GetMasterVolume()
                    if cur <= 0.01:
                        continue
                    low = round(cur * DUCK_LEVEL, 3)
                    saved.append((s, cur, low))
                    pairs.append((v, cur, low))
                self.saved = saved
                with open(_DUCK_FILE, "w", encoding="utf-8") as f:
                    json.dump([{"pid": s.ProcessId, "vol": c, "low": lo} for s, c, lo in saved], f)
                self._fade(pairs)
            except Exception as e:
                print(f"  [덕킹 오류] {e}")

    def off(self):
        if not DUCK:
            return
        with self.lock:
            if self.count == 0:
                return
            self.count -= 1
            if self.count > 0:
                return
            try:
                pairs = []
                for s, cur, low in self.saved:
                    v = s.SimpleAudioVolume
                    now = v.GetMasterVolume()
                    if abs(now - low) < 0.01:           # 그 사이 내가 바꿨으면 그대로 둔다
                        pairs.append((v, now, cur))
                self._fade(pairs)
            except Exception as e:
                print(f"  [덕킹 복구 오류] {e}")
            self.saved = []
            try:
                os.remove(_DUCK_FILE)
            except OSError:
                pass

    def recover(self):
        """지난번에 줄인 채로 죽었으면 되돌린다(데몬 시작 때 한 번)."""
        if not os.path.exists(_DUCK_FILE):
            return 0
        n = 0
        try:
            with open(_DUCK_FILE, encoding="utf-8") as f:
                rows = {r["pid"]: r for r in json.load(f)}
            for s in self._sessions():
                r = rows.get(s.ProcessId)
                v = s.SimpleAudioVolume
                if r and abs(v.GetMasterVolume() - r["low"]) < 0.01:
                    v.SetMasterVolume(r["vol"], None)
                    n += 1
        except Exception as e:
            print(f"  [덕킹 복구 오류] {e}")
        try:
            os.remove(_DUCK_FILE)
        except OSError:
            pass
        return n


ducker = _Ducker()


# ═══════════════════════════════════════════════════════════════
#  숫자 → 한글 읽기 (아야카용)
#   GPT-SoVITS 한국어 g2p 는 소수점·%·~ 를 제대로 못 읽는다 — "2.8%" 가 "이-" 로 나왔다(2026-09-18).
#   게다가 clean_for_speech 가 ~ · - 를 지우므로 그 전에 바꿔야 한다.
#   개수 단위(건·개·명…)는 고유어로 — "4건"을 "사건"으로 읽으면 事件 으로 들린다.
# ═══════════════════════════════════════════════════════════════
_SINO = "영일이삼사오육칠팔구"
_NATIVE_1 = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_10 = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
_COUNTERS = ("건", "개", "명", "시간", "시", "살", "번", "가지", "곳", "마리", "달", "군데", "사람", "대")


def _sino(n: int) -> str:
    if n == 0:
        return "영"
    units = ["", "십", "백", "천"]
    big = ["", "만", "억", "조", "경"]
    out, gi = [], 0
    while n > 0:
        chunk, n = n % 10000, n // 10000
        if chunk:
            s = ""
            for i in range(3, -1, -1):
                d = chunk // (10 ** i) % 10
                if d:
                    s += ("" if d == 1 and i > 0 else _SINO[d]) + units[i]
            if gi == 1 and s == "일":       # 10000 → 만 (일만 아님)
                s = ""
            out.append(s + big[gi])
        gi += 1
    return "".join(reversed(out))


def _native(n: int) -> str:
    if n == 20:
        return "스무"
    return _NATIVE_10[n // 10] + _NATIVE_1[n % 10]


def _read_int(digits: str, after: str) -> str:
    n = int(digits)
    if 0 < n < 100 and after.startswith(_COUNTERS) and not after.startswith(("개월", "대비", "달러")):
        return _native(n)
    return _sino(n)


def speak_numbers(text: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text)                         # 1,500 → 1500
    t = re.sub(r"(\d)\s*[~〜]\s*(?=[+\-]?\d)", r"\1에서 ", t)            # 3~4 → 3에서 4
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))\+(?=\d)", "플러스", t)
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))[-−](?=\d)", "마이너스", t)
    t = re.sub(r"\$\s?(\d[\d.]*)", r"\1달러", t)
    t = re.sub(r"%\s?p\b|%\s?포인트", "퍼센트포인트", t)
    t = t.replace("%", "퍼센트")

    # 소수는 붙여 읽는다(이점팔퍼센트) — 띄우면 끊겨 들린다. 뒤의 0은 안 읽는다(4.00 → 사, 3.50 → 삼점오)
    def dec(m):
        frac = m.group(2).rstrip("0")
        return _sino(int(m.group(1))) + ("점" + "".join(_SINO[int(c)] for c in frac) if frac else "")
    t = re.sub(r"(\d+)\.(\d+)", dec, t)
    t = re.sub(r"\d+", lambda m: _read_int(m.group(), t[m.end():m.end() + 2].lstrip()), t)
    return t


# ═══════════════════════════════════════════════════════════════
#  영어 → 한글 소리 (말하기 직전)
#   2026-09-23 호윤: 영어 발음이 이상하다, 한국어로 말하게 해줘.
#   아야카는 한국어로만 학습돼 "OpenAI" 같은 걸 이상하게 읽는다. 글자는 그대로 두고 소리만 바꾼다.
#   ① 사전  ② 대문자 약어는 알파벳 이름으로(GPT → 지피티)  ③ 나머지는 로컬 모델에게 한 번 묻고
#   hangul_cache.json 에 적어둔다 — 같은 단어는 다음부터 모델 안 거친다.
#   모델이 실패하면 그 단어는 그대로 둔다(말이 안 끊기는 게 먼저).
# ═══════════════════════════════════════════════════════════════
HANGUL_MODEL = "gemma4:12b"         # light 등급 — 게임모드에서도 쓰는 모델이라 게임 중에 올려도 된다
_HANGUL_FILE = os.path.join(_DIR, "hangul_cache.json")
_ABC = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                ["에이", "비", "씨", "디", "이", "에프", "지", "에이치", "아이", "제이", "케이", "엘", "엠",
                 "엔", "오", "피", "큐", "알", "에스", "티", "유", "브이", "더블유", "엑스", "와이", "제트"]))
_EN_WORDS = {           # 소문자 키 — 약어처럼 읽으면 안 되는 것, 모델이 틀리기 쉬운 고유명사
    "openai": "오픈에이아이", "chatgpt": "챗지피티", "nvidia": "엔비디아", "anthropic": "앤트로픽",
    "claude": "클로드", "gemini": "제미나이", "gemma": "젬마", "google": "구글", "apple": "애플",
    "microsoft": "마이크로소프트", "meta": "메타", "amazon": "아마존", "tesla": "테슬라",
    "samsung": "삼성", "intel": "인텔", "qualcomm": "퀄컴", "huawei": "화웨이", "xai": "엑스에이아이",
    "grok": "그록", "deepseek": "딥시크", "qwen": "큐웬", "llama": "라마", "mistral": "미스트랄",
    "astra": "아스트라", "copilot": "코파일럿", "github": "깃허브", "huggingface": "허깅페이스",
    "hugging": "허깅", "face": "페이스", "youtube": "유튜브", "discord": "디스코드", "iphone": "아이폰",
    "android": "안드로이드", "windows": "윈도우", "python": "파이썬", "bitcoin": "비트코인",
    "ethereum": "이더리움", "comet": "코멧", "rawcut": "로컷", "nasdaq": "나스닥", "kospi": "코스피",
    "kosdaq": "코스닥", "fed": "페드", "tsmc": "티에스엠씨", "sk": "에스케이", "lg": "엘지",
    "api": "에이피아이", "ai": "에이아이", "ok": "오케이", "vs": "대", "etf": "이티에프",
    "techmeme": "테크밈", "reddit": "레딧", "bluesky": "블루스카이", "mastodon": "마스토돈",
    "lobsters": "롭스터스", "arxiv": "아카이브", "blackwell": "블랙웰", "rubin": "루빈",
}
_hangul_lock = threading.Lock()
_hangul_cache = None
#  ★모델이 한 번 실패하면 한동안 안 묻는다 — 2026-10-01 로그에 '[한글 소리 실패] timed out' 이 두 번.
#    12b 가 대화 답을 만드는 동안엔 이 요청이 뒤에 줄 서서 20초를 꽉 채우고 실패한다 = 그 20초 동안 말이 안 나온다.
#    매 알림마다 20초씩 붙잡느니, 그동안은 모르는 영어를 그대로 읽게 둔다(사전·약어·캐시는 그대로 먹는다).
HANGUL_PAUSE = 600
_hangul_fail = 0.0
#    연결 거부(ollama 꺼짐)는 공용 표시(ollama_down.py)로 넓혔다 — 살아나면 10분 안 기다리고 바로 다시 묻는다.


def _ollama_down():
    try:
        import ollama_down
        return ollama_down.is_down()
    except ImportError:
        return False


def _load_hangul():
    global _hangul_cache
    if _hangul_cache is None:
        try:
            with open(_HANGUL_FILE, encoding="utf-8") as f:
                _hangul_cache = json.load(f)
        except Exception:
            _hangul_cache = {}
    return _hangul_cache


def _ask_hangul(words):
    """영어 단어들 → {단어: 한글 소리}. 실패하면 {} — 그리고 HANGUL_PAUSE 동안 안 묻는다."""
    global _hangul_fail
    try:
        import ollama
        r = ollama.Client(timeout=20).chat(
            model=HANGUL_MODEL, format="json", think=False, keep_alive="10m",
            options={"temperature": 0, "num_predict": 400},
            messages=[{"role": "user", "content":
                       "아래 영어 단어들을 한국 사람이 읽는 소리 그대로 한글로 적어라. 뜻 번역 말고 소리. "
                       "외래어 표기법을 따르고, 회사·제품·사람 이름은 한국에서 흔히 쓰는 표기로. "
                       'JSON 만: {"단어": "한글"}\n\n' + "\n".join(words)}])
        got = json.loads(r["message"].get("content") or "{}")
    except Exception as e:
        try:
            import ollama_down
            if ollama_down.mark(e):     # 꺼짐이면 공용 표시가 살아날 때까지 막는다 — 10분 쉬는 시간은 안 건다
                return {}
        except ImportError:
            pass
        _hangul_fail = time.time()
        print(f"  [한글 소리 실패] {str(e)[:60]} — {HANGUL_PAUSE // 60}분간 모델 안 부름")
        return {}
    out = {}
    for k, v in (got.items() if isinstance(got, dict) else []):
        v = str(v).strip()
        if k.lower() in words and re.fullmatch(r"[가-힣 ]{1,30}", v):   # 한글만 받는다
            out[k.lower()] = v
    return out


def _spell(word: str) -> str:
    return "".join(_ABC[c] for c in word.upper())


def speak_hangul(text: str) -> str:
    """문장 속 영어 단어를 한글 소리로. 한글 문장은 그대로."""
    tokens = set(re.findall(r"[A-Za-z]+", text))
    if not tokens:
        return text
    with _hangul_lock:
        cache = _load_hangul()
        unknown = sorted({w.lower() for w in tokens
                          if w.lower() not in _EN_WORDS and w.lower() not in cache
                          and not (w.isupper() and len(w) <= 4)
                          and not re.fullmatch(r"[A-Z]{2,4}s", w)})
        if unknown and time.time() - _hangul_fail > HANGUL_PAUSE and not _ollama_down():
            new = _ask_hangul(unknown[:40])
            if new:
                cache.update(new)
                try:
                    with open(_HANGUL_FILE, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False, indent=0)
                except OSError:
                    pass

    def one(m):
        w = m.group()
        low = w.lower()
        if low in _EN_WORDS:
            return _EN_WORDS[low]
        if w.isupper() and len(w) <= 4:              # GPT · AMD · CEO
            return _spell(w)
        if re.fullmatch(r"[A-Z]{2,4}s", w):         # GPUs → 지피유즈
            return _spell(w[:-1]) + "즈"
        return cache.get(low, w)
    return re.sub(r"[A-Za-z]+", one, text)


# ═══════════════════════════════════════════════════════════════
#  Mouth — Edge-TTS
# ═══════════════════════════════════════════════════════════════
def synth_mp3(text: str, voice: str = TTS_VOICE, rate: str = TTS_RATE) -> bytes:
    """텍스트 → InJoon mp3 바이트. 데몬이 폰으로 음성을 쏠 때 쓴다(로컬 재생 X)."""
    clean = speak_hangul(clean_for_speech(text))
    if not clean:
        return b""

    async def _go():
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        try:
            await edge_tts.Communicate(clean, voice=voice, rate=rate).save(path)
            with open(path, "rb") as fp:
                return fp.read()
        finally:
            if os.path.exists(path):
                os.remove(path)

    return asyncio.run(_go())


def envelope(data, sr, hop: float = ORB_HOP):
    """재생할 PCM → RMS 열(0~1). 형체가 이걸 읽어서 실제 소리에 맞춰 움직인다.
    스피커 루프백을 캡처할 필요가 없다 — 어차피 재생 직전에 파형을 손에 들고 있다."""
    x = data.mean(axis=1) if getattr(data, "ndim", 1) > 1 else data
    n = max(1, int(sr * hop))
    m = len(x) // n
    if m < 1:
        return []
    rms = np.sqrt((x[:m * n].reshape(m, n).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return []
    return [round(v, 3) for v in (rms / peak)]


def show_orb(data, sr, wait: float = 2.5):
    """형체를 띄우고 첫 프레임이 올라올 때까지만 잠깐 기다린다(소리와 시작을 맞추려고).
    실패해도 말하기는 그대로 진행한다 — 장식이 본업을 막으면 안 된다."""
    try:
        env = envelope(data, sr)
        if not env:
            return
        d = tempfile.mkdtemp(prefix="comet_orb_")
        ep, rp = os.path.join(d, "env.json"), os.path.join(d, "ready")
        with open(ep, "w", encoding="utf-8") as f:
            json.dump({"hop": ORB_HOP, "env": env}, f)
        py = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(py):
            py = sys.executable
        subprocess.Popen([py, os.path.join(_DIR, "orb.py"), "--env", ep, "--ready", rp],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dead = time.time() + wait
        while time.time() < dead and not os.path.exists(rp):
            time.sleep(0.04)
    except Exception as e:
        print(f"  [형체 오류] {e}")


_AYAKA_SPAWNED = False
#  ★서버가 '살아 있는데 고장'인 경우 — 2026-10-07 확인.
#    10/01 합성 도중 GPU 메모리가 모자라 'CUDA error: out of memory' 가 난 뒤로, 9/28 부터 떠 있던
#    서버(따로 사는 프로세스)가 모든 요청에 즉시 400 을 줬다. 연결 거부가 아니라서 아래
#    start_ayaka_server() 가 한 번도 안 불렸고, 데몬을 다시 켜도 서버는 그대로라 10/01~10/07 목소리가 전부 Edge 였다.
#    → 400 본문에 CUDA 오류가 보이면 서버 자체 명령(/control?command=exit)으로 내리고 새로 띄운다.
AYAKA_SELF_HEAL = True     # False 면 고장 나도 손 안 대고 Edge 로만 버틴다
AYAKA_HEAL_GAP  = 600      # 다시 띄우기는 10분에 한 번까지 — VRAM 이 정말 모자란 동안엔 띄워도 또 굳는다
_AYAKA_HEALED   = 0.0


def _ayaka_port_open(timeout=0.3):
    """아야카 서버 포트가 열려 있나(떠 있나). 굳었는지는 모른다 — 그건 합성 요청 답으로 본다."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(AYAKA_URL)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def start_ayaka_server(force=False):
    """아야카 서버를 창 없이 띄운다(안 기다림). 한 프로세스에서 한 번만 시도(force=True 는 고장 복구용).
    모델 올리는 데 ~10초 — 그동안의 말은 Edge 로 나가고, 다음 말부터 아야카."""
    global _AYAKA_SPAWNED
    if _AYAKA_SPAWNED and not force:
        return
    import headless
    if headless.HEADLESS:            # 로그인 전(세션 0) — 여기서 띄운 서버가 남으면 로그인 뒤 목소리가 안 들리는 세션 0 으로 나간다
        return
    _AYAKA_SPAWNED = True
    root = os.path.dirname(os.path.dirname(AYAKA_REF))          # .../gpt-sovits
    py = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(py):
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               PYTHONPATH=root + os.pathsep + os.path.join(root, "GPT_SoVITS"))
    log = open(os.path.join(root, "mydata", "server.log"), "a", encoding="utf-8")
    subprocess.Popen([py, "api_v2.py", "-a", "127.0.0.1", "-p", "9880",
                      "-c", "mydata/ayaka_infer.yaml"],
                     cwd=root, env=env, stdout=log, stderr=log,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("  [아야카] 서버 띄우는 중 — 이번 말은 Edge 로")
    _ayaka_touch()                 # 띄워 놓고 안 쓰면 그것도 내린다


def warm_ayaka(log=print):
    """데몬 뜰 때 미리 띄운다. 안 그러면 첫 말이 '연결 거부'를 보고서야 띄우느라 그 말은 Edge 로 샜다
    (9/22·9/28 로그 — 데몬 켜고 첫 알림이 매번 Edge). 이미 떠 있으면 손 안 댄다(굳었는지는 첫 말에서 본다)."""
    if TTS_ENGINE != "ayaka" or _ayaka_port_open():
        return
    start_ayaka_server()

    def wait():
        dead = time.time() + 90
        while time.time() < dead:
            if _ayaka_port_open():
                log("  [아야카] 목소리 준비 완료")
                return
            time.sleep(1)
        log("  [아야카] 90초 안에 안 떴다 — 말은 Edge 로 나간다 (gpt-sovits/mydata/server.log 확인)")
    threading.Thread(target=wait, name="ayaka-warm", daemon=True).start()


def heal_ayaka(why=""):
    """서버는 떠 있는데 GPU 오류로 굳었다 → 내리고 새로 띄운다. 뒤에서 돌고 안 기다린다(지금 말은 Edge 로).
    밖에서 프로세스를 죽이지 않는다 — api_v2 가 원래 갖고 있는 /control?command=exit 로 서버가 스스로 내려간다."""
    global _AYAKA_HEALED, _AYAKA_SPAWNED
    if not AYAKA_SELF_HEAL or time.time() - _AYAKA_HEALED < AYAKA_HEAL_GAP:
        return False
    _AYAKA_HEALED = time.time()
    _AYAKA_SPAWNED = True          # 내렸다 띄우는 사이 다른 말이 '연결 거부'를 보고 하나 더 띄우지 않게

    def run():
        import urllib.request
        try:
            urllib.request.urlopen(AYAKA_URL.rsplit("/", 1)[0] + "/control?command=exit", timeout=5)
        except Exception:
            pass                   # 서버가 그 자리에서 꺼지며 연결을 끊는다 — 오류가 나는 게 정상
        dead = time.time() + 15
        while time.time() < dead and _ayaka_port_open():
            time.sleep(0.3)
        if _ayaka_port_open():
            print("  [아야카] 굳은 서버가 안 내려갔다 — 계속 Edge 로 (아야카 서버 프로세스를 직접 꺼야 한다)")
            return
        start_ayaka_server(force=True)

    print(f"  [아야카] 서버가 GPU 오류로 굳어 있다({why.splitlines()[0][:40] if why else '?'}) "
          "→ 내리고 다시 띄운다 — 그동안 Edge 로")
    threading.Thread(target=run, name="ayaka-heal", daemon=True).start()
    return True


def _ayaka_reason(err):
    """HTTP 오류의 진짜 이유. 서버는 {"message": "tts failed", "Exception": "..."} 로 준다 —
    예전엔 'HTTP Error 400: Bad Request' 만 찍혀서 서버가 굳은 걸 몰랐다."""
    try:
        body = json.loads(err.read().decode("utf-8", "replace"))
        return str(body.get("Exception") or body.get("message") or "").strip()
    except Exception:
        return ""


# ── 목소리 먼저 — 아야카 합성 직전에 GPU 자리를 본다 (2026-10-07) ─────────
#  ★굳는 진짜 원인: 16GB 를 LLM·Whisper·아야카가 나눠 쓴다. 코멧이 gemma4:12b(8.3GB, keep_alive 10분)나
#    Q3 26b(13GB)를 올려 둔 채 아야카가 합성하면 'CUDA error: out of memory' 로 서버가 굳는다.
#    10/07 07:18 새 데몬에서 '알림 테스트' 때 여유 176MB — 다시 띄워도 또 굳는 상태였다(호윤: "TTS 개에반데").
#  → 합성 전에 여유를 재고, 모자라면 ollama 에 올라 있는 모델을 내린다(keep_alive=0 — 프로세스는 안 건드림,
#    다음에 필요하면 저절로 다시 올라온다. 생성 중인 모델은 그 생성이 끝난 뒤 내려간다).
#    그래도 모자라면 이번 말은 아야카를 부르지 않고 Edge — 모자란 채로 부르면 서버가 굳어서 이후 말이 다 Edge 가 된다.
AYAKA_NEED_MB = 1500       # 아야카가 한 문장 합성에 더 쓰는 몫(여유가 이보다 적으면 자리를 낸다)
OLLAMA_URL = "http://127.0.0.1:11434"


def _vram_free_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None                # 못 재면 막지 않는다(예전처럼 부른다)


def _room_for_ayaka():
    """True = 아야카를 불러도 된다. 자리가 모자라면 ollama 모델을 내려 자리를 만든다."""
    import urllib.request
    free = _vram_free_mb()
    if free is None or free >= AYAKA_NEED_MB:
        return True
    names = []
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=2) as r:
            names = [m.get("name") for m in json.loads(r.read()).get("models", []) if m.get("name")]
        for n in names:
            req = urllib.request.Request(OLLAMA_URL + "/api/generate",
                                         data=json.dumps({"model": n, "keep_alive": 0}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass
    for _ in range(10):            # 내려가는 데 1~2초
        time.sleep(0.3)
        after = _vram_free_mb()
        if after is None or after >= AYAKA_NEED_MB:
            print(f"  [아야카] GPU 여유 {free}MB → {after}MB — 모델 내려 자리 냄({', '.join(names) or '없음'})")
            return True
    print(f"  [아야카] GPU 여유 {free}MB — 자리 못 냄(생성 중인 모델) → 이번 말은 Edge (굳지 않게)")
    return False


# ── 안 쓰면 내린다 (2026-10-17 · 호윤 「아야카 서버도 그렇게 바꿔줘」 — 말걸기 Whisper 와 같은 방식) ──
#   서버는 떠 있기만 해도 약 6GB(대부분 GPU 몫 예약 · 실제 램은 0.2GB 안팎)를 잡는다.
#   데몬 켤 때 미리 띄우던 것(warm_ayaka)을 빼고, 말할 때 없으면 띄우고(그 말은 Edge),
#   마지막 말 뒤 AYAKA_IDLE_STOP 초 안 쓰면 /control?command=exit 로 내린다. 0 이면 안 내림.
#   대신 한동안 쉬다 처음 하는 말은 Edge 목소리.
AYAKA_IDLE_STOP = 600
_ayaka_last = 0.0
_ayaka_gate = threading.Lock()     # 합성 중엔 안 내리고, 내리는 중엔 합성 안 함
_ayaka_timer_lock = threading.Lock()
_ayaka_timer = None


def _ayaka_touch():
    global _ayaka_last
    _ayaka_last = time.time()
    _ayaka_idle_arm(AYAKA_IDLE_STOP)


def _ayaka_idle_arm(sec):
    global _ayaka_timer
    if AYAKA_IDLE_STOP <= 0:
        return
    with _ayaka_timer_lock:
        if _ayaka_timer:
            _ayaka_timer.cancel()
        _ayaka_timer = threading.Timer(sec, _ayaka_idle_stop)
        _ayaka_timer.daemon = True
        _ayaka_timer.start()


def _ayaka_idle_stop():
    global _AYAKA_SPAWNED
    left = AYAKA_IDLE_STOP - (time.time() - _ayaka_last)
    if left > 1:
        _ayaka_idle_arm(left)
        return
    if not _ayaka_gate.acquire(blocking=False):      # 지금 합성 중 — 조금 뒤 다시
        _ayaka_idle_arm(30)
        return
    try:
        if not _ayaka_port_open():
            _AYAKA_SPAWNED = False
            return
        import urllib.request
        try:
            urllib.request.urlopen(AYAKA_URL.rsplit("/", 1)[0] + "/control?command=exit", timeout=5)
        except Exception:
            pass                   # 서버가 그 자리에서 꺼지며 연결을 끊는다 — 오류가 나는 게 정상
        dead = time.time() + 15
        while time.time() < dead and _ayaka_port_open():
            time.sleep(0.3)
        _AYAKA_SPAWNED = False     # 다음 말이 '연결 거부'를 보면 다시 띄운다
        print(f"  [아야카] {AYAKA_IDLE_STOP // 60}분 안 써서 서버 내림 — 다음 말 때 다시 띄움(그 말은 Edge)")
    finally:
        _ayaka_gate.release()


def ayaka_idle_watch():
    """데몬 켤 때 — 미리 띄우지 않는다. 옛 데몬이 띄워 둔 서버가 떠 있으면 그것도 안 쓰면 내리게 건다."""
    if TTS_ENGINE == "ayaka" and _ayaka_port_open():
        _ayaka_touch()


def synth_ayaka(text: str, timeout: float = 30.0):
    """아야카 서버에 합성 요청 → (파형, 샘플레이트). 서버가 없거나 실패하면 None."""
    with _ayaka_gate:
        got = _synth_ayaka(text, timeout)
    if got is not None:
        _ayaka_touch()
    return got


def _synth_ayaka(text: str, timeout: float = 30.0):
    import io
    import urllib.error
    import urllib.request
    if _ayaka_port_open() and not _room_for_ayaka():
        return None
    try:
        with open(AYAKA_REF, encoding="utf-8") as f:
            ref = json.load(f)
        body = json.dumps({
            "text": text, "text_lang": "ko",
            "ref_audio_path": ref["ref_audio_path"], "prompt_text": ref["prompt_text"],
            "prompt_lang": "ko", "text_split_method": "cut5",
            "media_type": "wav", "parallel_infer": False,   # 짧은 문장은 끄는 쪽이 빠르다(실측 1.7~2.0초)
        }).encode("utf-8")
        req = urllib.request.Request(AYAKA_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data, sr = sf.read(io.BytesIO(r.read()))
        #  합성 도중 오류(GPU 메모리 부족 등)가 나면 서버는 200 + 1초 무음을 준다(TTS.run 의 except).
        #  그대로 틀면 알림이 소리 없이 지나간다 — 10/01 15:26 알림이 그랬다. 무음이면 Edge 로.
        if not np.any(data):
            print("  [아야카 무음 → Edge 로] 서버가 합성 중 오류를 삼켰다 — gpt-sovits/mydata/server.log")
            return None
        return data, sr
    except urllib.error.HTTPError as e:
        why = _ayaka_reason(e) or str(e)
        print(f"  [아야카 실패 → Edge 로] {e.code} {why.splitlines()[0][:60] if why else ''}")
        if "CUDA" in why or "out of memory" in why:
            heal_ayaka(why)
        return None
    except Exception as e:
        print(f"  [아야카 없음 → Edge 로] {str(e)[:60]}")
        if "10061" in str(e) or "refused" in str(e).lower():   # 서버가 아예 없을 때만 띄운다
            start_ayaka_server()
        return None


_hush = threading.Event()      # 그만(Esc) — 읽는 중이면 끊고, 합성 중이면 안 읽는다


# ── 말하는 차례 (한 번에 하나) ──────────────────────────────────
#   ★sd.play 는 출력 장치를 하나만 잡는다. 말하는 중에 딴 스레드가 또 speak 하면
#     앞말이 통째로 끊기고, 끊긴 쪽의 sd.wait() 이 즉시 풀리면서 ducker.off() 까지
#     먼저 터진다 — 뒷말이 나오는데 다른 앱 소리가 되살아난다.
#     입구가 여럿이라(대화 답·이슈 알림·아침 브리핑·데몬) 실제로 겹친다.
#     2026-09-18 호윤: "방금 tts 이상하게 재생됐어, 작동중에 새로운 소식오면 이러나".
#   부르는 쪽은 자기 차례가 끝날 때까지 기다린다 — 말하고 나서 듣는 말걸기 창이
#   비동기로 바뀌면 제 목소리를 녹음해버린다. 그래서 '띄우고 잊기'가 아니라 줄서기다.
_say_lock = threading.Lock()
_waiting = 0                   # 줄 서 있는 수
_waiting_lock = threading.Lock()
MAX_WAITING = 3                # 이보다 밀리면 묵은 알림은 버린다(뒤늦게 우르르 읽지 않게)
_stop_at = 0.0                 # 마지막으로 '그만' 한 시각 — 그 전에 줄 선 것은 취소


def stop_speaking():
    global _stop_at
    _stop_at = time.time()     # 이미 줄 서 있던 것들도 같이 취소
    _hush.set()
    try:
        sd.stop()
    except Exception:
        pass


# ── Esc = 그만 — 말하는 동안은 어느 창이 앞에 있든 먹는다 ──────────
#   ★2026-10-07 호윤: "내가 esc 눌렀는데 왜 음성이 계속 재생되는거지?"
#     Esc 는 말걸기 창(ask_box)이 키보드를 쥐고 있을 때만 먹었다. 이슈 알림·아침 할 일·대화창 답·
#     회의 결과는 끊을 길이 없었고, 형체(orb.py)는 Esc 에 저만 닫혀서 알아들은 것처럼 보였다.
#   말하는 차례(합성~재생)를 쥔 동안만 저수준 키보드 훅을 건다 — 말걸기(Alt 두 번)와 같은 방식.
#   키는 막지 않는다(보기만) — 그 Esc 는 원래 창에도 그대로 간다. 말 안 할 땐 훅 자체가 없다.
ESC_STOPS = True
ESC_GRACE = 2.0      # Esc 직후 이 안에 새로 들어온 말도 버린다 — '뉴스'·'알림 다시'는 분야별로 잇따라 읽어서
                     #   앞 건만 끊으면 곧바로 다음 건이 나온다(= 안 멈춘 것처럼 들린다)
VK_ESCAPE = 0x1B


def _esc_fires(vk, flags, wparam):
    """훅에 들어온 키 하나가 '그만' 인가. 훅과 떼어 둔 판정(테스트하려고 — autonomy.DoubleTap 처럼)."""
    return (wparam in (0x100, 0x104)          # WM_KEYDOWN / WM_SYSKEYDOWN
            and vk == VK_ESCAPE
            and not flags & 0x10              # 프로그램이 흉내 낸 키(LLKHF_INJECTED)는 무시 — 말걸기 훅과 같은 규칙
            and not _hush.is_set())           # 이미 멈췄으면(누르고 있어 자동반복) 또 안 한다


def _esc_pressed():
    print("  [Esc] 말하던 것 멈춤")
    stop_speaking()


class _EscWatch:
    """with _EscWatch(): ... — 그 사이 Esc 가 눌리면 stop_speaking(). 윈도우 전용, 훅이 실패해도 말하기는 그대로."""

    def __init__(self):
        self._done = threading.Event()
        self._thread = None
        self._tid = 0

    def __enter__(self):
        if ESC_STOPS and os.name == "nt":
            ready = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(ready,), name="tts-esc", daemon=True)
            self._thread.start()
            ready.wait(1.0)
        return self

    def __exit__(self, *exc):
        self._done.set()
        if self._thread is not None:
            if self._tid:
                try:
                    import ctypes
                    ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT — 바로 깨운다
                except Exception:
                    pass
            self._thread.join(1.0)
        return False

    def _run(self, ready):
        try:
            import ctypes
            from ctypes import wintypes
            #  ★windll.user32 를 그대로 쓰지 않는다 — 함수 argtypes 는 프로세스 전체가 같이 쓰는 값이라
            #    autonomy.TalkMode 의 훅 선언과 엉킨다. 따로 불러온 사본에만 선언한다.
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            LRESULT = ctypes.c_ssize_t
            PROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

            class KBD(ctypes.Structure):
                _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                            ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_size_t)]

            u32.SetWindowsHookExW.restype = wintypes.HHOOK
            u32.SetWindowsHookExW.argtypes = (ctypes.c_int, PROC, wintypes.HINSTANCE, wintypes.DWORD)
            u32.CallNextHookEx.restype = LRESULT
            u32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            u32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
            u32.SetTimer.restype = ctypes.c_size_t
            u32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
            u32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)
            u32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)

            def on_key(code, wparam, lparam):
                try:
                    if code == 0:
                        kb = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents
                        if _esc_fires(kb.vkCode, kb.flags, wparam):
                            # 훅 안에서 오래 머물면 PC 전체 키 입력이 굳는다 — 끊는 건 딴 스레드에서
                            threading.Thread(target=_esc_pressed, name="tts-esc-stop", daemon=True).start()
                except Exception:
                    pass
                return u32.CallNextHookEx(None, code, wparam, lparam)

            proc = PROC(on_key)                  # 참조를 쥐고 있어야 GC 가 콜백을 안 치운다
            msg = wintypes.MSG()
            u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # 메시지 큐부터 만든다(WM_QUIT 을 받을 수 있게)
            self._tid = k32.GetCurrentThreadId()
            hook = u32.SetWindowsHookExW(13, proc, None, 0)      # WH_KEYBOARD_LL
            if not hook:
                print(f"  [Esc 훅 실패] 오류 {ctypes.get_last_error()} — 이번 말은 Esc 로 못 끊는다")
                return
            timer = u32.SetTimer(None, 0, 100, None)   # 0.1초마다 깨서 '말 끝났나' 본다 — WM_QUIT 을 놓쳐도 안 남게
            try:
                ready.set()
                while not self._done.is_set() and u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    pass
            finally:
                if timer:
                    u32.KillTimer(None, timer)
                u32.UnhookWindowsHookEx(hook)
        except Exception as e:
            print(f"  [Esc 훅 오류] {e}")
        finally:
            ready.set()


class Mouth:
    def __init__(self, voice: str = TTS_VOICE, rate: str = TTS_RATE, gain: float = TTS_GAIN):
        self.voice = voice
        self.rate  = rate
        self.gain  = gain

    def speak(self, text: str, drop_if_busy: bool = False, on_start=None):
        """차례를 기다렸다가 읽는다. 읽었으면 True.
           drop_if_busy=True(이슈 알림 같은 뒷전 소식)면 줄이 길 때 그냥 버린다 —
           묵은 뉴스를 몇 분 뒤에 우르르 읽어주는 게 더 이상하다.
           Esc(그만) 전에 들어왔거나 Esc 직후(ESC_GRACE) 이어 들어온 말은 안 읽는다.
           on_start() = 합성이 끝나고 소리 나기 직전에 한 번(이슈 배너를 목소리와 같이 띄우려고).
           못 읽으면 안 불린다 — 그 뒤처리는 부른 쪽 몫."""
        global _waiting
        import headless
        if headless.HEADLESS:                # 로그인 전(세션 0) — 스피커 없음. 못 읽은 걸로
            return False
        called_at = time.time()             # ★정리 전에 잰다 — 한글 소리 찾기가 모델을 부르면 수 초 걸려
                                             #   'Esc 전에 온 말' 이 'Esc 뒤에 온 말' 로 보였다
        if TTS_ENGINE == "ayaka":
            text = speak_numbers(text)       # clean_for_speech 가 ~·- 를 지우기 전에
        clean = speak_hangul(clean_for_speech(text))   # 정리 뒤에 — 링크·코드는 이미 빠져 있다
        if not clean:
            return False

        with _waiting_lock:
            if drop_if_busy and _waiting >= MAX_WAITING:
                return False
            _waiting += 1
        try:
            with _say_lock:                  # ★소리내는 구간은 한 번에 하나
                if called_at < _stop_at + ESC_GRACE:   # 기다리는 사이에 '그만' — 또는 그 직후 이어 온 말
                    return False
                _hush.clear()
                try:
                    with _EscWatch():        # 합성~재생 동안 Esc 를 어느 창에서든 받는다
                        asyncio.run(self._speak(clean, on_start))
                except Exception as e:
                    print(f"  [TTS 오류] {e}")
                    return False
        finally:
            with _waiting_lock:
                _waiting -= 1
        return True

    async def _speak(self, text: str, on_start=None):
        path = None
        try:
            got = synth_ayaka(text) if TTS_ENGINE == "ayaka" else None
            if got is not None:
                data, sr = got
                data = data * self.gain * AYAKA_BOOST
            else:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    path = f.name
                await edge_tts.Communicate(text, voice=self.voice, rate=self.rate).save(path)
                data, sr = sf.read(path)
                data = data * self.gain          # 음량 조절
            if _hush.is_set():
                return
            ducker.on()                      # 다른 앱 소리 낮추고
            try:
                if on_start:                 # 소리와 같이 보일 것(이슈 배너·대화창) — 형체와 같이 띄운다
                    try:
                        on_start()
                    except Exception as e:
                        print(f"  [말 시작 알림 오류] {e}")
                if SHOW_ORB:
                    show_orb(data, sr)       # 형체 먼저 띄우고(첫 프레임까지 대기)
                if not _hush.is_set():       # 형체 뜨길 기다리는 사이(최대 2.5초)에 Esc 가 왔으면 안 튼다
                    sd.play(data, sr)
                    if _hush.is_set():       # play 와 엇갈린 '그만' — 그쪽 sd.stop() 은 앞 스트림에 갔다
                        sd.stop()
                    sd.wait()
            finally:
                ducker.off()
        finally:
            if path and os.path.exists(path):
                os.remove(path)


# ═══════════════════════════════════════════════════════════════
#  Ear — Whisper STT (마이크 녹음 + 무음 자동 종료)
# ═══════════════════════════════════════════════════════════════
class Ear:
    def __init__(self, model: str = STT_MODEL, sample_rate: int = SAMPLE_RATE):
        self.model_name  = model
        self.sample_rate = sample_rate
        self._model      = None
        self.last_stats  = {}

    def _load(self):
        if self._model is None:
            import whisper
            print(f"  [STT] Whisper '{self.model_name}' 로딩 중... (처음 한 번)")
            import torch
            self._cuda = torch.cuda.is_available()
            m = whisper.load_model(self.model_name, device="cuda" if self._cuda else "cpu")
            if self._cuda:
                m = m.half()
                for mod in m.modules():             # whisper 의 LayerNorm 은 float 가중치만 받는다
                    if isinstance(mod, torch.nn.LayerNorm):
                        mod.float()
            self._model = m
            print("  [STT] 준비 완료")

    def listen(self, stop_fn=None, start_timeout=None) -> str:
        """stop_fn() 이 참이 되면 바로 멈추고 "" (말걸기 창에서 타이핑을 시작했을 때).
        start_timeout 초 안에 말이 시작 안 되면 "" — 30초 내내 붙잡고 있지 않게."""
        self._load()
        audio = self._record(stop_fn=stop_fn, start_timeout=start_timeout)
        if self.last_stats.get("stopped") or not self.last_stats.get("started"):
            return ""
        if audio.size < self.sample_rate // 2:   # 0.5초 미만 = 무시
            return ""
        # 마이크 입력이 작으면(레벨·게인이 내려가 있으면) Whisper 가 문장을 뭉갠다 — 키워서 넣는다.
        # 배수는 20배까지만: 진짜 무음을 20배 키워 봐야 잡음만 커진다.
        peak = float(np.abs(audio).max())
        if 0 < peak < 0.3:
            gain = min(20.0, 0.3 / peak)
            audio = audio * gain
            self.last_stats["gain"] = round(gain, 1)
        result = self._model.transcribe(audio, fp16=getattr(self, "_cuda", False), language="ko",
                                        initial_prompt=STT_PROMPT)
        return result["text"].strip()

    # 문턱은 고정값이 아니라 '지금 이 방의 소음' 기준으로 잡는다.
    #   고정 0.015 는 조용한 방에서 작게 말하면 시작조차 못 하고(말해도 무음 취급),
    #   시끄러운 방에선 말을 멈춰도 계속 소리가 있어 안 끝났다.
    NOISE_START = 3.0      # 바닥소음의 이 배를 넘으면 '말 시작'
    NOISE_STOP  = 2.0      # 이 배 밑으로 내려가면 '조용'  (시작보다 낮게 = 말꼬리를 안 자름)
    PEAK_STOP   = 0.12     # 또는 지금까지 제일 큰 소리의 12% 밑 (소음이 커도 끝을 알아채게)
    FLOOR       = 0.0025   # 아무리 조용한 방이어도 이 밑은 소리로 안 본다
    WARMUP      = 3        # 마이크가 열리는 첫 칸들은 값이 0 에 가깝다 — 소음 추정에서 뺀다

    def _record(self, max_seconds=30, silence_duration=1.2, min_speech=0.4,
                stop_fn=None, start_timeout=None) -> np.ndarray:
        """끊김 없는 연속 녹음(InputStream) + 방 소음에 맞춘 자동 종료.

        ★예전엔 0.1초마다 sd.rec 로 스트림을 새로 열었다 — 칸과 칸 사이가 통째로 빠져
          15초를 받는 데 22.6초가 걸렸다(=말의 1/3 유실, 받아쓴 문장이 깨지던 원인).
          이제 콜백이 모든 샘플을 큐에 담고, 판정만 0.1초 단위로 본다.
        """
        import queue
        sr    = self.sample_rate
        chunk = int(sr * 0.1)
        q = queue.Queue()

        print("\n🎙  말해봐 (멈추면 자동 종료)...")
        frames, silent, spoke, started = [], 0, 0, False
        noise, peak = 0.0, 0.0
        hist = []                        # 최근 칸들의 크기 — 바닥소음을 계속 다시 잡는다
        self.last_stats = {"started": False, "stopped": False, "max_rms": 0.0, "sec": 0.0}
        silence_limit = int(silence_duration / 0.1)
        speech_min    = int(min_speech / 0.1)
        t_end   = time.time() + max_seconds
        t_start = time.time() + start_timeout if start_timeout else None

        with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=chunk,
                            callback=lambda indata, n_, t_, s_: q.put(indata[:, 0].copy())):
            while time.time() < t_end:
                if stop_fn and stop_fn():
                    self.last_stats["stopped"] = True
                    break
                if t_start and not started and time.time() > t_start:
                    break
                try:
                    block = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                frames.append(block)
                rms = float(np.sqrt(np.mean(block ** 2)))
                self.last_stats["max_rms"] = max(self.last_stats["max_rms"], round(rms, 4))
                peak = max(peak, rms)
                hist.append(rms)
                # 바닥소음 = 최근 5초 중 조용한 쪽 20% 지점. 말하는 중에도 숨 쉬는 틈이 여기 잡힌다.
                # 앞 WARMUP 칸(마이크 여는 중 = 거의 0)은 빼야 문턱이 바닥에 붙지 않는다.
                usable = hist[self.WARMUP:]
                if len(usable) >= 5:
                    noise = float(np.percentile(usable[-50:], 20))

                if not started:
                    # 소음 추정(0.8초)이 서기 전엔 시작 판정을 하지 않는다 —
                    # 문턱이 바닥에 붙어 잡음에 '시작'이 걸리면 start_timeout 이 풀려 30초를 붙잡는다.
                    if len(usable) >= 5 and rms > max(self.FLOOR, noise * self.NOISE_START):
                        started = True
                        spoke += 1
                        print("🔴 녹음 중...", end="\r")
                    continue

                gate = max(self.FLOOR, noise * self.NOISE_STOP, peak * self.PEAK_STOP)
                if rms > gate:
                    silent = 0
                    spoke += 1
                else:
                    silent += 1
                    if spoke >= speech_min and silent >= silence_limit:
                        break

        self.last_stats.update(started=started, sec=round(len(frames) * 0.1, 1),
                               noise=round(noise or 0, 4), peak=round(peak, 4))
        print("⏹  인식 중...        ")
        return np.concatenate(frames) if frames else np.zeros(1, dtype=np.float32)
