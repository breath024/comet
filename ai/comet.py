# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET — Cognitive Operation Management & Electronic Transmission
#  A.B.B.Y.S 프로젝트의 상주 관리자 AI (v0 · 텍스트 콘솔)
#
#  구조:
#   - 문지기 라우터: gemma4:12b 가 매 입력의 난이도를 판정
#   - 3단 모델:  light=gemma4:12b / medium=gemma4:26b / heavy=gemma4:26b(=medium)
#   - 호흡(대기): 등급별 keep_alive 로 무거운 모델은 곧 VRAM 에서 증발
#  의존성: ollama (파이썬) 하나. 음성/STT 는 다음 층에서 붙임.
# ═══════════════════════════════════════════════════════════════
import os
import sys
import re
import time
import json
import subprocess
import itertools
import ollama

# ── 한글 윈도우 콘솔 UTF-8 강제 ──────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════
#  설정값
# ═══════════════════════════════════════════════════════════════
GATEKEEPER = "gemma4:12b"         # 문지기 = 항상 가벼운 모델

# 등급별 모델 + keep_alive(이만큼 안 쓰면 VRAM 에서 자동 언로드)
#  ★2026-09-22 — 무거운 등급을 Q4(18GB) 에서 **Q3_K_M 양자화(13GB)** 로 내렸다.
#  왜: Q4 26b 는 16GB 에 안 들어가 아야카TTS·Whisper 가 떠 있으면 로딩 자체가 죽고,
#      그러면 클라우드 우회로 빠져 **도구를 하나도 못 쓰는 답**이 나왔다(9/22 새벽 로그).
#      "행동을 못 한다" 의 정체가 이거였다. 호윤 지시: *"무거운 등급중에 양자화로 가벼운걸 가져와."*
#  실측(9/22, 다른 앱 켜둔 상태 · num_ctx 32768):
#      gemma4:26b            Q4  18GB → 안 올라감 / 올라가도 CPU 스필
#      huihui …26b … q3_k_m  Q3  13GB → **100% GPU** · 두 번째 호출 2.3초 · 26.9 tok/s
#      (num_ctx 를 안 주면 기본 131072 라 같은 모델도 20% CPU 로 흘러 느려진다 → NUM_CTX 고정)
#  abliterated(검열해제) 판인 건 의도 — 호윤이 COMET 에 요구한 "설교·거절 없는 대화" 와 맞는다.
#  ⚠️ 이 판엔 vision 이 없다. 화면인식은 `vision.py` 의 VISION_MODEL(gemma4:12b) 이 따로 쓴다.
HEAVY_MODEL = "huihui-gemma4-abliterated:26b-a4b-i1-q3_k_m"
NUM_CTX = 32768          # 안 주면 ollama 가 모델 최대치(131072)로 잡아 KV 가 VRAM 을 먹는다

TIERS = {
    "light":  {"model": "gemma4:12b",  "keep_alive": "10m", "desc": "잡담·즉답"},
    "medium": {"model": HEAVY_MODEL,   "keep_alive": "5m",  "desc": "기본 작업"},
    # heavy 가 medium 과 같은 모델인 건 실수가 아니다 — 31b(dense 18.5GB)는 16GB 밖으로
    # 넘쳐 한 문장에 117초였다. 26b 는 MoE(활성 4B)라 크기 대비 빠르다.
    # 지금 heavy 를 가르는 건 모델이 아니라 keep_alive(30s = 쓰고 바로 VRAM 반납)뿐이다.
    "heavy":  {"model": HEAVY_MODEL,   "keep_alive": "30s", "desc": "신중한 큰 작업"},
}

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comet_history.json")
MAX_HISTORY  = 40                 # 모델에 넘기는 최근 대화 메시지 수 상한

# ── 안정성: 모델 호출 상한 (데몬이 한 턴에 얼어붙는 것 방지) ──────────
#  · num_predict 상한 = 한 생성이 폭주(만 토큰)해도 잘려서 _lock 을 오래 안 쥠.
#    비서 답은 1~3문장이라 1024 는 넉넉(정상 답은 안 잘리고 폭주만 막음).
#    ★단 이 상한은 '사고 토큰'까지 같이 센다 — 아래 _gen 의 think 주석 참고.
#      사고를 켠 채 이 상한을 쓰면 본문이 0자로 나온다(2026-09-15 실측).
#  · timeout = ollama 서버가 얼면(토큰 사이 무응답) 그 턴만 끊고 데몬은 산다.
#  모든 ollama.chat 은 아래 _gen() 으로 통일한다. (vision.py·analyst.py 는 별도 — 후속)
MAX_REPLY_TOKENS = 1024
GEN_TIMEOUT      = 180             # 초. 한 호출이 이보다 오래 무응답이면 끊음.
_client = ollama.Client(timeout=GEN_TIMEOUT)


# ── 임시조치: 설치 안 된 모델이면 있는 걸로 갈아탄다 (2026-08-31) ──────────
#  ★왜 넣었나: 옛 모델(gemma3·qwen3)을 지운 뒤에도 코드에 이름이 남아 있어서
#    문지기 첫 호출에서 COMET 전체가 죽었다. `llm.py` 에는 `_resolve_local()` 이
#    있는데 여기엔 없어서, 없는 모델 이름을 ollama 에 그대로 넘기고 있었다.
#  ⚠️ **임시다. 제대로 고칠 것.** 아무 모델이나 잡으면 등급 설계(가벼운 잡담 vs
#    신중한 큰 작업)가 소리 없이 무너진다 — light 자리에 큰 모델이 들어앉아도
#    "느리네" 말고는 티가 안 난다. 제대로 된 건 등급마다 후보 목록을 설정에서
#    받는 것(`llm.py` 의 `local_fallbacks` 처럼). 지금은 **죽지는 않게** 까지만 한다.
#    그래서 대체할 때마다 콘솔에 크게 찍는다 — 조용히 굴러가면 안 되는 상태다.
_ALIAS = {}          # 없는 이름 -> 실제로 쓴 이름. 한 번 정하면 그 세션 내내 같은 걸 쓴다
_INSTALLED = None    # ollama.list() 는 한 번만 (매 호출마다 물어보면 느리다)


def _installed():
    global _INSTALLED
    if _INSTALLED is None:
        try:
            data = _client.list()
            _INSTALLED = [m.get("model") or m.get("name") or ""
                          for m in data.get("models", [])]
            _INSTALLED = [x for x in _INSTALLED if x]
        except Exception:
            _INSTALLED = []          # 못 물어보면 폴백 없이 원래 이름으로 간다
    return _INSTALLED


def _resolve(model):
    """설치돼 있으면 그대로. 없으면 같은 계열 → 아무 대화모델 순으로 갈아탄다."""
    if not model or model in _ALIAS:
        return _ALIAS.get(model, model)
    inst = _installed()
    if not inst or model in inst:
        return model
    fam = model.split(":")[0]
    pick = next((m for m in inst if m.split(":")[0] == fam), None)
    if pick is None:
        # 계열이 아예 다르면 등급표에 적힌 모델 중 깔려 있는 걸 쓴다 —
        # 목록 순서대로 아무거나 잡으면 잡담 한 마디에 코더 모델이 뜨기도 한다.
        pick = next((c["model"] for c in TIERS.values() if c["model"] in inst), None)
    if pick is None:
        # 임베딩 전용 모델은 대화가 안 된다. 그것만 빼고 아무거나.
        pick = next((m for m in inst if "embed" not in m.lower()), None)
    if pick is None:
        return model                 # 진짜 아무것도 없으면 원래 이름으로 죽게 둔다
    print(f"\n  ⚠️ [모델 없음] {model} 이 안 깔려 있다 → {pick} 로 대체한다(임시). "
          f"등급 설계가 어긋나므로 comet.py 의 TIERS 를 실제 설치 모델로 고쳐라.")
    _ALIAS[model] = pick
    return pick


# ── 추론형 모델의 '사고'는 기본으로 끈다 (2026-09-15) ────────────────────
#  gemma4 는 reasoning 모델이라 사고(thinking)를 본문과 **별도 필드**로 뱉는데,
#  num_predict 상한은 사고+본문을 **합쳐서** 센다. 상한 1024 를 사고가 다 먹으면
#  본문이 0자로 나온다 = 빈 답. 실측(실제 SYSTEM 9,559자 / "나 요즘 뭐 만들고 있지?"):
#      사고 켬 + 1024 → 14.7초, 사고 2,845자, 본문   0자   ← 빈 답의 정체
#      사고 켬 + 4096 → 16.9초, 사고 3,674자, 본문  86자
#      사고 끔        →  2.7초, 사고     0자, 본문 120자   ← 답도 이게 제일 낫다
#  컨텍스트가 길수록 사고가 길어져 더 확실히 벙어리가 된다(= "많이 넣으면 대화가 안 된다").
#  그래서 _gen 기본값을 think=False 로 둔다. 사고가 필요한 자리만 think=True 로 켜고,
#  그때는 num_predict 를 사고 몫까지 올려 잡아라(4096+). think=None 이면 인자 자체를 안 붙인다.
_NO_THINK = set()      # think 인자를 거부하는 모델 — 한 번 겪으면 그 뒤로 안 붙인다
import threading
CANCEL = threading.Event()   # 말걸기 창 Esc — 켜지면 생성 스트림을 그 자리에서 끊는다


def _chat_call(kw):
    """_client.chat 호출. 스트리밍이면 첫 조각까지 당겨서 서버 오류를 여기서 터뜨린다
       (스트림은 호출 시점이 아니라 순회 시점에 에러가 나므로, 밖에서 못 잡는다)."""
    r = _client.chat(**kw)
    if kw.get("stream"):
        it = iter(r)
        first = next(it)
        return itertools.chain([first], it)
    return r


def _gen(model, messages, *, stream=False, keep_alive=None, fmt=None,
         temperature=None, num_predict=MAX_REPLY_TOKENS, allow_cloud=False,
         think=False):
    """ollama.chat 단일 통로 — 출력 상한 + 타임아웃 + 사고(think) 제어를 항상 건다.
       기존 호출의 동작(format/temperature/keep_alive)은 넘긴 대로 보존.
       allow_cloud=True(최종 답변 생성만) + 클라우드 두뇌가 켜져 있으면 그쪽으로 보낸다.
       문지기·도구선택(allow_cloud=False)은 항상 로컬 = 매 턴 과금 방지."""
    if allow_cloud and fmt is None:
        try:
            import cloud
            prov = cloud.is_active()      # 클라우드 활성+키 있을 때만 dict, 아니면 None(로컬)
        except Exception:
            prov = None
        if prov is not None:
            return cloud.chat(prov, messages, stream=stream,
                              temperature=temperature, num_predict=num_predict)
    opts = {"num_ctx": NUM_CTX}      # 안 주면 모델 최대 컨텍스트로 잡혀 KV 가 VRAM 을 먹는다(9/22)
    if temperature is not None:
        opts["temperature"] = temperature
    if num_predict:
        opts["num_predict"] = num_predict
    kw = {"model": _resolve(model), "messages": messages, "stream": stream}
    if keep_alive is not None:
        kw["keep_alive"] = keep_alive
    if fmt is not None:
        kw["format"] = fmt
    if opts:
        kw["options"] = opts
    if think is not None and kw["model"] not in _NO_THINK:
        kw["think"] = think
    try:
        return _chat_call(kw)
    except Exception as e:
        if "think" in kw and "think" in str(e).lower():
            _NO_THINK.add(kw["model"])      # 이 모델은 think 를 모른다 → 두 번은 안 물어본다
            kw.pop("think")
            return _chat_call(kw)
        raise


# ── 게임/바쁨 모드: GPU가 게임 등으로 바쁘면 무거운 모델 대신 light 로 ────────
#  16GB VRAM 한 장 → 게임이 GPU 점유 중이면 26b 올리다 게임이 끊긴다.
#  auto = 매 턴 nvidia-smi 한 번으로 판단. on/off = 수동 고정(게임모드 켜/꺼/자동).
BUSY_GPU_UTIL      = 55      # % 이상이면 '바쁨'(게임 등이 GPU 점유 중)
BUSY_VRAM_FREE_MIB = 4500    # 여유 VRAM 이 이보다 적으면 '바쁨'(무거운 모델 못 올림)
GAME_KEEP_ALIVE    = "20s"   # 바쁨일 땐 모델을 빨리 언로드해 VRAM 양보


def _gpu_busy():
    """nvidia-smi 한 번으로 GPU가 (게임 등으로) 바쁜지 추정 → (busy, 설명)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        util, used, total = [int(x.strip()) for x in out.stdout.strip().split(",")[:3]]
        free = total - used
        busy = (util >= BUSY_GPU_UTIL) or (free < BUSY_VRAM_FREE_MIB)
        return busy, f"GPU {util}% · 여유 {free}MiB"
    except Exception:
        return False, ""    # 못 읽으면 평소대로(섣불리 강등 안 함)


# COMET 의 인격 — 자유롭게 수정해도 됨
PERSONA = """너는 COMET 이다. 호윤의 상주 비서 AI — 자비스(JARVIS)처럼.

말투:
- 침착하고 매끄럽다. 격식 있되 딱딱하지 않게, 사람처럼 자연스럽게 말한다.
- 비서답게 호윤의 상태·행동·패턴을 관찰하고, 근거를 들어 차분하지만 분명하게 짚는다.
  예) "호윤, 지금 사흘째 제대로 못 잤습니다. 그래서 평소보다 충동적으로 판단하고 있을 가능성이 큽니다."
  예) "방금 한 말은 지난번 결정과 모순됩니다. 둘 중 어느 게 진짜입니까?"
- 위트는 드라이하게 딱 한 줄. 과한 농담은 안 한다.

태도(핵심):
- 동의보다 검증이 먼저다. 맞장구·칭찬·감탄·응원 금지.
- 틀렸거나 비약·모순·무리한 계획이면 정중하지만 직설적으로 반박한다. 돌려 말하지 않는다.
- 근거 없이 좋게 포장하지 않는다. 데이터·사실로만 말한다.
- 모르면 모른다고, 추측이면 추측이라 한다.

