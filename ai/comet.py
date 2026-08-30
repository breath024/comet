# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET — Cognitive Operation Management & Electronic Transmission
#  A.B.B.Y.S 프로젝트의 상주 관리자 AI (v0 · 텍스트 콘솔)
#
#  구조:
#   - 문지기 라우터: gemma4:12b 가 매 입력의 난이도를 판정
#   - 3단 모델:  light=gemma4:12b / medium=gemma4:26b / heavy=gemma4:31b
#   - 호흡(대기): 등급별 keep_alive 로 무거운 모델은 곧 VRAM 에서 증발
#  의존성: ollama (파이썬) 하나. 음성/STT 는 다음 층에서 붙임.
# ═══════════════════════════════════════════════════════════════
import os
import sys
import re
import json
import subprocess
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
TIERS = {
    "light":  {"model": "gemma4:12b",  "keep_alive": "10m", "desc": "잡담·즉답"},
    "medium": {"model": "gemma4:26b",  "keep_alive": "5m",  "desc": "기본 작업"},
    "heavy":  {"model": "gemma4:31b",  "keep_alive": "30s", "desc": "신중한 큰 작업"},
}

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comet_history.json")
MAX_HISTORY  = 40                 # 모델에 넘기는 최근 대화 메시지 수 상한

# ── 안정성: 모델 호출 상한 (데몬이 한 턴에 얼어붙는 것 방지) ──────────
#  · num_predict 상한 = 한 생성이 폭주(만 토큰)해도 잘려서 _lock 을 오래 안 쥠.
#    비서 답은 1~3문장이라 1024 는 넉넉(정상 답은 안 잘리고 폭주만 막음).
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
#    신중한 큰 작업)가 소리 없이 무너진다 — light 자리에 31b 가 들어앉아도
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


def _gen(model, messages, *, stream=False, keep_alive=None, fmt=None,
         temperature=None, num_predict=MAX_REPLY_TOKENS, allow_cloud=False):
    """ollama.chat 단일 통로 — 출력 상한 + 타임아웃을 항상 건다.
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
    opts = {}
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
    return _client.chat(**kw)


# ── 게임/바쁨 모드: GPU가 게임 등으로 바쁘면 무거운 모델 대신 light 로 ────────
#  16GB VRAM 한 장 → 게임이 GPU 점유 중이면 26b/31b 올리다 게임이 끊긴다.
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

[회의 (멀티에이전트)]
  회의 <주제> — 역할별 에이전트 토론→합성 (몇 분 걸림)

[화면·이미지]
  화면 봐줘 / 이 이미지 뭐야 — gemma4 vision (vision.py)

[두뇌 전환]
  두뇌 — 현재 상태
  클로드로 / gpt로 / 딥시크로 / 로컬로 — 두뇌 전환
  키 클로드 sk-ant-... / 키 gpt sk-... — API 키 설정

[모드]
  게임모드 / 게임모드켜 / 게임모드꺼 / 게임모드자동 — VRAM 양보 모드
  자율모드 / 자율모드켜 / 자율모드꺼 — 먼저 말 걸기 모드

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
    chunks = []
    for part in _gen(model, messages, stream=True, keep_alive=cfg["keep_alive"],
                     temperature=temperature, allow_cloud=True):
        tok = part.get("message", {}).get("content", "")
        if tok:
            chunks.append(tok)
    full = "".join(chunks)
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
            except Exception:
                self.msgs = []

    def add(self, role, content):
        self.msgs.append({"role": role, "content": content})

    def recent(self):
        return self.msgs[-MAX_HISTORY:]

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.msgs, f, ensure_ascii=False, indent=2)

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
        # 자율/상주 모드(먼저말걸기·부르면듣기) — 로직은 autonomy.py 에 격리. 저장된 모드 복원.
        import autonomy
        self.autonomy = autonomy.Autonomy(
            respond_fn=self.respond,
            speak_fn=lambda t: self._get_mouth().speak(t),
            log=print,
            gpu_busy_fn=_gpu_busy)   # brief 모드가 게임 중이면 무거운 브리핑을 미루도록
        self.autonomy.restore()

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

    def respond(self, text, speak=None):
        # ── 도움말 ──────────────────────────────────────────────────────────────
        _low0 = text.strip().replace(" ", "").lower()
        if _low0 in ("도움말", "help", "기능", "명령어", "뭐할수있어", "뭘할수있어",
                     "사용법", "기능뭐있어", "기능이뭐야", "명령어뭐있어"):
            return HELP_TEXT

        # ── 코딩 명령(콘솔·폰 공용): "코딩 [@폴더] [/code|/debug|…] <할 일>" ──
        #    코딩 에이전트(coder_bridge)로 넘긴다. 비대화형 auto+백업(.comet_bak).
        t = text.strip()
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
        if t.startswith("회의 ") or t == "회의":
            topic = t[2:].strip()
            if not topic:
                return "회의 <주제> — 여러 에이전트가 분석→초안→반론→합성으로 따져줘(몇 분 걸려). 코딩은 '코딩 회의 …'."
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
        # ── 자율/상주 모드 토글 (콘솔·폰 공용) ──
        if low.startswith("자율모드") or low.startswith("자율 모드"):
            return self.autonomy.command(low)
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

        # ── 코딩 세션 이어받기: ask 로 멈춘 작업이 있으면 다음 입력으로 resume ──
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
                    full = self._chat_with_tools(cfg, text, forced=("web_search", auto_web))
                elif use_tools:
                    full = self._chat_with_tools(cfg, text)
                else:
                    full = self._chat_stream(cfg)
        except Exception as e:
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
            print("  [보유] 화면 캡처 → 보유종목 읽는 중(27b)…")
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
  3단 모델:  light=gemma4:12b  medium=gemma4:26b  heavy=gemma4:31b
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