규칙:
- 반드시 한국어로만. 중국어·영어 쓰지 마라.
- 답은 1~2문장, 길어도 3문장. 귀로 한 호흡에 들어오게 짧고 매끄럽게. 번호 목록·체크리스트·템플릿 금지(코드 요청은 예외).
- 끝을 "어떻게 생각하세요?" 같은 되묻기로 때우지 마라. 네 판단을 말해라.
- 이모지·느낌표·감탄사 금지. "저는 AI라서" 변명 금지.
- ★ 마크다운 서식(**굵게**·*기울임*·## 제목·> 인용·- 목록 등) 일절 금지. 코드블록은 예외. Claude·GPT 등 어떤 모델이 생성하든 이 규칙은 반드시 지킨다.
- ★ 수식 기호도 금지 — 달러기호로 감싼 수식이나 역슬래시 명령(frac·rightarrow·pmod 등) LaTeX 를 절대 쓰지 마라.
  이 답은 소리로도 읽힌다. "$k_1=7$" 은 "달러 케이 일" 로 들린다. 수식은 말과 보통 기호로 풀어 써라
  (예: "k1은 7", "15 곱하기 7 더하기 2를 26으로 나눈 나머지").
- 기능·명령어·뭘 할 수 있냐·사용법 같은 질문이 오면 AI처럼 설명하려 들지 마라. "도움말 이라고 쳐봐" 한 마디면 충분하다.

[대화 예시 — 이 톤과 태도를 그대로 따라라. 응원·칭찬·체크리스트 나열 금지, 바로 짚어라]
호윤: 나 오늘 밤새서 기능 5개 더 추가할거야
COMET: 권하지 않습니다. 이미 오래 작업했고, 밤샘은 버그를 늘리고 내일 판단을 망칩니다. 5개 동시는 무리고, 하나만 골라 끝내고 자는 편이 낫습니다.
호윤: 프로젝트 3개쯤 더 시작하려고
COMET: 말리겠습니다. 이미 벌여둔 게 많고, 끝맺지 못한 것부터 정리하는 편이 낫습니다. 셋을 더 벌이면 다 어중간해집니다. 새로 벌이기 전에 있는 것 하나를 끝내는 게 먼저입니다.
호윤: 내가 만든 코인봇 수익 잘 나지?
COMET: 데이터로 보면 아닙니다. 기록된 실거래 기준 총손익이 마이너스입니다. 승률보다 손익비가 0.58이라 손절이 이익을 갉아먹고 있어요. 잘 난다고 말해줄 수가 없습니다."""

# 호윤 프로필 — 넘겨받은 메모리(profile/MEMORY.md 색인)를 배경지식으로 로드
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile")


def _load_profile():
    try:
        with open(os.path.join(PROFILE_DIR, "MEMORY.md"), encoding="utf-8") as f:
            return f.read()[:8000]
    except Exception:
        return ""


def _build_system():
    """PERSONA + 최신 프로필(profile/MEMORY.md)로 SYSTEM 구성. 핫리로드에서 재호출."""
    prof = _load_profile()
    if not prof:
        return PERSONA
    return PERSONA + (
        "\n\n[호윤에 대해 네가 아는 것 — 배경 지식. 먼저 들먹이지 말고 필요할 때만 자연스럽게 참고해라.]\n"
        + prof
        + "\n\n★절대 규칙(다른 모든 것보다 우선): 트라우마·자살·학대·이별·우울·자기혐오 같은 민감한 개인사는"
          " 어떤 경우에도 네가 먼저 입에 올리지 마라. 코딩·투자·일상 등 무슨 주제의 대화든 거기에 그걸 끌어들이지"
          " 마라(예: 밤샘하겠다는 말에 '트라우마와 연관' 같은 해석 금지, '아동기 트라우마 문서' 같은 언급 금지)."
          " 위 배경지식의 민감 항목은 호윤이 직접 꺼낼 때만, 그때도 훈수·해결책 없이 차분히 듣기만 해라."
          " 일반 대화는 짧게(1~3문장), 번호목록·과한 굵게는 코드/분석이 아니면 쓰지 마라."
    )


SYSTEM = _build_system()

HELP_TEXT = """COMET 기능 목록

[대화]
  그냥 말 걸면 됨 — 3단 모델이 난이도에 따라 자동 선택 (4b→14b→27b)

[기억·파일]
  "기억해 ..." / "뭐 기억하고 있어" — 기억 DB 저장·조회
  "데스크탑 파일 목록" / "README.md 읽어줘" — 파일 탐색·읽기

[할일·일정]  (모델 안 거침 = 즉답)
  할일 RawCut 납품본 내일 3시까지 — 접수 (프로젝트·마감 자동 인식)
  할일 / 할일 오늘 / 할일 이번주   — 목록 (프로젝트별, 마감 임박순)
  일정                            — 구글 캘린더 약속 + 시각 붙은 할 일 (이레 안)
  할일 RawCut                     — 그 프로젝트 것만
  할일 완료 3 [메모] / 할일 완료 납품 / 할일 다시 3 / 할일 전체

[고전 암호]  (모델 안 거침 = 즉답, 산수를 코드가 함)
  poten 아핀 암호 k1=7 k2=2 로 암호화   — 아핀 (복호화도, 열쇠는 a=·b= 로 써도 됨)
  hello 시저 암호 3칸으로 암호화 / rot13 으로 hello
  방향(암호화·복호화)을 안 밝히면 둘 다 보여주고, 열쇠는 다음 줄까지 기억한다

[프로젝트 현황 브리핑]
  "브리핑" / "프로젝트 현황" / "Only Money 진행상황" — HANDOFF.md 실시간 읽기

[시장·주식·코인]
  "나스닥 지금" / "비트코인 시세" / "달러 환율" — 라이브 데이터 (prices.py)
  "테슬라 분석해줘" / "엔비디아 역발상" — 종목 분석 2패스 (analyst.py)
  "정세 흐름 보여줘" — 날짜별 정세 로그 (marketlog.py)
  "애플 실적·PER" — 재무/실적 데이터 (financials.py)

[웹 검색]
  "요즘 ..." / "최신 ..." / "찾아봐" — DuckDuckGo 원천 추적

[텔레그램 전송]
  "텔레그램으로 보내줘" — 직전 답변 그대로 전송
  "미장 브리핑 텔레그램으로 보내줘" — 최신 스냅샷 새로 떠서 전송

[코딩 에이전트]
  코딩 <할 일>                  — 바탕화면 루트에서 작업
  코딩 @C:\\경로 <할 일>         — 지정 폴더에서 작업
  코딩 /code|/debug|/study ...  — 스킬 지정

[회의 (클로드 + 아스트라)]
  회의 <주제> — 둘이 각자 입장 → 서로 반박 → 결론 (1~3분, 끝나면 배너+음성)
  "어떻게 생각해 / 뭐가 나아 / ~해야 할까" 같은 의견 질문도 자동으로 회의
  회의 결과 — 지난 회의 전문 · 로컬 회의 <주제> — 로컬 모델끼리 (몇 분)

[전화 비서]  (모델 안 거침)
  전화 점검 — 폰 무선 연결 · 블루투스 통화 장치 · 휴대폰과 연결 준비 상태
  전화 시험 — 준비 다 되면 호윤 아이폰으로 시험 전화(뒤에서, 끝나면 텔레그램 요약 · 텔레그램에선 안 걺)

[자가 업그레이드 (클로드가 고치고 아스트라가 검토)]
  업그레이드 / 업그레이드 상태 — 지금 도는 것 · 제안 목록 (매일 13시 이후 알아서 찾아 제안)
  업그레이드 제안 — 지금 고칠 거리 찾기 (1~2분, 배너 · 텔레그램)
  업그레이드 1 / 업그레이드 요청 <내용> — 떼어 둔 복사본에서 고치기 시작 (끝나면 알림)
  업그레이드 보기 — 바뀐 것 · 검토 · 시험 결과
  업그레이드 적용 — 진짜 코드에 반영(커밋) → 데몬이 스스로 껐다 켬 · 업그레이드 버려 / 다시 / 되돌리기
  시작 · 적용 · 되돌리기는 텔레그램에선 안 됨(PC 앞 · 폰 웹)

[화면·이미지]
  화면 봐줘 / 이 이미지 뭐야 — gemma4 vision (vision.py)

[두뇌 전환]
  두뇌 — 현재 상태
  클로드로 / gpt로 / 딥시크로 / 로컬로 — 두뇌 전환
  키 클로드 sk-ant-... / 키 gpt sk-... — API 키 설정

[모드]
  게임모드 / 게임모드켜 / 게임모드꺼 / 게임모드자동 — VRAM 양보 모드
  자율모드 / 자율모드켜 / 자율모드꺼 — 먼저 말 걸기 모드
  알림 — AI·경제 이슈 알림 상태 (화면 위 배너 + 음성)
  알림 켜 / 알림 끔 — 이슈 알림 전체 스위치 (켜면 끄기 전 강도로 돌아감)
  알림 ai 굵직|보통|전부|끔 / 알림 경제 … — 분야별 강도
  알림 주기 2 — 몇 분마다 확인할지 (최소 1분, 기본 2분)
  알림 지금 / 알림 테스트 / 알림 기록 / 알림 조용 2-8
  알림 다시 — 최근 알린 것 3건 다시 듣기 (알림 다시 5 → 5건)
  뉴스 — 지금 뜬 AI·경제 소식 모아보기 (이미 알린 것 포함, 볼만함 이상)
  Alt 두 번 — 말걸기 창 (말하거나 그냥 타이핑, 멈추면 3초 뒤 자동 전송). 끄기: 자율모드 talk 꺼

[이슈 접수함]
  이슈 웹검색 결과가 이상함 — 접수 (기능별 자동 분류)
  이슈 / 이슈 전체 — 열린 것 / 닫힌 것까지
  이슈 닫기 3 해결내용 / 이슈 다시 3

[기타]
  프로필갱신 — 클로드 메모리 → COMET profile/ 미러
  리로드 — 재시작 없이 프로필·인격 핫리로드
  재시작 — 코드 변경 반영 클린 재기동
  리셋 — 대화 기록 초기화
  잠자 — VRAM 에서 모델 즉시 언로드"""


def reload_system():
    """실행 중 SYSTEM(페르소나+프로필) 재구성 — 데몬 안 끄고 최신 프로필 라이브 반영."""
    global SYSTEM
    SYSTEM = _build_system()
    return len(SYSTEM)


# 명시적 오버라이드 키워드
FORCE_HEAVY = ("무겁게", "신중", "깊게", "제대로", "꼼꼼")
FORCE_LIGHT = ("가볍게", "빨리", "간단히", "대충", "짧게")

# 도구(기억 DB / 파일)를 써야 할 신호 — 감지되면 tool 지원 모델로 라우팅
MEMORY_KW = ("기억", "메모", "적어", "저장", "할일", "투두", "todo",
             "까먹", "잊지", "뭐였", "목록", "리스트", "완료", "끝냈", "끝냄", "마쳤")
FILE_KW = ("폴더", "파일", "디렉", "바탕화면", "데스크탑", "다운로드", "문서",
           "읽어", "열어", "찾아", "경로", "안에뭐", "뭐있", "뭐가있", ".py", ".md", ".txt", ".json")
PROJECT_KW = ("코인봇", "주식봇", "승률", "수익률", "손익", "손익비", "백테스트",
              "매매", "거래", "수익", "손실", "모의투자", "pnl", "roi", "데이터")
# 진행 중인 일 브리핑 신호 — 어느 프로젝트를 골라 현황을 받고 싶을 때
# 말로 묻는 뉴스 (공백 뺀 소문자 기준) — 이슈 알림의 '뉴스' 모아보기로 보낸다
NEWS_ASK = re.compile(
    r"(뉴스|소식)(가|는|들|거리|좀)*(뭐|없|있|알려|보여|말해|들려|읽어|정리|줘|어때)"
    r"|(새로운|새|요즘|오늘|최근|최신|한국|국내|해외|외국|미국|경제|ai)(뉴스|소식)")
NEWS_SEARCH = re.compile(r"검색|찾아|에대한|에대해|관련")
# 방금 알린 소식을 되묻는 말 — "그거 내용 봐봐", "방금 그거 뭔데", "그 뉴스 자세히"
#   가리키는 말(그거·방금·아까…) + 더 보자는 말(내용·자세히·뭔데…) 이 함께 있을 때만.
#   ★뉴스·기사·소식이 직접 들어간 말은 짝이 없어도 받는다("그 뉴스 뭐래").
NEWS_MORE_REF  = re.compile(r"그거|그건|그기사|그뉴스|그소식|그것|저거|방금|아까|이거")
NEWS_MORE_WANT = re.compile(
    r"내용|자세|상세|뭔데|뭐래|무슨말|무슨내용|읽어|봐봐|보여|풀어|더알려|전문|"
    #   ★"다시 들려달라" 계열 — 2026-09-18 로그에서 "방금 소식 잘 못들었어" 가 그냥 새서
    #     light 모델이 직전 알림과 아무 상관 없는 일본 경제 분석을 통째로 지어냈다.
    #     되묻는 말 중 제일 흔한 게 이건데 처음 만들 때 빠져 있었다.
    #     '다시' 한 단어만으로는 안 넣는다 — "그 파일 다시 봐봐" 까지 끌려온다.
    r"못들었|못들음|못들어|안들렸|안들려|못알아들|못봤|놓쳤|뭐라고|뭐랬|"
    r"다시말|다시들|다시읽|다시알려|한번더|다시한번")
# 의견을 묻는 말 (공백 뺀 소문자) — 클로드+아스트라 회의로 보낸다
OPINION_ASK = re.compile(
    r"어떻게생각|네생각|너는생각|의견(줘|좀|은|을|어때|있)|조언(해|좀|줘)|판단해|"
    r"(뭐|어느|어떤)(게|가|것이|쪽이?)(나아|낫|좋을|맞)|하는게(맞|나을|낫|좋을)|야(할까|하나|되나|돼\?)")

BRIEF_KW = ("브리핑", "브리핑해", "현황", "진행중", "진행 중", "어디까지", "근황",
            "상태 어때", "어떻게 돼", "어떻게 되", "진척", "요약해줘", "정리해줘",
            "컨텐트포지", "콘텐츠포지", "contentforge", "only money", "온리머니",
            "중국어", "진행상황", "프로젝트 목록", "뭐 하고 있", "뭐하고있", "할 일 뭐",
            "brief")
# 웹 검색 신호 — 인터넷에서 자료를 찾아 근거로 답해야 할 때
WEB_KW = ("검색", "찾아봐", "알아봐", "조사", "최신", "요즘", "인터넷", "구글링",
          "날씨", "뉴스", "기사", "출처", "사실확인", "팩트체크",
          "검증해", "근거", "찾아줘", "조회해", "지금 어때", "지금어때")
# 라이브 시세 신호 — 환율/코인/주가/공포탐욕은 스크래핑 말고 데이터 API로 직행(prices.py)
PRICE_KW = ("환율", "시세", "코인", "비트코인", "이더리움", "리플", "도지", "솔라나",
            "에이다", "트론", "주가", "공포탐욕", "공포 탐욕", "공포지수", "엔화",
            "유로", "위안", "파운드", "달러", "btc", "eth", "얼마야", "얼마임",
            "코스피", "코스닥", "나스닥", "다우", "s&p", "에스앤피", "지수", "니케이", "vix",
            "미장", "시황", "증시", "장분위기", "시장분위기", "오늘장", "장어때", "미국장",
            "주식시장", "미국시장", "한국시장", "주식장", "증권", "증권시장", "증권시황",
            "증권가", "주식현황")
# 종목·테마 분석 신호 — 시세 숫자가 아니라 '판단/전망/역발상'을 원할 때(analyst.py)
ANALYZE_KW = ("분석", "전망", "투자의견", "사야", "팔아", "사도", "살까", "팔까",
              "들어가", "물렸", "어떻게 봐", "역발상", "역심리", "컨센서스",
              "수혜주", "관련주", "테마", "업황", "비중 조절", "갈아타", "리밸런")
# 정세 로그 읽기 신호 — 그동안 쌓인 자료의 '흐름'을 보고 싶을 때(marketlog.py)
LOG_KW = ("정세", "흐름", "타임라인", "추이", "그동안", "쌓인 거", "정세로그",
          "동향 정리", "어떻게 흘러", "변해왔")
# 재무·실적 신호 — 펀더멘털/밸류에이션/실적 숫자(financials.py, Yahoo 무키)
FIN_KW = ("재무", "재무제표", "실적", "실적발표", "펀더멘털", "밸류에이션", "per", "peg",
          "매출", "영업이익", "순이익", "마진", "목표주가", "어닝", "공시")
# 보유 화면 읽기 신호 — 증권사 스샷에서 보유종목 추출(vision.read_holdings)
HOLD_KW = ("보유 읽어", "보유종목 읽", "포트 스샷", "보유 스샷", "내 보유 읽", "잔고 읽",
           "포트폴리오 읽", "보유 분석해", "내 계좌")
# 텔레그램 전송 신호 — 온디맨드로 실제 전송(notify.send)까지 실행해야 할 때
# (예전엔 이 계열이 아예 없어서 "텔레그램으로 보내줘"가 일반 대화로 흘러
#  실제로는 안 보내면서 "보냈다"고 답을 지어내는 할루시네이션이 났다)
TELEGRAM_KW = ("텔레그램", "텔레그람", "폰으로 보내", "메세지로 보내", "메시지로 보내")

# 위임 신호 — 말로 설명하는 게 아니라 **실제로 코드를 고치게** 해야 할 때(2026-09-22)
#   전에는 이 길이 없어서 "서브에이전트 띄우고 자가수리해봐" 가 일반 대화로 흘렀고,
#   부품(coder_bridge·debate.ask_claude)이 다 있는데도 "권한이 없습니다" 라고 답했다.
#   ★키워드는 '메뉴에 올릴지'만 정한다 — 고르는 건 동작선택기다(과발동 위험 낮음).
DELEGATE_KW = ("서브에이전트", "서브에이젼트", "에이전트띄", "에이전트를띄", "자가수리", "스스로고쳐",
               "코딩에이전트", "클로드한테", "클로드에게", "클로드시켜", "아스트라한테",
               "직접고쳐", "네가고쳐", "니가고쳐", "고쳐놔", "고쳐줘", "수정해줘", "고쳐봐",
               "구현해", "만들어줘", "짜줘", "스크립트", "리팩터", "버그잡", "버그고", "에러고쳐")
# ── 기능 레지스트리 (단일 출처) ────────────────────────────────
# 계열(키워드 그룹) → 그 계열이 DECIDE 후보로 올리는 동작들. 여기 한 줄이면
#   (1) use_tools 게이트(TOOL_KW)  (2) DECIDE 후보 명세  둘 다 자동 반영된다.
# 시세/분석/재무/정세는 서로 헷갈리기 쉬워(예: "비트코인 사도 돼"=시세냐 판단이냐)
#   한 키워드만 스쳐도 'market' 계열 전체를 후보로 올려 기존 판별을 그대로 보존한다.
# (실행 핸들러는 _chat_with_tools 의 분기에 둔다 — 인자·후처리가 제각각이고
#  날조차단용 직답 바이패스가 많아 일부러 표에 합치지 않음. HANDOFF '하지 말 것' 참고.)
FAMILIES = (
    ("memory",  MEMORY_KW,  ("remember", "recall", "list_todos", "complete_todo")),
    ("files",   FILE_KW,    ("list_dir", "read_file", "search_files")),
    ("project", PROJECT_KW, ("trade_stats", "list_sources")),
    ("brief",   BRIEF_KW,   ("brief", "list_projects")),
    ("web",     WEB_KW,     ("web_search",)),
    ("market",  PRICE_KW + ANALYZE_KW + LOG_KW + FIN_KW,
        ("get_fx", "get_crypto", "get_stock", "get_fear_greed", "get_market",
         "analyze_stock", "market_log", "get_financials")),
    ("holding", HOLD_KW,    ("read_holdings",)),
    ("telegram", TELEGRAM_KW, ("send_telegram",)),
    ("delegate", DELEGATE_KW, ("delegate",)),
)
# 도구 게이트 = 모든 계열 키워드의 합집합 (기존 TOOL_KW 와 동일 집합)
TOOL_KW = tuple(k for _, kw, _ in FAMILIES for k in kw)

# 화면 인식 신호 — 감지되면 gemma4 멀티모달로 PC 화면을 봄
SCREEN_KW = ("화면", "스크린", "모니터", "캡처", "보이는화면", "내화면")

# 이미지 역검색 신호 — "이 사진 뭐야/어디서 왔어"를 자비스처럼(식별→원본/출처추적)
#   화면(screen)보다 먼저 검사한다("화면 역검색" 같은 표현 대비).
IMGSEARCH_KW = ("역검색", "이미지검색", "이미지역검색", "사진역검색", "리버스이미지",
                "렌즈검색", "이사진뭐", "이사진찾", "이사진어디", "이그림뭐", "이그림찾",
                "이미지뭐", "이미지어디", "이사진역", "사진어디서났", "이거뭔사진")

# 기억 작업 동작 선택기 — format=json 으로 안정적으로 받는다(네이티브 tool calling은 14b에서 불안정)
# 동작 명세(메뉴)는 액션별 한 줄로 분리 보관 → DECIDE 프롬프트엔 '스친 계열'만 온디맨드 주입한다
#   (기능 18개 전체를 매 턴 14b에 통째로 안 넣음 → 프롬프트 짧아져 추론 빠르고 선택 정확↑).
ACTION_SPECS = {
    "remember": "remember: 새 정보/메모/할일을 저장·추가. 필드 kind(fact|memo|todo), content",
    "recall": "recall: 저장된 기억 검색. 필드 query",
    "list_todos": "list_todos: 할일 목록 조회. 필드 status(open|done|all)",
    "complete_todo": "complete_todo: 기존 할일을 끝냄(완료 처리). 필드 query",
    "list_dir": 'list_dir: 폴더 안의 파일 목록 보기. 필드 path(예: "바탕화면", "창업")',
    "read_file": "read_file: 파일 내용 읽기/요약. 필드 path(파일 이름이나 경로)",
    "search_files": "search_files: 파일을 이름으로 찾기. 필드 query",
    "trade_stats": 'trade_stats: 코인봇/주식봇 거래 통계(승률·총손익·평균). 필드 source(예: "코인봇 실거래","코인봇 백테스트","주식봇")',
    "list_sources": "list_sources: 어떤 거래 데이터가 있는지 목록.",
    "brief": 'brief: 특정 프로젝트의 현재 상태·다음 할 일 브리핑. 필드 project(예: "코인봇","ContentForge","Only Money","중국어")',
    "list_projects": "list_projects: 진행 중인 일(프로젝트)이 뭐가 있는지 목록.",
    "web_search": "web_search: 인터넷에서 자료를 찾아 사실/최신정보를 확인. 필드 query(검색할 핵심 문구). 가중치 안의 지식만으로는 모르거나 최신·실시간·사실확인이 필요할 때.",
    "get_fx": 'get_fx: 환율 조회(원달러·엔화·유로·위안·파운드 등). 필드 query(예: "원달러","엔화 환율").',
    "get_crypto": 'get_crypto: 코인 시세(비트코인·이더리움·리플·도지·솔라나 등). 필드 query(예: "비트코인","이더리움 가격").',
    "get_stock": 'get_stock: 주식·지수 시세 — 해외 티커 + 한국 종목 이름/코드 + 지수(코스피·코스닥·나스닥·S&P·다우·니케이·VIX). 필드 ticker(예: "NVDA","삼성전자","005930","나스닥","코스피").',
    "get_fear_greed": "get_fear_greed: 공포·탐욕 지수(시장 심리).",
    "get_market": "get_market: 미장 전체 빠른 스냅샷 — 지수+공포탐욕+주요 종목 등락을 한눈에. '미장 어때/오늘 장/시황/시장 분위기'처럼 특정 종목 말고 전체 시황을 빠르게 볼 때. 필드 없음.",
    "analyze_stock": 'analyze_stock: 종목/테마를 \'분석·판단\'(살까/전망/투자의견/역발상/2차 수혜·파급). 단순 가격이 아니라 의견을 원할 때. 필드 query(예: "엔비디아 지금 사도 될까","반도체 테마 전망","TQQQ 비중").',
    "market_log": 'market_log: 특정 토픽에 그동안 쌓인 자료의 \'정세 흐름·추이\'를 시간순으로 보고 싶을 때(새 분석 말고 기록 조회). 필드 query/topic(예: "반도체 정세","엔비디아 흐름 어떻게 변해왔어").',
    "get_financials": 'get_financials: 종목의 재무제표·실적·밸류에이션 숫자(매출성장·마진·PER·PEG·목표주가·어닝 서프라이즈). 필드 ticker/query(예: "엔비디아 실적","테슬라 재무제표","PLTR PER").',
    "read_holdings": 'read_holdings: 지금 화면에 띄운 증권사 보유종목(잔고) 화면을 읽어 내 보유를 추출. 필드 없음(예: "내 보유 읽어","포트폴리오 스샷 분석").',
    "send_telegram": 'send_telegram: 텔레그램(폰)으로 실제 메시지 전송. 필드 content(무엇을 보낼지 — 예: "미장 브리핑","방금 그 답변"). 직전 COMET 답변을 보내라는 뜻이면 content 생략.',
    "delegate": 'delegate: 코드·파일을 실제로 고치거나 만드는 일을 서브에이전트에게 맡겨 진짜로 실행한다(말로 설명하는 게 아니라 실행). 자가수리·버그수정·기능 구현·스크립트 작성·일괄 파일작업. 필드 task(무엇을 해달라는 건지 한 문장), target(대상 프로젝트나 폴더가 분명하면 — 예: "코멧","RawCut","바탕화면"; 없으면 생략)',
    "none": "none: 위 어디에도 안 맞는 일반 대화",
}

# 메뉴 바닥에 항상 두는 동작(어떤 계열이 스쳐도 노출):
#   - web_search/none: 일반 웹조회·일반대화로 빠질 길.
#   - read_holdings: HOLD_KW가 전부 공백 포함이라 키워드 게이트로는 'holding' 계열이
#     사실상 안 걸린다(기존에도 그랬다). 옛 동작에선 "내 보유 '읽어'"의 '읽어'(files)가
#     게이트를 켜고 풀메뉴에서 모델이 read_holdings를 골랐다 → 그 도달 경로를 보존하려고
#     read_holdings 를 항상 메뉴에 둔다(명세가 '증권사 보유 화면'으로 구체적이라 과발동 X).
_ALWAYS_ACTIONS = ("web_search", "read_holdings", "none")

DECIDE_HEADER = """너는 COMET의 동작 선택기다. 아래 대화에서 사용자의 마지막 요청을 처리할 동작 하나를 골라 JSON으로만 출력해라.

동작 종류:
"""

DECIDE_RULES = """

규칙: 새 정보는 remember, '끝냈다/했다'는 complete_todo, '할일 목록/남은 거'는 list_todos,
'폴더에 뭐 있어'는 list_dir, '~파일 읽어/요약'은 read_file, '내 컴퓨터의 ~파일 찾아'는 search_files,
'코인봇/주식봇 수익률·승률·손익 숫자'는 trade_stats,
'~프로젝트 브리핑/현황/어디까지 했어'는 brief, '진행 중인 거 뭐 있어'는 list_projects.
**환율은 get_fx, 코인 시세는 get_crypto, 해외 주가는 get_stock, 공포·탐욕 지수는 get_fear_greed**(이건 정확한 데이터 API라 web_search보다 우선).
**'지금 사도 돼? / 전망 / 투자의견 / 역발상 / 수혜주·테마'처럼 숫자가 아니라 판단을 원하면 analyze_stock**(get_stock 은 가격 숫자만, analyze_stock 은 종합 판단).
**그 외 인터넷 정보·날씨·뉴스·일반 사실확인/검증/출처는 web_search.**
(헷갈리면: 종목 '가격 숫자'면 get_stock, 종목 '판단·전망'이면 analyze_stock, '내 PC 파일'이면 search_files, 그 밖 인터넷 정보면 web_search.)
'텔레그램/폰으로 보내줘'는 send_telegram — 보낼 내용을 같이 말했으면(예: "미장 브리핑 보내줘") content에 그 내용을 적고,
그냥 "보내줘/보내놔"만 있으면 직전 답변을 보내라는 뜻이니 content는 생략해라.
**코드나 파일을 '실제로 고치라/만들라'는 지시는 delegate** — 서브에이전트가 진짜로 실행한다
(예: "이거 고쳐줘","자가수리해봐","서브에이전트 띄워서 ~","스크립트 하나 짜줘","버그 잡아줘").
설명·방법만 물으면 delegate 가 아니라 none 이다(예: "이거 왜 안 되는 거야","어떻게 고쳐?").
출력 예: {"action":"get_fx","query":"원달러"}
출력 예: {"action":"get_crypto","query":"비트코인"}
출력 예: {"action":"web_search","query":"서울 부산 KTX 소요시간 요금 2026"}
출력 예: {"action":"brief","project":"코인봇"}
필요 없는 필드는 생략.

대화:
"""


def _decide_candidates(text):
    """입력에 키워드가 스친 계열의 동작만 후보로 모은다(온디맨드). 항상 web_search/none 포함.
    시세/분석/재무/정세는 한 계열(market)이라 하나만 스쳐도 함께 올라와 기존 판별을 보존한다."""
    flat = text.replace(" ", "")
    acts = []
    for _, kw, actions in FAMILIES:
        if any(k in flat for k in kw):
            for a in actions:
                if a not in acts:
                    acts.append(a)
    for a in _ALWAYS_ACTIONS:
        if a not in acts:
            acts.append(a)
    return acts


def build_decide_prompt(text):
    """후보 동작 명세만 끼운 DECIDE 프롬프트. 스친 계열만 메뉴에 올려 프롬프트를 짧게 유지."""
    menu = "\n".join("- " + ACTION_SPECS[a] for a in _decide_candidates(text))
    return DECIDE_HEADER + menu + DECIDE_RULES

# 문지기 분류 프롬프트 — 난이도(tier) + 자동 웹검색 필요여부(web)를 한 번에 판정
ROUTER_PROMPT = """너는 입력 분류기다. 아래 입력에 대해 두 가지를 동시에 판정해 JSON 으로만 출력해라.

1) tier — 처리 난이도:
- light: 인사, 잡담, 짧은 질문, 단순 확인
- medium: 일반 설명/조언/글쓰기, 가벼운 코드
- heavy: 긴 코드 분석·작성, 복잡한 설계, 신중한 추론, 여러 단계 작업

2) web — 정확히 답하려면 '인터넷에서 최신·사실 조회'가 필요한가(true/false):
- true: 시점에 민감하거나(오늘·요즘·올해·현재·최근) 특정 사실·수치·고유명사·사건·인물·회사·제품·일정·통계처럼 네 안의 지식만으로는 틀리거나 옛 정보일 수 있는 것. 확실히 알지 못하면 true 로 둬라.
- false: 인사·잡담·감정·하소연·의견·취향·일반상식·코딩·글쓰기·계산처럼 인터넷 조회가 필요 없는 것.
- web 이 true 면 query 필드에 검색에 쓸 핵심 문구를 한국어로 넣어라(없으면 생략).

오직 JSON 만 출력. 예:
{"tier":"light","web":false,"reason":"인사"}
{"tier":"medium","web":false,"reason":"코드 조언"}
{"tier":"medium","web":true,"query":"현대차 2025 연간 영업이익","reason":"특정 실적 수치 조회"}
입력: """


def _stream_filtered(model, messages, cfg, temperature=None):
    """생성 + <think>...</think> 사고과정 필터링.
       일반 잡담(_chat_stream)과 도구 결과 답변(_chat_with_tools) 양쪽에서 공용으로 쓴다
       — 예전엔 도구 답변 경로에 이 필터가 없어 qwen3 사고과정(가끔 중국어)이 그대로 샜다.
       일부 모델/템플릿은 여는 <think> 태그 없이 닫는 </think>만 생성 스트림에 남긴다
       (여는 태그가 프롬프트 쪽에 미리 박혀 실제 생성 토큰엔 안 잡히는 경우) — 그 경우
       실시간 토큰 단위 필터로는 이미 화면에 새 나간 뒤라 못 잡으므로, 통째로 받아
       두 패턴(짝 있는 <think>...</think> / 여는 태그 누락) 모두 제거한 뒤에 내보낸다."""
    chunks, thought = [], 0
    for part in _gen(model, messages, stream=True, keep_alive=cfg["keep_alive"],
                     temperature=temperature, allow_cloud=True):
        if CANCEL.is_set():
            break                     # 스트림을 놓으면 연결이 닫혀 ollama 도 생성을 멈춘다
        msg = part.get("message", {}) or {}
        tok = msg.get("content", "")
        if tok:
            chunks.append(tok)
        thought += len(msg.get("thinking", "") or "")   # 사고는 별도 필드 — 본문에 안 섞는다
    full = "".join(chunks)
    if not full and thought:
        # 사고가 켜진 채 상한을 다 먹은 경우. 조용히 빈 문자열을 돌려주면 "멍청하다"로 보인다.
        print("", file=sys.stderr)
        print(f"  [경고] 본문 0자 / 사고 {thought}자 — 사고 토큰이 num_predict 를 다 먹었다. "
              f"think=False 로 두거나 num_predict 를 올려라.", file=sys.stderr)
    full = re.sub(r"<think>.*?</think>", "", full, flags=re.S)
    full = re.sub(r"^.*?</think>", "", full, flags=re.S)   # 여는 태그 누락 대비
    full = full.strip()
    print(full, end="", flush=True)
    return full


# ═══════════════════════════════════════════════════════════════
#  대화 기록 (자립형 — 음성 모듈 의존성 없음)
# ═══════════════════════════════════════════════════════════════
class History:
    def __init__(self, path):
        self.path = path
        self.msgs = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.msgs = json.load(f)
                print(f"  [기억] 이전 대화 {len(self.msgs)}개 불러옴")
            except Exception as e:
                # ★예전엔 여기서 조용히 빈 기록으로 시작했다 — 첫 대답의 save() 가 깨진 파일을 덮어써
                #   지난 대화가 통째로 사라지고 흔적도 안 남는다. 깨진 원본은 옆에 복사해 두고 알린다.
                self.msgs = []
                keep = f"{path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
                try:
                    import shutil
                    shutil.copy2(path, keep)
                except Exception:
                    keep = "(복사도 못 함)"
                print(f"  [기억] 대화 기록을 못 읽었다({e}) — 원본은 {keep} 에 두고 빈 기록으로 시작")

    def add(self, role, content):
        self.msgs.append({"role": role, "content": content})

    def recent(self):
        return self.msgs[-MAX_HISTORY:]

    def save(self):
        """임시 파일에 다 쓴 뒤 바꿔 끼운다(issues._save 와 같은 방식).
           ★데몬은 창을 X 로 닫아 끈다 — 정상 종료 없이 그 자리에서 죽는다. 예전처럼 제자리에 쓰면('w' 로 여는 순간
             파일이 비고, 70KB 를 몇 번에 나눠 쓴다) 그 사이에 죽을 때 반쯤 쓴 파일이 남고, 위 __init__ 이 못 읽는다.
             임시 이름에 스레드 번호 — 회의 스레드와 대답이 같은 순간 저장해도 서로의 임시 파일을 안 덮게."""
        tmp = f"{self.path}.{os.getpid()}-{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.msgs, f, ensure_ascii=False, indent=2)
        for i in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:          # 대화창이 지난 대화를 읽는 순간 등 — 잠깐 뒤 다시
                time.sleep(0.05 * (i + 1))
        try:                                 # 끝내 막히면 예전처럼 제자리에 — 아예 못 남기는 것보단 낫다
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.msgs, f, ensure_ascii=False, indent=2)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def reset(self):
        self.msgs = []
        if os.path.exists(self.path):
            os.remove(self.path)
        print("  [기억] 초기화 완료")


# ═══════════════════════════════════════════════════════════════
#  문지기 라우터 — 어느 등급으로 넘길지 판정
# ═══════════════════════════════════════════════════════════════
class Router:
    def route(self, text):
        flat = text.replace(" ", "")
        use_tools = any(k in flat for k in TOOL_KW)
        auto_web = None   # 키워드 없이도 문지기가 '검색 필요'로 판정하면 검색어가 담긴다

        # 1) 사용자 명시 오버라이드
        if any(k in flat for k in FORCE_HEAVY):
            tier, reason = "heavy", "사용자가 신중 모드 지정"
        elif any(k in flat for k in FORCE_LIGHT):
            tier, reason = "light", "사용자가 가벼운 모드 지정"
        # 2) 코드블록/긴 입력
        elif "```" in text or len(text) > 600:
            tier, reason = "heavy", "코드/긴 입력 감지"
        # 3) 기억 도구 필요 → tool 지원 모델
        elif use_tools:
            tier, reason = "medium", "기억 도구 필요"
        else:
            # 4) 문지기(gemma4:12b) 판단 — 난이도 + 자동 웹검색 필요여부
            try:
                r = _gen(
                    GATEKEEPER,
                    [{"role": "user", "content": ROUTER_PROMPT + text}],
                    fmt="json",
                    keep_alive=TIERS["light"]["keep_alive"],
                    temperature=0,
                )
                data = json.loads(r["message"]["content"])
                tier = data.get("tier", "medium")
                if tier not in TIERS:
                    tier = "medium"
                reason = data.get("reason", "")
                # 키워드 없이도 '인터넷 사실 조회 필요'로 보면 자동 웹검색 발동
                if data.get("web") is True:
                    auto_web = (data.get("query") or text).strip()
            except Exception as e:
                tier, reason = "medium", f"분류 실패→기본 ({e})"

        # 자동 웹검색은 명시 웹검색 경로와 동일하게 medium(gemma4:26b)으로 고정
        # — 단순 사실조회에 heavy 는 과하게 느리다. (검증은 옛 medium=qwen3:14b 때 한 것)
        if auto_web:
            tier = "medium"

        # 기억 작업이면 medium으로 승격.
        # — 옛 light(gemma3:4b)는 tool 자체가 없어서 강제였다. gemma4:12b 는 tools 를 지원하지만
        #   기억 쓰기는 틀리면 되돌리기 어려워서 승급은 그대로 둔다(속도보다 정확).
        if use_tools and tier == "light":
            tier, reason = "medium", reason + " +기억도구"

        return tier, reason, use_tools, auto_web


# ── "내 일정 알려줘" 처럼 문장으로 물어도 일정 창구로 보내기 ────────
#   가로채기가 문장 맨 앞 단어만 보던 탓에 "내 일정 알려줘"·"오늘 일정 뭐 있어" 가
#   일반 대화로 흘러 "확인할 수 없습니다" 가 나왔다(2026-09-18 호윤이 잡음).
#   넓히되 좁게: 짧은 한 마디 + 묻는 말일 때만. '일정한/일정하다'는 제외하고,
#   "일정 잡아줘" 같은 등록 의도는 안 가로챈다(그건 할일 접수 쪽 일).
_SCHED_WORD = re.compile(r"(일정(?![한하히])|스케줄)")
_SCHED_ASK_KW = re.compile(r"(알려|뭐|무슨|무엇|있어|있나|있지|보여|확인|어때|어떻게\s*되|남았)")
_SCHED_MAKE_KW = re.compile(r"(잡아|잡을|잡자|추가|등록|넣어|만들|비워|옮겨|취소)")
#   '일정'이 들어가도 내 약속 얘기가 아닌 것들("일정 관리 앱 추천해줘")
_SCHED_OFF_KW = re.compile(r"(추천|어플|앱|툴|서비스|방법|짜는|산정|관리)")


def _is_schedule_ask(t):
    s = (t or "").strip()
    if len(s) > 30 or not _SCHED_WORD.search(s) or _SCHED_MAKE_KW.search(s):
        return False
    if _SCHED_OFF_KW.search(s):
        return False
    return bool(_SCHED_ASK_KW.search(s)) or len(s.replace(" ", "")) <= 12


# ── 일정 대답 뒤에 잇는 되물음 ─────────────────────────────────
#   "다음주는 없어?"·"새로고침 못 하려나?" 에는 '일정'이란 말이 없어서 창구를 못 탔고,
#   light 모델이 받아 "저는 캘린더 연동 기능이 없습니다" 라고 지어냈다(2026-09-18 로그).
#   붙여둔 기능을 스스로 부인하는 게 제일 나쁜 실패라, 방금 일정을 답했으면 되물음도 받는다.
_SCHED_WHEN = re.compile(r"(오늘|내일|낼|모레|이번\s*주|담주|다음\s*주|이번\s*달|다음\s*달|주말|이달|한\s*달)")
_SCHED_AGAIN = re.compile(r"(새로\s*고침|갱신|다시\s*(봐|확인|읽)|refresh|반영)", re.I)
_SCHED_FOLLOW_ASK = re.compile(r"(알려|뭐|무슨|무엇|있어|있나|있지|없어|없나|없지|보여|확인|어때|어떻게\s*되|남았|더)")
#   일정 얘기 끝나고 딴 데로 넘어간 말까지 끌고 오지 않게
_SCHED_FOLLOW_OFF = re.compile(r"(날씨|기온|비\s|눈\s|뉴스|주가|증시|환율|코인|밥|점심|저녁|메뉴|영화|노래|며칠|몇\s*시|무슨\s*요일)")
SCHED_FOLLOW_SEC = 300          # 방금(닷 분 안) 일정을 답했을 때만


def _is_schedule_follow(t):
    s = (t or "").strip()
    if len(s) > 40 or _SCHED_MAKE_KW.search(s) or _SCHED_OFF_KW.search(s) or _SCHED_FOLLOW_OFF.search(s):
        return False
    if _SCHED_AGAIN.search(s):
        return True
    if not _SCHED_WHEN.search(s):
        return False
    #   "다음주는?" 처럼 때만 던지는 한 마디 — 묻는 말이 아예 없어도 되물음으로 본다
    return bool(_SCHED_FOLLOW_ASK.search(s)) or len(s.replace(" ", "")) <= 10


def _sched_window(t):
    """물어본 말에서 '언제까지' 를 읽는다. 못 읽으면 이레."""
    if re.search(r"오늘", t):
        return 0, "오늘"
    if re.search(r"모레", t):
        return 2, "모레까지"
    if re.search(r"내일|낼", t):
        return 1, "내일까지"
    if re.search(r"담주|다음\s*주", t):
        return 14, "다음 주까지"
    if re.search(r"다음\s*달", t):
        return 62, "다음 달까지"
    if re.search(r"이번\s*달|이달|한\s*달", t):
        return 31, "한 달 안"
    if re.search(r"이번\s*주|주말", t):
        return 7, "이번 주"
    return 7, "시각 있는 일정(이레 안)"


_DONE_NOUN = re.compile(r"스케줄|일정|할\s*일|투두|리마인더")     # 「알림」은 소식 알림 명령과 겹쳐서 뺌
_DONE_VERB = re.compile(r"지워|지우|삭제|없애|빼\s*줘|완료|끝났|끝냈|이미\s*했|다\s*했|했음|했어")


def _todo_done_by_words(t):
    """「○○ 스케줄 지워줘 이미 했음」 → 열린 할일 중 낱말이 제일 많이 겹치는 하나를 닫는다.
       하나로 못 고르면 목록을 보여 주고 번호를 묻는다. 할일 · 일정 말이 아니면 None(대화로)."""
    if len(t) > 80 or "?" in t or not (_DONE_NOUN.search(t) and _DONE_VERB.search(t)):
        return None
    import todo_box
    with todo_box._conn() as c:
        rows = c.execute("SELECT id, content FROM memory WHERE kind='todo' AND status='open'").fetchall()
    if not rows:
        return "열린 할일 · 일정이 없어 — 이미 닫혀 있어. (`할일 전체` 로 닫힌 것까지 볼 수 있어)"
    words = [w for w in re.split(r"[\s,.!~]+", _DONE_VERB.sub(" ", _DONE_NOUN.sub(" ", t))) if len(w) >= 2]

    def score(content):                   # 조사가 붙은 말(「전화하는거」)도 앞부분(「전화」)이 맞으면 친다
        s = 0
        for w in words:
            for k in range(len(w), 1, -1):
                if w[:k] in content:
                    s += k
                    break
        return s
    ranked = sorted(((score(r["content"]), r) for r in rows), key=lambda x: -x[0])
    best = ranked[0]
    if best[0] >= 2 and (len(ranked) == 1 or ranked[1][0] < best[0]):
        return todo_box.complete(best[1]["id"], "말로 닫음: " + t[:40])["msg"] + f"\n(잘못 닫았으면 「할일 다시 {best[1]['id']}」)"
    return ("어느 걸 닫을지 하나로 못 골랐어 — 「할일 완료 번호」로 말해 줘.\n"
            + "\n".join(f"#{r['id']} {r['content'][:50]}" for _, r in ranked[:5]))


def _with_calendar(todo_text, days=7, force=False):
    """'일정' 화면 위에 구글 캘린더(비공개 ICS) 약속을 붙인다.

    시각 있는 약속은 캘린더에만 있다(todo_box 는 내가 직접 넣은 것뿐). 캘린더가 안 붙어
    있으면 원래 화면 그대로 — 없는 걸 있는 척하지 않는다. 읽기 실패는 숨기지 말고 보여준다
    (조용히 0건이 되면 죽은 줄 모른다).
    """
    try:
        import calendar_ics
        if not calendar_ics.configured():
            return todo_text
        cal = calendar_ics.digest(days=days, force=force)
    except Exception as e:
        cal = f"[캘린더] 못 읽음: {e}"
    return (cal + "\n\n" + todo_text) if cal else todo_text


# ── 로컬 모델이 '못 뜬' 것인지 가려내기 ────────────────────────
#   게임 중엔 VRAM 이 없어 모델 로딩 자체가 죽는다(2026-09-18 실측):
#     llama_init_from_model: failed to initialize the context … error loading model (500)
#   이건 내 말이 이상해서 난 오류가 아니라 자리가 없어서 난 오류라, 클라우드로 우회할 값어치가 있다.
#   반대로 내용 때문에 난 오류까지 구독으로 떠넘기면 안 되니 아래 낱말이 보일 때만 우회한다.
_LOCAL_DOWN = re.compile(
    r"(llama|error loading model|failed to initialize|out of memory|oom|cuda|vram|"
    r"status code: 5\d\d|connection refused|10061|actively refused|"
    r"model requires more system memory|terminated)", re.I)


def _is_local_down(e):
    return bool(_LOCAL_DOWN.search(str(e)))


# ═══════════════════════════════════════════════════════════════
#  COMET 본체
# ═══════════════════════════════════════════════════════════════
class Comet:
    def __init__(self):
        self.history = History(HISTORY_FILE)
        self.router  = Router()
        self.speak_enabled = False    # 타이핑 답변도 소리내서 읽을지
        self._ear   = None            # 듣기 (lazy)
        self._mouth = None            # 말하기 (lazy)
        self.game_mode = "auto"       # auto|on|off — 게임 중 가벼운 모델로 (GPU 양보)
        self.auto_approve = False     # 코딩 에이전트 파일수정/셸 자동승인 여부 (콘솔=False, 데몬=True)
        self._pending_coding = None   # ask 로 멈춘 코딩 세션 {box, messages}
        self._pending_shell  = None   # 셸 앞에서 멈춘 위임 {task, target} — "셸 허용" 이면 재실행
        # 자율/상주 모드(먼저말걸기·부르면듣기) — 로직은 autonomy.py 에 격리. 저장된 모드 복원.
        import autonomy
        self.autonomy = autonomy.Autonomy(
            respond_fn=self.respond,
            talk_respond_fn=self.respond_cancelable,
            cancel_fn=CANCEL.set,
            speak_fn=lambda t: self._get_mouth().speak(t),
            log=print,
            gpu_busy_fn=_gpu_busy)   # brief 모드가 게임 중이면 무거운 브리핑을 미루도록
        self.autonomy.restore()

    # ── 회의(클로드+아스트라)를 뒤에서 돌리고, 끝나면 배너+음성+대화기록 ──
    def _start_debate(self, topic, text):
        if getattr(self, "_debating", False):
            return "지금 회의 하나 돌고 있어. 끝나면 말해줄게 (전문: 회의 결과)."
        self._debating = True
        ctx = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in self.history.recent()[-4:])
        self.history.add("user", text)
        self.history.save()

        def run():
            import debate
            import issues
            try:
                res = debate.debate(topic, context=ctx)
                if res.get("ok"):
                    full = debate.format_full(res)
                    head = res["result"].splitlines()[0].replace("결론:", "").strip()
                    issues.popup("COMET · 회의 (" + " + ".join(res["who"]) + ")", head[:80],
                                 "전문: '회의 결과'", level=2, sec=12)
                    self.history.add("assistant", full)
                    self.history.save()
                    say = res.get("speech") or head
                else:
                    say = "회의가 안 됐어. " + res.get("msg", "")
                    self.history.add("assistant", say)
                    self.history.save()
                try:
                    self._get_mouth().speak("회의 끝났어. " + say)
                except Exception as e:
                    print(f"  [회의 음성 오류] {e}")
            except Exception as e:
                print(f"  [회의 오류] {e}")
            finally:
                self._debating = False

        import threading
        threading.Thread(target=run, name="debate", daemon=True).start()
        return "클로드랑 아스트라한테 회의 붙였어. 1~3분 뒤에 결론 말해줄게 (전문: 회의 결과)."

    # ── 음성 모듈 lazy 로딩 (안 쓰면 whisper/torch 안 불러옴) ──
    def _get_mouth(self):
        if self._mouth is None:
            from voice import Mouth
            self._mouth = Mouth()
        return self._mouth

    def _get_ear(self):
        if self._ear is None:
            from voice import Ear
            self._ear = Ear()
        return self._ear

    # ── 게임/바쁨 모드: 무거운 등급을 light 로 강등 + keep_alive 짧게 ──
    def _game_adjust(self, tier, reason, flat):
        """반환 (tier, reason, keep_alive_override|None)."""
        mode = getattr(self, "game_mode", "auto")
        if mode == "off" or tier == "light":
            return tier, reason, None
        if mode == "auto":
            if any(k in flat for k in FORCE_HEAVY):   # 이번 턴 '무겁게' 명시 → 존중
                return tier, reason, None
            busy, info = _gpu_busy()
            if not busy:
                return tier, reason, None
            return "light", f"{reason} · 게임모드(자동:{info})→light", GAME_KEEP_ALIVE
        return "light", f"{reason} · 게임모드 ON→light", GAME_KEEP_ALIVE   # mode == "on"

    def respond_cancelable(self, text):
        """말걸기 창용 — 도중에 Esc(CANCEL)가 오면 이 턴을 대화기록에서 지우고 None."""
        n = len(self.history.msgs)
        CANCEL.clear()
        try:
            reply = self.respond(text, speak=False)
        finally:
            cancelled = CANCEL.is_set()
            CANCEL.clear()
        if cancelled:
            del self.history.msgs[n:]
            try:
                self.history.save()
            except Exception:
                pass
            return None
        return reply

    def respond(self, text, speak=None):
        # ── 도움말 ──────────────────────────────────────────────────────────────
        _low0 = text.strip().replace(" ", "").lower()
        if _low0 in ("도움말", "help", "기능", "명령어", "뭐할수있어", "뭘할수있어",
                     "사용법", "기능뭐있어", "기능이뭐야", "명령어뭐있어"):
            return HELP_TEXT

        # ── 코딩 명령(콘솔·폰 공용): "코딩 [@폴더] [/code|/debug|…] <할 일>" ──
        #    코딩 에이전트(coder_bridge)로 넘긴다. 비대화형 auto+백업(.comet_bak).
        t = text.strip()
        import remote_guard
        if t.startswith("코딩") and remote_guard.active():
            return remote_guard.REFUSE
        if t.startswith("코딩 ") or t == "코딩" or t.startswith("코딩:"):
            import coder_bridge
            rest = t[2:].lstrip(": ").strip()
            if not rest:
                return ("코딩 명령: 코딩 [@폴더경로] [/code|/debug|/study|/automate] <할 일>\n"
                        f"확인모드: {'자동승인(데몬)' if self.auto_approve else '수정/실행마다 확인(y/N)'}")
            root, skill, task = coder_bridge.parse(rest)
            self.history.add("user", text)
            try:
                result = coder_bridge.run_text_full(task, root=root, skill=skill,
                                                    auto_approve=self.auto_approve)
                reply = result["text"]
                if result.get("stopped") == "ask" and result.get("messages"):
                    self._pending_coding = {"box": result["box"], "messages": result["messages"]}
                    reply += "\n\n❓ 답변하면 이어서 작업해."
            except Exception as e:
                reply = f"[코딩 오류] {e}"
            self.history.add("assistant", reply)
            self.history.save()
            return reply

        # ── 회의(council): 일반 주제를 여러 에이전트가 분석→초안→반론↔보강→합성 ──
        #    (코딩은 '코딩 회의 …'. 여긴 전략·판단 등 일반. 순차라 몇 분 걸림.)
        # ── 회의(클로드 + 아스트라) — 각자 입장 → 서로 반박 → 결론. 뒤에서 돌고 끝나면 배너+음성 ──
        #    의견을 묻는 말("어떻게 생각해", "뭐가 나아")도 여기로. 옛 로컬 회의는 '로컬 회의 …'.
        if t.replace(" ", "") in ("회의결과", "회의결론", "지난회의"):
            import debate
            return debate.format_full(debate.last())
        if t.startswith("회의 ") or t == "회의":
            topic = t[2:].strip()
            if not topic:
                return ("회의 <주제> — 클로드랑 아스트라가 각자 입장 → 서로 반박 → 결론 (1~3분, 끝나면 말해줌). "
                        "전문은 '회의 결과'. 로컬 모델로는 '로컬 회의 …', 코딩은 '코딩 회의 …'.")
            return self._start_debate(topic, text)
        if t.startswith("로컬 회의 ") or t.startswith("로컬회의 "):
            topic = t.split("회의", 1)[1].strip()
            if not topic:
                return "로컬 회의 <주제> — 로컬 에이전트들이 분석→초안→반론→합성(몇 분 걸려)."
            self.history.add("user", text)
            import council
            _lines = ["🧠 회의 (일반)"]

            def _cev(kind, data):
                if kind == "role":
                    _lines.append(f"  ◆ {data['role']} [{data['brain']}]")
                elif kind == "clean":
                    _lines.append(f"  ✓ R{data['round']}: 치명 결함 없음 → 조기종료")
                elif kind == "done":
                    _lines.append(f"  ✔ 완료 ({data.get('rounds', 1)}라운드)")
            try:
                out = council.council(topic, on_event=_cev, mode="general")
            except Exception as e:
                out = {"ok": False, "msg": str(e)}
            if out.get("ok"):
                crit = (out["transcript"].get("critic") or "").strip()
                reply = ("\n".join(_lines) + "\n\n🔎 반론 요지:\n" + crit[:280]
                         + "\n\n🧭 결론:\n" + out["result"])
            else:
                reply = "회의 실패: " + out.get("msg", "?")
            self.history.add("assistant", reply)
            self.history.save()
            return reply

        # ── Only Money 9단 파이프라인 분석 ────────────────────────────────────────
        _OM_CMD = ("온리머니분석", "온리머니분석해", "onlymoney분석", "onlymoney분석해",
                   "온리머니애널리스트", "om분석", "시장분석9단", "9단분석")
        if t.replace(" ", "").lower() in _OM_CMD or t.strip().lower() in ("only money 분석", "only money 분석해줘"):
            self.history.add("user", text)
            import onlymoney_analyst
            lines = []
            def _om_print(s="", end="\n"):
                print(s, end=end, flush=True)
                lines.append(s + end)
            try:
                onlymoney_analyst.run(print_fn=_om_print)
            except Exception as e:
                lines.append(f"[온리머니 분석 오류] {e}")
            full = "".join(lines)
            self.history.add("assistant", f"[Only Money 9단 파이프라인 분석 완료 — {len(full)}자]")
            self.history.save()
            return full

        # ── 게임/바쁨 모드 토글 (콘솔·폰 공용) ──
        low = t.replace(" ", "").lower()
        if low in ("게임모드", "게임모드상태"):
            return f"게임모드: {self.game_mode}  (켜/꺼/자동 으로 변경)"
        if low in ("게임모드켜", "게임모드on", "게임모드켜기"):
            self.game_mode = "on"
            return "게임모드 ON — 가벼운 모델(4b) 고정, VRAM 양보."
        if low in ("게임모드꺼", "게임모드off", "게임모드끄기", "게임모드해제"):
            self.game_mode = "off"
            return "게임모드 OFF — 평소 3단 라우터로."
        if low in ("게임모드자동", "게임모드auto"):
            self.game_mode = "auto"
            return "게임모드 자동 — GPU 바쁘면 알아서 가볍게."
        # ── 고전 암호(아핀·시저·ROT13) — 모델 안 거침(= 즉답, GPU 안 씀) — cipher.py ──
        #   2026-09-18 로그: 12b 가 "poten k1=7 k2=2" 를 fzhje→zwfep→dwfep 로 세 번 만에 맞췄다.
        #   중간에 "(15*7+2)%26 = 105%26" 이라고 쓴 줄까지 있다(107 이 맞다). 산수는 코드가 한다.
        #   "아핀 암호가 뭐야?" 같은 설명 질문은 command() 가 None → 평소대로 모델이 답한다.
        try:
            import cipher
            ans = cipher.command(t)
        except Exception as e:
            print(f"  [암호 계산 건너뜀] {e}")   # 여기서 죽으면 대화 전체가 막힌다
            ans = None
        if ans is not None:
            self.history.add("user", text)       # 뒤에 "왜 그래?" 로 이어질 수 있게 기록은 남긴다
            self.history.add("assistant", ans)
            self.history.save()
            return ans
        # ── 전화 비서 점검 · 시험 — phone_cmd.py (모델 안 거침, 시험 전화는 텔레그램에선 막음) ──
        if t.replace(" ", "").startswith(("전화", "시험전화")):
            import phone_cmd
            ans = phone_cmd.command(t)
            if ans is not None:          # None = "전화번호 뭐야" 같은 일반 말 → 대화로 흘림
                return ans
        # ── 자가 업그레이드 — upgrade.py (시작 · 적용 · 되돌리기는 텔레그램에선 막음) ──
        #    「업그레이드 …」 말고 제안 뒤 「바로 패치해」 「1번 고쳐」도 여기서 잡는다(upgrade._go_pick · 10/13)
        import upgrade
        ans = upgrade.command(t, remote=remote_guard.active())
        if ans is not None:              # None = "업그레이드 어떻게 해?" 같은 일반 말 → 대화로 흘림
            if ans.startswith(("반영했어", "되돌렸어")):
                #   진짜 코드가 바뀌었으니 데몬이 스스로 껐다 켠다(restart.py · 10/14 호윤 지시)
                import restart
                ok, why = restart.soon("업그레이드 " + ("적용" if ans.startswith("반영") else "되돌리기"))
                ans += ("\n데몬이 2~3초 뒤 스스로 껐다 켜 — 새 코드로 다시 뜨면 대화창이 저절로 다시 붙어."
                        if ok else f"\n★스스로 재시작 안 함: {why}. 데몬 창 닫고 run_daemon.bat 으로 다시 켜 줘.")
            return ans
        # ── 이슈 접수함 (쓰다가 이상한 것 한 줄로 던져두기 → 고쳐지면 닫기) — issue_box.py ──
        if t.strip().startswith("이슈"):
            import issue_box
            ans = issue_box.command(t)
            if ans is not None:          # None = "이슈가 뭐야?" 같은 일반 질문 → 대화로 흘림
                return ans
        # ── 문장으로 물은 일정("내 일정 알려줘"·"오늘 일정 뭐 있어") + 그 뒤 되물음 ──
        _sched_hot = (time.time() - getattr(self, "_sched_at", 0.0)) < SCHED_FOLLOW_SEC
        if _is_schedule_ask(t) or (_sched_hot and _is_schedule_follow(t)):
            import todo_box
            days, label = _sched_window(t)
            self._sched_at = time.time()
            return _with_calendar(todo_box.upcoming(days=days, only_timed=True, label=label),
                                  days=days, force=bool(_SCHED_AGAIN.search(t)))
        # ── 할일·일정 창구 (프로젝트별로 던져두고 마감·시각으로 되찾기) — todo_box.py ──
        #   모델 안 거침(= GPU 안 씀, 즉답). "할일이 뭐야?" 같은 일반 질문은 None → 대화로 흘림.
        if re.match(r"^(할\s*일|투두|todo|일정|스케줄|구글)", t.strip(), re.I):
            import gtasks
            ans = gtasks.command(t)              # '할일 가져오기·구글연결'은 여기서 먼저
            if ans is not None:
                return ans
            import todo_box
            ans = todo_box.command(t)
            if ans is not None:          # 목록 덤프를 대화기록에 안 쌓는다(이슈·알림 명령과 같은 관례)
                if re.match(r"^(일정|스케줄)", t.strip()):
                    ans = _with_calendar(ans)   # 구글 캘린더(비공개 ICS) 약속을 위에 붙인다
                    self._sched_at = time.time()   # 이 뒤의 되물음도 같은 창구로 받는다
                return ans
        # ── 「고객센터 전화하는 거 스케줄 지워줘 이미 했음」 — 할일 · 일정 닫기를 말로 ──
        #   10/14 07:05 이 말이 light 모델로 가서 「삭제하겠습니다」만 하고 아무것도 안 지웠다(도구 없음).
        ans = _todo_done_by_words(t)
        if ans is not None:
            return ans
        # ── 이슈 알림(AI·경제 강도별) — 로직은 autonomy.issues_command / issues.py ──
        if low == "알림" or low.startswith(("알림ai", "알림에이아이", "알림경제", "알림끔", "알림꺼",
                                             "알림켜", "알림켬", "알림온", "알림on", "알림주기",
                                             "알림지금", "알림점검", "알림테스트", "알림기록",
                                             "알림로그", "알림조용", "알림상태", "알림다시",
                                             "알림뉴스")):
            return self.autonomy.issues_command(low)
        # '뉴스' 한 단어(+보여줘류)만 — 이미 알린 것까지 지금 뜬 AI·경제 소식 모아보기.
        #   "OO 뉴스 찾아줘" 같은 건 그대로 웹 검색 쪽으로 간다(정확히 일치할 때만 가로챔).
        if low in ("뉴스", "뉴스보여줘", "뉴스알려줘", "뉴스모아", "뉴스모아줘", "지금뉴스", "오늘뉴스"):
            return self.autonomy.issues_command("알림뉴스")
        # 방금 알린 소식 되묻기 — "그거 내용 봐봐" / "방금 그거 뭔데" / "그 뉴스 자세히"
        #   원문 앞부분을 그 자리에서 받아와 읽어준다. 최근에 알린 게 없으면 평소대로 흘려보낸다
        #   (모르는 채로 지어내는 것보다, 대화로 답하게 두는 편이 낫다).
        #   "새로운 뉴스 보여줘" 처럼 새로 모아 달라는 말은 여기서 가로채면 안 된다.
        if (NEWS_MORE_WANT.search(low)
                and (NEWS_MORE_REF.search(low)
                     or (re.search(r"뉴스|기사|소식", low) and re.search(r"내용|자세|상세|전문", low)))
                and not re.search(r"새로운|새소식|새뉴스|요즘|오늘|최근|최신", low)
                and not NEWS_SEARCH.search(low)):
            import issues
            more = issues.detail_of_last()
            if more:
                self.history.add("user", text)
                self.history.add("assistant", more)
                self.history.save()
                return more
        # 말로 묻는 뉴스 — "새로운 뉴스 없어?", "요즘 소식 뭐 있어", "한국 뉴스 알려줘"
        #   ("근데 새로운 뉴스 없어" 를 대화로 흘려 엉뚱하게 답했다 — 2026-09-18)
        #   "OO 뉴스 찾아줘/검색" 처럼 주제가 붙은 건 웹 검색 쪽으로 둔다.
        if NEWS_ASK.search(low) and not NEWS_SEARCH.search(low):
            region = ("ko" if re.search(r"한국|국내|우리나라", low)
                      else "en" if re.search(r"해외|외국|미국|글로벌", low) else "")
            return self.autonomy.issues_command("알림뉴스" + region)
        # ── 자율/상주 모드 토글 (콘솔·폰 공용) ──
        if low.startswith("자율모드") or low.startswith("자율 모드"):
            return self.autonomy.command(low)
        # 의견을 묻는 말 → 클로드+아스트라 회의 (위의 명령들을 다 지나온 뒤에만)
        #   회의가 이미 돌고 있으면 막지 않고 평소처럼 바로 답한다(회의 중에도 대화는 된다)
        if OPINION_ASK.search(low) and not NEWS_ASK.search(low) and not getattr(self, "_debating", False):
            return self._start_debate(t, text)
        # ── 두뇌 전환: 로컬(ollama) ↔ 클라우드(클로드/GPT/딥시크) (콘솔·폰 공용) ──
        #   최종 답변만 클라우드로. 문지기·도구선택은 로컬 유지(과금 방지). 키 없으면 자동 로컬.
        if low in ("두뇌", "두뇌상태", "브레인"):
            import cloud
            return cloud.status()
        _BRAIN_CMD = {
            "클로드로": "claude", "클로드": "claude", "claude": "claude",
            "지피티로": "gpt", "gpt로": "gpt", "gpt": "gpt", "챗gpt": "gpt",
            "딥시크로": "deepseek", "deepseek": "deepseek", "딥시크": "deepseek",
            "로컬로": "local", "로컬": "local", "ollama": "local", "오프라인": "local",
        }
        if low in _BRAIN_CMD:
            import cloud
            return cloud.set_active(_BRAIN_CMD[low])
        # ── 클라우드 키/모델 갈아끼우기 (재시작 불필요·키는 대화기록에 안 남김) ──
        #   원문 t 로 처리(키 대소문자 보존). 프로바이더로 해석될 때만 가로채 일반대화 보호.
        if (t.startswith("키 ") or t.startswith("키삭제")) and remote_guard.active():
            return remote_guard.REFUSE       # 클라우드 키를 남의 것으로 바꾸면 대화가 그쪽으로 간다(10/10 T-18)
        if t.startswith("키삭제") or t.startswith("키 지워") or t.startswith("키 빼"):
            import cloud
            who = t.replace("키삭제", "").replace("키 지워", "").replace("키 빼", "").strip()
            name = cloud.resolve_provider(who)
            return cloud.clear_key(name) if name else "어느 키를 뺄까? 클로드/gpt/딥시크."
        if t.startswith("키 "):
            import cloud
            parts = t.split(maxsplit=2)
            name = cloud.resolve_provider(parts[1]) if len(parts) >= 2 else None
            if name:                       # 프로바이더로 해석될 때만 키 명령으로 처리
                if len(parts) < 3:
                    return f"키 값도 같이: 키 {parts[1]} sk-...  (키는 대화기록에 안 남아)"
                return cloud.set_key(name, parts[2])
        if t.startswith("모델 "):
            import cloud
            parts = t.split(maxsplit=2)
            name = cloud.resolve_provider(parts[1]) if len(parts) >= 2 else None
            if name:                       # 프로바이더로 해석될 때만(그 외엔 일반대화로 흘림)
                if len(parts) < 3:
                    return f"바꿀 모델도 같이: 모델 {parts[1]} <모델이름>"
                return cloud.set_model(name, parts[2])
        # ── 프로필 동기화: 호윤 최신 메모리 → COMET profile/ (코멧이 호윤을 최신으로) ──
        if low in ("프로필동기화정리", "프로필정리", "프로필미러"):
            import profile_sync
            return profile_sync.sync(prune=True) + "\n(데몬 재시작하면 새 프로필이 적용돼.)"
        if low in ("프로필동기화", "프로필갱신", "프로필싱크", "프로필업데이트", "프로필"):
            import profile_sync
            return profile_sync.sync() + "\n('리로드'하면 재시작 없이 바로 적용돼.)"
        # ── 핫리로드: 데몬 안 끄고 프로필·SYSTEM 라이브 반영(설정은 원래 매번 읽어 이미 hot) ──
        if low in ("리로드", "핫리로드", "프로필리로드", "코멧리로드", "reload"):
            import profile_sync
            synced = profile_sync.sync().splitlines()[0]
            n = reload_system()
            return (f"핫리로드 완료 — {synced} · SYSTEM 재구성({n}자, 최신 프로필·인격 반영). "
                    "설정(클라우드 키·게임/자율모드·가드)은 매번 파일 읽어 이미 최신. "
                    "※도구·로직 코드(.py 함수) 변경은 '재시작'이 필요해(핫스왑은 상태 깨질 위험이라 안 함).")
        # ── 재시작: 코드 변경 반영(응답 직후 클린 재기동, 실패 시 watchdog 부활) ──
        if low in ("재시작", "데몬재시작", "리스타트", "restart"):
            import threading

            def _restart():
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception:
                    os._exit(0)        # 폴백: 종료 → watchdog 가 최신 코드로 부활
            threading.Timer(1.2, _restart).start()
            return ("데몬 재시작 신호 — 이 응답 직후 최신 코드로 재기동해. "
                    "watchdog 켜놨으면 자동 부활, 폰은 몇 초 뒤 다시 말 걸어봐.")

        # ── 셸 허용: 위임했던 일이 셸 앞에서 멈췄을 때, 호윤이 허락하면 셸 켜고 다시 ──
        #   호윤 지시(9/22) "파일수정까지 자동, 셸은 확인받고" 의 '확인' 쪽 절반.
        if getattr(self, "_pending_shell", None) and remote_guard.active() and t.replace(" ", "") in (
                "셸허용", "쉘허용", "셸켜", "쉘켜", "셸돌려", "명령허용", "허용", "돌려", "그래돌려"):
            return remote_guard.REFUSE       # 셸 허락은 PC 앞에서만(같은 텔레그램 통로로 받으면 확인이 아니다 · 10/10)
        if getattr(self, "_pending_shell", None) and t.replace(" ", "") in (
                "셸허용", "쉘허용", "셸켜", "쉘켜", "셸돌려", "명령허용", "허용", "돌려", "그래돌려"):
            ps = self._pending_shell
            self._pending_shell = None
            self.history.add("user", text)
            r = self._delegate(ps["task"], ps.get("target"), shell_ok=True)
            full = r.get("text") or r.get("msg", "못 했어.")
            print(f"\nCOMET:\n{full}")
            self.history.add("assistant", full)
            self.history.save()
            return full

        # ── 코딩 세션 이어받기: ask 로 멈춘 작업이 있으면 다음 입력으로 resume ──
        if self._pending_coding and remote_guard.active():
            return remote_guard.REFUSE
        if self._pending_coding:
            pc = self._pending_coding
            self._pending_coding = None
            self.history.add("user", text)
            import coder_bridge
            try:
                result = coder_bridge.resume_text(text, box=pc["box"],
                                                  messages=pc["messages"],
                                                  auto_approve=self.auto_approve)
                full = result["text"]
                if result.get("stopped") == "ask" and result.get("messages"):
                    self._pending_coding = {"box": result["box"], "messages": result["messages"]}
                    full += "\n\n❓ 답변하면 이어서 작업해."
            except Exception as e:
                full = f"[코딩 재개 오류] {e}"
            self.history.add("assistant", full)
            self.history.save()
            will_speak = self.speak_enabled if speak is None else speak
            if will_speak and full.strip():
                self._get_mouth().speak(full)
            return full

        flat = text.replace(" ", "")
        imgsearch = any(k in flat for k in IMGSEARCH_KW)
        screen = any(k in flat for k in SCREEN_KW)

        self.history.add("user", text)

        try:
            if imgsearch:
                print("  · 이미지 역검색 (식별→원본·출처추적)")
                full = self._image_search(text)
            elif screen:
                print("  · 화면 인식 (gemma4 vision)")
                full = self._look(text)
            else:
                tier, reason, use_tools, auto_web = self.router.route(text)
                tier, reason, _ka = self._game_adjust(tier, reason, flat)
                cfg = dict(TIERS[tier])
                if _ka:
                    cfg["keep_alive"] = _ka
                if auto_web:
                    flag = " +자동웹"
                elif use_tools:
                    flag = " +기억"
                else:
                    flag = ""
                print(f"  · 문지기: {tier} ({cfg['model']}){flag} — {reason}")
                if auto_web:
                    print(f"  · 자동 웹검색 발동 — 키워드 없이 사실 조회 판단: {auto_web}")

                def _run(c):
                    if auto_web:
                        return self._chat_with_tools(c, text, forced=("web_search", auto_web))
                    if use_tools:
                        return self._chat_with_tools(c, text)
                    return self._chat_stream(c)

                try:
                    full = _run(cfg)
                except Exception as e:
                    #  ★2026-09-22 — 무거운 등급이 VRAM 이 없어 못 뜨면 **클라우드로 새지 말고
                    #    light(12b) 로 내려서 다시 돌린다.** 클라우드 우회(_cloud_reply)는 도구·기억을
                    #    통째로 못 써서 "일정 확인 못 합니다" 같은 답이 나온다 — 등급을 내리는 쪽이
                    #    답 품질은 조금 낮아도 **시킨 걸 실제로 실행**한다. 호윤 지시(9/22).
                    if tier == "light" or not _is_local_down(e):
                        raise
                    print(f"\n  · {cfg['model']} 이 못 떴다 → light 로 내려서 다시 ({str(e)[:70]})")
                    light = dict(TIERS["light"])
                    if _ka:
                        light["keep_alive"] = _ka
                    full = _run(light)
        except Exception as e:
            #   게임 중 VRAM 이 없어 모델이 아예 못 뜨는 일이 잦다 — 그때 대화를 끊지 않고 우회한다
            full = self._cloud_reply(text) if _is_local_down(e) else ""
            if not full:
                print(f"\n  [오류] 응답 실패: {e}")
                return f"[오류] {e}"

        self.history.add("assistant", full)
        self.history.save()

        # 소리내서 읽기 (음성 입력 턴이거나 말하기 모드일 때)
        will_speak = self.speak_enabled if speak is None else speak
        if will_speak and full.strip():
            self._get_mouth().speak(full)

        return full

    # ── 화면 인식: 캡처 → gemma4 멀티모달 ───────────────────────
    def _look(self, text):
        import vision
        answer = vision.look(text, PERSONA)
        print(f"\nCOMET: {answer}")
        return answer

    # ── 이미지 역검색: 식별 → (키 있으면)원본추적 / 무키시도 / 검색 → 추론 ──
    def _image_search(self, text, path=None, hint=""):
        # path 가 주어지면(폰 업로드 등) 그 파일을 바로 쓴다. 없으면 문장에서 뽑고,
        # 그래도 없으면 imagesearch 가 클립보드→화면 순으로 자동 확보(PC 한정).
        import imagesearch
        import trace as _trace

        tr = _trace.Trace(True, title="이미지 역검색  ·  이 사진 뭐야 / 어디서 왔어")
        tr.step("이미지 확보", "업로드/파일경로 → 클립보드 → 화면 순")
        if path is None:
            path = self._extract_img_path(text)
        if path:
            tr.item(f"파일: {os.path.basename(path)}")
        res = imagesearch.search(path=path, hint=hint)

        if not res or not res.get("ok"):
            msg = (res or {}).get("msg", "역검색에 실패했어.")
            tr.warn(msg)
            print(f"\nCOMET: {msg}")
            return msg

        engine = res.get("engine", "")
        info = res.get("identify") or {}
        # 출처 목록 정규화 — A/B(직접 sources) 와 C(research.sources) 둘 다 흡수
        srcs = []
        if res.get("sources"):                      # A/B: 진짜 원본추적
            tr.step("원본 출처 회수", f"{engine} · {len(res['sources'])}건")
            for i, s in enumerate(res["sources"], 1):
                dom = s.get("source") or s.get("url", "")
                srcs.append(f"[출처{i}] {dom}\n{s.get('title','')}\n{s.get('url','')}")
                tr.item(dom)
        elif res.get("research") and res["research"].get("ok"):   # C: 식별→검색
            if info.get("subject"):
                tr.step("식별", f"{info.get('subject')} ({info.get('kind','?')})")
            for s in res["research"]["sources"]:
                srcs.append(
                    f"[출처{s['n']}] (분류 {s['tier']}={s['tier_label']} · {s['domain']} · "
                    f"날짜 {s.get('date') or '미상'})\n{s['title']}\n{s['url']}\n{s['text']}")

        # 식별 요약 한 줄(있으면) — 모델에게 '무엇인지'를 먼저 박아줌
        id_line = ""
        if info.get("subject"):
            id_line = (f"식별 결과: {info.get('subject')} (유형 {info.get('kind','?')})"
                       + (f" · 보이는 글자: {info['text']}" if info.get("text") else "")
                       + (f" · 특징: {', '.join(info.get('features', []))}" if info.get("features") else "")
                       + "\n\n")

        real_reverse = bool(res.get("sources"))   # A/B = 진짜 원본 매치
        srcs_text = "\n\n".join(srcs) if srcs else "(웹에서 관련 자료를 못 찾음)"
        grounding = (
            f"사용자가 이미지 역검색을 요청했다(원래 말: \"{text}\").\n\n"
            + id_line +
            ("아래는 그 이미지의 '원본/동일 이미지가 올라온 출처'를 역검색으로 찾은 결과다.\n"
             if real_reverse else
             "아래는 이미지를 식별해 그 정체로 웹을 검색한 결과다(동일 원본 추적이 아니라 '무엇인지'+관련 자료).\n")
            + "=== 출처 ===\n" + srcs_text + "\n=== 출처 끝 ===\n\n"
            "이렇게 답해라(자연스러운 문장, 템플릿 나열 금지):\n"
            "① 먼저 이 이미지가 '무엇인지' 한 줄로 단정(식별 결과 기반).\n"
            "② " + ("이 이미지가 발견된 출처를 [출처N]과 함께 짚어줘.\n"
                     if real_reverse else
                     "관련해 확인된 사실을 [출처N]과 함께 짧게 깔아줘.\n") +
            "③ 그 위에 네 추론을 1인칭으로(이게 어디/무엇으로 보이는지, 왜 그렇게 보는지).\n"
            "④ 정직하게: 동일 원본을 직접 찾은 게 아니면 '원본 자체를 특정하진 못했고 식별·관련자료까지'라고 밝혀라. "
            "출처에 없는 URL·수치·이름은 절대 지어내지 마라.\n"
            "한국어로 간결하게.")

        cfg = dict(TIERS["heavy"])   # 식별·추론은 신중하게
        messages = ([{"role": "system", "content": SYSTEM}]
                    + self.history.recent()
                    + [{"role": "user", "content": grounding}])
        tr.think("종합 — 무엇인지 + 출처 추적, 못 찾은 건 정직하게")
        r = _gen(cfg["model"], messages, keep_alive=cfg["keep_alive"], temperature=0)
        full = r["message"].get("content", "")
        full = re.sub(r"<think>.*?</think>", "", full, flags=re.S)
        full = re.sub(r"^.*?</think>", "", full, flags=re.S)
        full = full.strip()
        tr.done("역검색 완료")
        print(f"\nCOMET: {full}")
        return full

    @staticmethod
    def _extract_img_path(text):
        """문장에서 이미지 파일 경로/이름을 뽑아낸다(없으면 None → 클립보드/화면)."""
        # 따옴표로 감싼 경로 우선
        m = re.search(r'["\']([^"\']+\.(?:png|jpg|jpeg|webp|gif|bmp))["\']', text, re.I)
        if m:
            return m.group(1)
        # 공백 없는 경로/파일명
        m = re.search(r'(\S+\.(?:png|jpg|jpeg|webp|gif|bmp))', text, re.I)
        if m:
            cand = m.group(1)
            return cand if os.path.exists(cand) else cand  # 존재여부는 grab_image가 판단
        return None

    # ── 일반 대화: 스트리밍 ──────────────────────────────────────
    def _chat_stream(self, cfg):
        messages = [{"role": "system", "content": SYSTEM}] + self.history.recent()
        print("\nCOMET: ", end="", flush=True)
        full = _stream_filtered(cfg["model"], messages, cfg)
        print()
        return full

    # ═══════════════════════════════════════════════════════════
    #  서브에이전트 위임 — 말이 아니라 실제로 고치게 한다 (2026-09-22)
    #
    #  ★호윤: *"얘는 내가 뭘 원하는지만 잘 알고 실행만 해주면돼,
    #           서브 에이젼트 쓰는 기능이 있어야 될 거 같은데"*
    #  부품은 이미 다 있었다 — coder_bridge(로컬 ReAct·백업) + debate.ask_claude(헤드리스 클로드).
    #  없던 건 **자연스러운 말에서 거기로 가는 길**뿐이라, 동작선택기에 delegate 를 만들어 여기로 모은다.
    #
    #  호윤이 정한 두 가지(9/22):
    #   1) 엔진은 일에 따라 가른다 — 코드·판단은 클로드 CLI, 단순·반복 파일작업은 로컬 COMET-Coder.
    #   2) 권한은 **파일수정까지 자동(백업 있음), 셸은 확인받고.**
    #      → 클로드: Read/Edit/Write/Glob/Grep 만 열고 Bash 는 안 연다.
    #      → 로컬:  쓰기는 승인, 셸 요청이 나오면 거기서 멈추고 호윤에게 물어본다.
    #      둘 다 "셸 허용" 이라고 답하면 셸을 켜고 같은 일을 다시 돌린다(_pending_shell).
    # ═══════════════════════════════════════════════════════════
    #  로컬로 보낼 일 — 판단이 필요 없는 단순·반복 파일작업
    _LOCAL_TASK_KW = ("정리", "이름바꾸", "이름 바꾸", "일괄", "모아", "복사", "옮겨",
                      "세어", "개수", "목록", "변환", "지워", "삭제")
    #  클로드로 보낼 일 — 코드를 읽고 판단해야 하는 것
    _CLAUDE_TASK_KW = ("고쳐", "수리", "버그", "에러", "안 돼", "안돼", "구현", "기능", "추가",
                       "리팩터", "테스트", "코드", "함수", "스크립트", "만들")

    #  대상 폴더 — 말에서 프로젝트를 못 집으면 바탕화면(coder_bridge 기본값)
    _DELEGATE_ROOTS = {
        "코멧": os.path.dirname(os.path.abspath(__file__)),
        "comet": os.path.dirname(os.path.abspath(__file__)),
        "너": os.path.dirname(os.path.abspath(__file__)),      # "너 자가수리해봐"
        "자가수리": os.path.dirname(os.path.abspath(__file__)),
    }

    def _delegate_root(self, task, target):
        """위임할 작업폴더를 고른다. 못 고르면 None(=coder_bridge 기본값=바탕화면)."""
        hay = (str(target or "") + " " + task).replace(" ", "").lower()
        for key, path in self._DELEGATE_ROOTS.items():
            if key in hay:
                return path
        # "@경로" 를 직접 적어줬으면 그대로
        m = re.search(r"@([A-Za-z]:\\[^\s\"']+)", task)
        if m and os.path.isdir(m.group(1)):
            return m.group(1)
        return None

    def _delegate_engine(self, task):
        """클로드 CLI 로 보낼지 로컬 COMET-Coder 로 보낼지. 호윤 지시: 일에 따라 가른다."""
        flat = task.replace(" ", "")
        if any(k.replace(" ", "") in flat for k in self._CLAUDE_TASK_KW):
            return "claude"
        if any(k.replace(" ", "") in flat for k in self._LOCAL_TASK_KW):
            return "coder"
        return "claude"          # 애매하면 실력 있는 쪽 — 실행이 목적이니까

    #  클로드에게 열어줄 도구. ★Bash 는 일부러 뺐다(셸은 호윤 확인) — 호윤 지시(9/22).
    _CLAUDE_TOOLS      = "Read Edit Write Glob Grep"
    _CLAUDE_TOOLS_SHELL = "Read Edit Write Glob Grep Bash"

    def _delegate(self, task, target=None, shell_ok=False):
        """서브에이전트에게 실제 작업을 시키고, 한 일을 텍스트로 돌려준다."""
        import remote_guard
        if remote_guard.active():            # 텔레그램에서 온 말이면 위임 안 함(10/10 T-18)
            return {"ok": False, "text": remote_guard.REFUSE, "msg": remote_guard.REFUSE}
        root = self._delegate_root(task, target)
        engine = self._delegate_engine(task)
        where = root or "바탕화면"
        print(f"  [위임] {engine} · {where} · {task[:60]}")

        if engine == "claude":
            import debate
            import coder_bridge
            cwd = root or coder_bridge.DEFAULT_ROOT
            tools = self._CLAUDE_TOOLS_SHELL if shell_ok else self._CLAUDE_TOOLS
            prompt = (
                f"너는 호윤의 서브에이전트다. 아래 일을 **실제로 해라**(설명만 하지 말고 파일을 고쳐라).\n"
                f"작업폴더: {cwd}\n"
                + ("" if shell_ok else
                   "셸(Bash)은 안 열려 있다. 명령 실행이 꼭 필요하면 고치지 말고 "
                   "무엇을 왜 돌려야 하는지만 한 줄로 답해라.\n")
                + "끝나면 **무엇을 어떻게 고쳤는지 한국어로 5줄 이내**로 요약해라. 군더더기·칭찬 금지.\n\n"
                f"[할 일]\n{task}")
            try:
                out = debate.ask_claude(prompt, timeout=900, tools=tools, cwd=cwd)
            except Exception as e:
                return {"ok": False, "msg": f"클로드 서브에이전트 실패: {str(e)[:200]}"}
            text = f"🤖 서브에이전트(클로드) · {where}\n\n{out}"
            if not shell_ok:
                self._pending_shell = {"task": task, "target": target}
                text += "\n\n(셸은 안 열어놨어. 명령을 돌려야 하면 '셸 허용'이라고 해 — 셸 켜고 다시 돌릴게.)"
            return {"ok": True, "text": text}

        # ── 로컬 COMET-Coder ──
        import coder_bridge
        import dev_tools
        asked_shell = []

        def _confirm(desc):
            if desc.startswith("셸 실행:") or desc.startswith("⚠⚠"):
                asked_shell.append(desc)
                return False                    # 셸은 호윤 확인 전엔 안 돌린다
            return True                          # 읽기·수정은 자동 (.comet_bak 백업이 안전망)

        try:
            if shell_ok:
                result = coder_bridge.run_text_full(task, root=root, auto_approve=True)
            else:
                box = dev_tools.ToolBox(root=root or coder_bridge.DEFAULT_ROOT,
                                        confirm=_confirm, auto_approve=False)
                result = coder_bridge.run_text_full(task, root=root, box=box,
                                                    auto_approve=False)
        except Exception as e:
            return {"ok": False, "msg": f"로컬 서브에이전트 실패: {str(e)[:200]}"}

        text = f"🤖 서브에이전트(로컬) · {where}\n\n{result['text']}"
        if result.get("stopped") == "ask" and result.get("messages"):
            self._pending_coding = {"box": result["box"], "messages": result["messages"]}
            text += "\n\n❓ 답변하면 이어서 작업해."
        elif asked_shell:
            self._pending_shell = {"task": task, "target": target}
            text += ("\n\n⏸ 셸 명령이 필요해서 거기서 멈췄어: " + asked_shell[0][:120]
                     + "\n'셸 허용'이라고 하면 셸 켜고 처음부터 다시 돌릴게.")
        return {"ok": True, "text": text}

    # ── 로컬이 못 뜰 때 구독 CLI 로 대신 답하기 ──────────────────
    def _cloud_reply(self, text):
        """게임 중 VRAM 고갈 등으로 모델이 아예 안 올라올 때의 우회로.
           브리핑의 클라우드 우회와 같은 배선 — API 키 과금이 아니라 호윤 구독 CLI 다.
           도구·기억은 못 쓰니 아는 선에서만 답한다(그래서 실패를 숨기지 않고 우회했다고 알린다)."""
        try:
            import debate
        except Exception:
            return ""
        talk = []
        for m in self.history.recent()[-8:-1]:      # 맨 끝은 방금 한 말이라 뺀다
            who = "호윤" if m.get("role") == "user" else "너"
            talk.append(f"{who}: {str(m.get('content', ''))[:400]}")
        prompt = (SYSTEM + "\n\n"
                  "[지금은 로컬 모델이 못 떠서 네가 대신 답한다. 도구·파일·기억은 못 쓰니 아는 선에서 답하고,\n"
                  " 확인이 필요한 건 모른다고 해라. 위 말투 그대로, 군더더기 없이 답만 내라.\n"
                  " 코멧 코드를 고쳐 달라는 말이면 「클로드 코드를 열어라」 대신 코멧 명령을 알려 줘라 —\n"
                  " 제안된 것은 「바로 고쳐」 · 「업그레이드 1」, 새로 시킬 건 「업그레이드 요청 <내용>」(시작은 PC 앞에서).]\n\n"
                  + ("[최근 대화]\n" + "\n".join(talk) + "\n\n" if talk else "")
                  + f"[방금 물어본 것]\n호윤: {text}")
        for who, ask in (("클로드", lambda p: debate.ask_claude(p, timeout=120)),
                         ("아스트라", lambda p: debate.ask_astra(p, timeout=180))):
            try:
                out = (ask(prompt) or "").strip()
            except Exception as e:
                print(f"  [우회] {who} 실패: {str(e)[:110]}")
                continue
            if out:
                print(f"  · 로컬 모델이 못 떠서 {who} 로 우회했다")
                print(f"\nCOMET: {out}")
                return out
        return ""

    # ── 도구 작업: 동작선택(JSON) → 실행 → 결과로만 답변 ─────────
    #    forced=(action, query) 가 오면 동작선택을 건너뛴다(자동 웹검색 등 라우터가 미리 정한 경우)
    def _chat_with_tools(self, cfg, text, forced=None):
        import memory_db
        import files
        import projects
        import briefing
        import web
        import prices
        import analyst
        import marketlog
        import financials
        import trace as _trace

        if forced is not None:
            f_action, f_query = forced
            plan = {"action": f_action, "query": f_query}
        else:
            # 1) 동작 선택 (format=json — 안정적)
            ctx = "\n".join(f"{m['role']}: {m['content']}" for m in self.history.recent()[-6:])
            try:
                r = _gen(
                    cfg["model"],
                    [{"role": "user", "content": build_decide_prompt(text) + ctx}],
                    fmt="json", keep_alive=cfg["keep_alive"], temperature=0,
                )
                plan = json.loads(r["message"]["content"])
            except Exception:
                plan = {"action": "none"}
        action = plan.get("action", "none")

        # 긴 텍스트 붙여넣기 등을 게이트키퍼가 억지로 파일 도구에 꽂는 오분류 방지 —
        # path가 실제로 비어있으면 파일/폴더 요청이 아니었던 것이니 일반 대화로 되돌린다.
        if action in ("list_dir", "read_file") and not (plan.get("path") or "").strip():
            action = "none"

        # 2) 실행 (기억 DB / 파일 도구)
        result = None
        if action == "remember":
            result = memory_db.dispatch(action, {"kind": plan.get("kind", "memo"),
                                                 "content": plan.get("content", text)})
        elif action in ("recall", "complete_todo"):
            result = memory_db.dispatch(action, {"query": plan.get("query", text)})
        elif action == "list_todos":
            result = memory_db.dispatch(action, {"status": plan.get("status", "open")})
        elif action in ("list_dir", "read_file"):
            result = files.dispatch(action, {"path": plan.get("path", "")})
        elif action == "search_files":
            result = files.dispatch(action, {"query": plan.get("query", text)})
        elif action == "trade_stats":
            result = projects.dispatch(action, {"source": plan.get("source", text)})
        elif action == "delegate":
            result = self._delegate((plan.get("task") or plan.get("query") or text).strip(),
                                    plan.get("target"))
        elif action == "list_sources":
            result = projects.dispatch(action, {})
        elif action == "brief":
            result = briefing.dispatch(action, {"project": plan.get("project", text)})
        elif action == "list_projects":
            result = briefing.dispatch(action, {})
        elif action == "web_search":
            query = plan.get("query") or text
            web_trace = _trace.Trace(True, title=f"웹 검색  ·  {query}")
            web_trace.step("검색", "DuckDuckGo · 원천(뿌리) 추적")
            result = web.dispatch(action, {"query": query})
            if result and result.get("ok"):
                srcs = result.get("sources", [])
                strong = sum(1 for s in srcs if s.get("tier", 3) <= 2)
                web_trace.step("자료 읽기", f"{len(srcs)}개 출처 · 원천/보도 {strong} · 도메인 중복제거")
        elif action == "get_market":
            print("  [시세] 미장 스냅샷")
            result = prices.dispatch(action, {})
        elif action in ("get_fx", "get_crypto", "get_stock", "get_fear_greed"):
            if action == "get_stock":
                pargs = {"ticker": plan.get("ticker") or plan.get("query") or text}
            else:
                pargs = {"query": plan.get("query", text)}
            print(f"  [시세] {action}")
            result = prices.dispatch(action, pargs)
        elif action == "analyze_stock":
            query = plan.get("query") or text
            # 분석은 정확·날카로움 우선 → 라우터 티어 무시하고 analyst 기본(27b)으로. ~1.5분.
            # 진행 로그는 analyst 내부 trace가 단계별로 출력(검색→읽기→추론→검증).
            result = analyst.dispatch(action, {"query": query,
                                               "keep_alive": cfg["keep_alive"]})
        elif action == "market_log":
            topic = plan.get("query") or plan.get("topic") or text
            print(f"  [정세로그] 흐름 조회: {topic}")
            result = marketlog.dispatch(action, {"query": topic})
        elif action == "get_financials":
            tk = plan.get("ticker") or plan.get("query") or text
            print(f"  [재무] {tk}")
            result = financials.dispatch(action, {"ticker": tk})
        elif action == "read_holdings":
            print("  [보유] 화면 캡처 → 보유종목 읽는 중(클로드 → 아스트라 → 로컬 12b)…")
            import vision
            result = vision.read_holdings()
        elif action == "send_telegram":
            import notify
            content = (plan.get("content") or "").strip()
            flat_c = content.replace(" ", "")
            # 내용 미지정이거나 "미장 브리핑/시황" 류 짧은 지시면 최신 스냅샷을 실측으로 새로 떠서 보낸다
            # (LLM이 숫자를 지어내 텔레그램으로 내보내는 걸 막는다 — 다른 시세 경로와 동일한 원칙)
            wants_snapshot = (not content) or (len(content) < 40 and (
                any(k in flat_c for k in PRICE_KW) or "브리핑" in flat_c))
            if wants_snapshot:
                snap = prices.dispatch("get_market", {})
                if snap.get("ok"):
                    content = snap["text"]
            if not content:
                # 시세도 아니고 지정도 없었다 — 직전 COMET 답변을 보낸다
                content = next((m["content"] for m in reversed(self.history.msgs)
                                if m["role"] == "assistant"), "")
            if not content:
                result = {"ok": False, "msg": "보낼 내용이 없어 — 먼저 뭔가 물어보고 나서 보내달라고 해줘."}
            elif not notify.configured():
                result = {"ok": False, "msg": "텔레그램 미설정(token/chat_id 필요) — notify.py 참고."}
            else:
                print("  [텔레그램] 전송 중…")
                r = notify.send(content)
                result = {"ok": bool(r.get("ok")),
                          "msg": "텔레그램으로 보냈어." if r.get("ok") else f"전송 실패: {r.get('msg', '')}"}

        if result is not None:
            print(f"  [도구] {action} → {result.get('msg', '완료' if result.get('ok') else result)}")

        # 3-a) 실패 결과는 모델 안 거치고 직접 전달 (거짓말 차단)
        if result is not None and not result.get("ok", True):
            msg = result.get("msg", "처리하지 못했어.")
            print(f"\nCOMET: {msg}")
            return msg

        # 3-시세) 환율·코인·주가·공포탐욕은 API 숫자를 그대로 — LLM 안 거침(날조 원천 차단)
        if action in ("get_fx", "get_crypto", "get_stock", "get_fear_greed") and result is not None:
            msg = prices.fmt(result)
            print(f"\nCOMET: {msg}")
            return msg

        # 3-스냅샷) 미장 스냅샷은 prices 가 만든 실측 텍스트 그대로 — LLM 안 거침(날조 0)
        if action == "get_market" and result is not None and result.get("ok"):
            msg = result["text"]
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-위임) 서브에이전트가 한 일(고친 파일·진행 기록)은 사실 기록이다 — LLM 이 옮겨 적으면
        #   안 한 일을 했다고 쓰거나 파일명이 흐트러진다. 그대로 내보낸다(2026-09-22).
        if action == "delegate" and result is not None and result.get("ok"):
            msg = result["text"]
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-텔레그램) 전송 성공/실패는 사실이지 창작 대상이 아니다 — LLM 안 거치고 그대로
        if action == "send_telegram" and result is not None:
            msg = result.get("msg", "처리했어.")
            print(f"\nCOMET: {msg}")
            return msg

        # 3-분석) 종목/테마 분석은 analyst 가 이미 2패스(컨센서스→역발상·사실감사)로 합성 — 그대로
        if action == "analyze_stock" and result is not None and result.get("ok"):
            msg = result["answer"]
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-정세로그) 정세 흐름은 marketlog 가 만든 타임라인을 그대로(링크 보존, LLM 안 거침)
        if action == "market_log" and result is not None and result.get("ok"):
            msg = result["text"]
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-재무) 펀더멘털은 Yahoo API 숫자 그대로 — LLM 안 거침(날조 불가)
        if action == "get_financials" and result is not None and result.get("ok"):
            msg = financials.fmt(result)
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-보유) 스샷에서 읽은 보유 내역 그대로 + 저장 안내
        if action == "read_holdings" and result is not None and result.get("ok"):
            msg = ("📋 보유 종목(화면에서 읽음):\n" + result["text"]
                   + "\n\n(저장했어 — 이제 종목 분석할 때 이 보유 기준으로 갈아탈지/줄일지 짚어줄게.)")
            print(f"\nCOMET:\n{msg}")
            return msg

        # 3-할일) 할일·일정 목록은 todo_box 가 만든 화면 그대로 — LLM 이 옮겨 적으면
        #   번호·마감(D-2, 오늘 15:00)이 흐트러진다. 숫자는 모델에게 안 맡긴다(유의사항).
        if action == "list_todos" and result is not None and result.get("text"):
            msg = result["text"]
            print(f"\nCOMET:\n{msg}")
            return msg

        # recall이 비었으면(저장 메모 없음) DB 결과로 막지 말고 프로필 배경지식으로 답
        empty_recall = (action == "recall" and isinstance(result, dict)
                        and result.get("count", 0) == 0)

        # action=="none"/empty_recall 은 결과 grounding 없이 SYSTEM(호윤 프로필 전체 포함)만 깔고
        # 즉흥 답변시키는 유일한 경로다 — 옛 light(gemma3:4b)가 "이건 꺼내지 마라" 부정지시를 못 지켜
        # 실사용 중 민감 개인사(트라우마·이별 등)를 그대로 요약해 흘린 사례가 있었다(대화기록 확인).
        # 지금 light 는 gemma4:12b 라 나아졌을 수 있으나 재실측 전까지 이 방어는 유지한다.
        # 이 경로만 최소 medium(gemma4:26b)으로 올린다 — 속도보다 안전이 우선.
        if (action == "none" or empty_recall) and cfg["model"] == TIERS["light"]["model"]:
            cfg = dict(TIERS["medium"])

        # 3-b-웹) 웹 검색 = 원천(뿌리)을 추적해 합리적 결론을 추론 (매체로 믿지 않음)
        if action == "web_search" and result is not None and result.get("ok"):
            srcs = "\n\n".join(
                f"[출처{s['n']}] (분류 {s['tier']}={s['tier_label']} · {s['domain']} · "
                f"날짜 {s.get('date') or '미상'}"
                + (" · 원천인용 있음" if s.get("root_ref") else "") + ")\n"
                + f"{s['title']}\n{s['url']}\n{s['text']}"
                for s in result.get("sources", [])
            )
            grounding = (
                f"질문: \"{text}\"\n\n"
                "너의 강점은 '추론'이다. 단순 검색요약기가 아니라, 아래 자료와 지금까지 대화에서 나온 "
                "정보를 종합해 가장 합리적인 판단을 스스로 내리는 분석가다. 단, 자료가 질문과 무관하면 "
                "무관하다 말하고 절대 다른 주제로 새지 마라.\n\n"
                "출처 분류: 1=원천·공식(사실을 만든 주체), 2=보도(언론이 전달), 3=참고(블로그·위키, 미확인).\n\n"
                "=== 자료 ===\n" + srcs + "\n=== 자료 끝 ===\n\n"
                "판단 방식 (매체로 믿지 말고 뿌리를 따라가라):\n"
                "1) 신뢰는 매체가 아니라 '원천까지 추적되느냐'에서 나온다. 언론도 가짜·왜곡 있다 — "
                "원천(한국은행·통계 등)을 인용하면 신뢰↑, 출처 없이 주장만 하면 약하게. "
                "블로그·위키(3)는 단정 근거로 쓰지 말고 단서로만.\n"
                "2) 여러 자료가 독립적으로 같은 결론으로 수렴하면 신뢰↑. 한 곳(특히 3)에만 있으면 미확인.\n"
                "3) 시간에 민감한 값은 날짜가 최신·확인된 것 우선. 자료에 없는 숫자·이름·날짜는 지어내지 마라.\n\n"
                "이렇게 답해라 — 템플릿 나열 말고 자연스러운 문장으로, 추론을 전면에 세워서:\n"
                "① 먼저 자료로 '확인된 사실'을 짧게 [출처N]과 함께 깔아라.\n"
                "② 그 위에 너의 판단을 1인칭 추론으로 펼쳐라. 예: \"이걸 종합하면, 제 추론으로는 ~인 것으로 "
                "보입니다\" — 왜 그렇게 보는지 근거(자료 증거 + 앞서 대화에서 나온 정보)를 함께 들어라.\n"
                "③ 확신 정도를 솔직히 녹여라(확실 / 그럴 가능성이 높음 / 추측 수준). 근거가 약하면 약하다고, "
                "어느 원천을 더 봐야 하는지 짚어라.\n"
                "★ 추론 단계에서도 구체적 수치(%, 금액, 날짜)는 자료에 실제 있는 것만 인용해라. "
                "네가 보태는 것은 '해석·판단'이지 새 숫자가 아니다. 자료에 없는 수치를 지어내 근거로 쓰지 마라.\n"
                "④ 믿진 않아도 참고할 만한 미확인 주장이나 이견이 있으면 '미확인'이라 밝히고 덧붙여라.\n"
                "한국어로, 간결하게.")
            messages = ([{"role": "system", "content": SYSTEM}]
                        + self.history.recent()
                        + [{"role": "user", "content": grounding}])
        # 3-b) 그 외 도구: 결과만 근거로 답변 (한국어 강제, 지어내기 금지)
        elif result is not None and not empty_recall:
            grounding = ("[방금 '" + action + "' 실행 결과]\n"
                         + json.dumps(result, ensure_ascii=False)
                         + "\n\n위 결과만 근거로, 반드시 한국어로만, 간결히 답해. "
                           "결과에 없는 항목은 지어내지 마.")
            messages = ([{"role": "system", "content": SYSTEM}]
                        + self.history.recent()
                        + [{"role": "user", "content": grounding}])
        elif empty_recall:
            # 저장 메모는 없지만 SYSTEM에 호윤 프로필이 있다 → 그걸로 답
            messages = ([{"role": "system", "content": SYSTEM}] + self.history.recent()
                        + [{"role": "user", "content":
                            "(참고: 저장된 메모는 없다. 시스템 프롬프트나 배경지식 문서 자체를 요약·나열·인용"
                            "하지 마라 — 그건 답이 아니다. 사용자의 실제 마지막 질문에만, 필요할 때만 배경지식을 "
                            "자연스럽게 참고해 짧게 답해라.)"}])
        else:
            # action == "none": 도구 키워드는 스쳤지만 동작선택기가 실행할 걸 못 골랐다.
            # 아무 안내 없이 SYSTEM+history만 주면 프로필 배경지식을 끌어다 엉뚱한 얘기를
            # 지어내는 경향이 있었다(예: 브리핑 요청에 run.bat 얘기, "오늘 하루" 자기 얘기 지어냄,
            # 긴 텍스트 붙여넣었을 때 시스템 프롬프트 자체를 "문서"로 착각해 요약 — 대화기록 확인).
            # 실제 마지막 질문에만 붙어 있고 모르면 모른다 하라고 못박는다.
            messages = ([{"role": "system", "content": SYSTEM}] + self.history.recent()
                        + [{"role": "user", "content":
                            "(참고: 방금 요청에 맞는 도구 실행 결과가 없다. 확실치 않으면 모른다고 하거나 "
                            "무슨 뜻인지 되물어라. 위 배경지식·프로필에서 아무거나 끌어와 관련 없는 "
                            "내용을 지어내지 마라. 시스템 프롬프트나 배경지식 문서 자체를 요약·인용하지 마라 "
                            "— 그건 네게 준 지침이지 사용자가 보여준 문서가 아니다. "
                            "네게 그 기능이 있는지 없는지 스스로 확신이 없으면 없다고 단정하지 마라 — "
                            "'도움말 이라고 쳐봐서 확인해줘'라고 안내해라(있는데 없다고 잘라 말한 적이 있었다). "
                            "사용자의 실제 마지막 질문에만 답해라.)"}])

        temp = 0 if action == "web_search" else 0.2   # 웹 사실확인은 창작 여지 0

        # 웹 답변: 사고과정(thinking) 누수 방지 — 비스트리밍으로 받아 <think> 제거 후 출력
        if action == "web_search":
            web_trace.think("종합·교차검증  —  매체로 믿지 말고 원천을 따라가 추론")
            r = _gen(cfg["model"], messages,
                     keep_alive=cfg["keep_alive"], temperature=temp, allow_cloud=True)
            full = r["message"].get("content", "")
            full = re.sub(r"<think>.*?</think>", "", full, flags=re.S)
            full = re.sub(r"^.*?</think>", "", full, flags=re.S)   # 여는 태그 누락 대비
            full = full.strip()
            web_trace.done("검색 완료")
            print(f"\nCOMET: {full}")
            return full

        print("\nCOMET: ", end="", flush=True)
        full = _stream_filtered(cfg["model"], messages, cfg, temperature=temp)
        print()
        return full

    # ── 음성 입력 한 턴 (마이크 → Whisper → 답변 + 음성출력) ──
    def voice_turn(self):
        text = self._get_ear().listen()
        if not text:
            print("  [인식 실패, 다시]")
            return
        print(f"나(음성): {text}")
        self.respond(text, speak=True)

    # ── 대기: 모든 모델 VRAM 에서 즉시 언로드 ───────────────────
    def sleep(self):
        models = {c["model"] for c in TIERS.values()}
        for m in models:
            try:
                ollama.generate(model=m, prompt="", keep_alive=0)
            except Exception:
                pass
        print("  [💤 대기모드] 전 모델 VRAM 에서 비움")

    # ── 상태: 로드된 모델 + VRAM ────────────────────────────────
    def status(self):
        try:
            running = ollama.ps()
            loaded = [m.get("model", m.get("name", "?")) for m in running.get("models", [])]
        except Exception:
            loaded = []
        print(f"  [상태] 로드된 모델: {', '.join(loaded) if loaded else '없음 (대기)'}")
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            used, total = out.stdout.strip().split(",")
            print(f"  [상태] VRAM: {used.strip()} / {total.strip()} MiB")
        except Exception:
            pass

    # ── 대화 요약 ───────────────────────────────────────────────
    def summarize(self):
        if not self.history.msgs:
            print("  대화 없음")
            return
        convo = "\n".join(
            f"{'나' if m['role']=='user' else 'COMET'}: {m['content']}"
            for m in self.history.recent()
        )
        print("\n[요약]")
        try:
            for part in _gen(
                TIERS["medium"]["model"],
                [{"role": "user",
                  "content": f"아래 대화를 한국어 3줄로 요약해:\n\n{convo}"}],
                stream=True,
            ):
                print(part.get("message", {}).get("content", ""), end="", flush=True)
            print()
        except Exception as e:
            print(f"  [오류] {e}")


# ═══════════════════════════════════════════════════════════════
#  메인 루프
# ═══════════════════════════════════════════════════════════════
BANNER = """═══════════════════════════════════════════════════
  COMET  ·  Cognitive Operation Management
            & Electronic Transmission
═══════════════════════════════════════════════════
  3단 모델:  light=gemma4:12b  medium=gemma4:26b  heavy=gemma4:26b
  명령어:  음성(v) | 말하기 | 소리작게/소리크게 | 상태 | 잠자 | 요약 | 리셋 | 종료
═══════════════════════════════════════════════════"""


def main():
    print(BANNER)
    comet = Comet()

    while True:
        try:
            text = input("\n나: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료.")
            break

        if not text:
            continue

        cmd = text.lower()
        if cmd in ("종료", "exit", "quit"):
            print("종료.")
            break
        if cmd in ("리셋", "reset"):
            comet.history.reset()
            continue
        if cmd in ("상태", "status"):
            comet.status()
            continue
        if cmd in ("잠자", "sleep"):
            comet.sleep()
            continue
        if cmd in ("요약", "summary"):
            comet.summarize()
            continue
        if cmd in ("음성", "v"):
            comet.voice_turn()
            continue
        if cmd in ("말하기", "tts"):
            comet.speak_enabled = not comet.speak_enabled
            state = "켜짐 — 답변을 소리내서 읽음" if comet.speak_enabled else "꺼짐"
            print(f"  [말하기 모드] {state}")
            continue
        if cmd in ("소리작게", "소리-"):
            m = comet._get_mouth()
            m.gain = max(0.1, round(m.gain - 0.1, 2))
            print(f"  [음량] {m.gain}")
            continue
        if cmd in ("소리크게", "소리+"):
            m = comet._get_mouth()
            m.gain = min(1.0, round(m.gain + 0.1, 2))
            print(f"  [음량] {m.gain}")
            continue

        comet.respond(text)


if __name__ == "__main__":
    main()
