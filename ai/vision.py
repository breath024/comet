# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 화면 인식 — PC 화면 캡처 → gemma4 멀티모달로 본다.
#  데몬이 사는 PC의 화면을 캡처(폰에서 물어도 PC 화면을 봐줌).
# ═══════════════════════════════════════════════════════════════
import os
import tempfile
import ollama
from PIL import ImageGrab

VISION_MODEL = "gemma4:12b"     # 멀티모달. 더 정밀히 보려면 gemma4:26b
HOLDINGS_MODEL = "gemma4:26b"  # 표·숫자 읽기는 정밀해야 하니 한 단계 위. 26b 도 vision 된다
MAX_WIDTH = 1280               # 캡처 축소 — 속도/토큰 절약

# ── 안정성: 얼어붙은 ollama 대비 타임아웃 + 출력 상한(설명/표 폭주 차단). 27b 홀딩스라 300s.
_VC = ollama.Client(timeout=300)

_DIR = os.path.dirname(os.path.abspath(__file__))
HOLDINGS_FILE = os.path.join(_DIR, "holdings.txt")

HOLDINGS_PROMPT = (
    "이 화면은 증권사 앱/MTS의 보유 종목(포트폴리오) 화면이다. 표에서 보유 종목을 정확히 읽어 "
    "각 줄을 '종목명(또는 티커) · 수량 · 평단가 · 현재가 · 평가손익(금액/%)' 형식으로 추출해라.\n"
    "규칙: 숫자는 화면에 보이는 그대로만(추측·반올림 금지, 안 보이는 칸은 '-'). "
    "종목이 아닌 UI 버튼·광고·메뉴는 무시. 합계/총평가금액이 보이면 맨 끝에 한 줄 덧붙여라. "
    "보유 표가 안 보이면 '보유 화면이 아님'이라고만 답해라. 한국어, 목록만."
)


def capture_screen() -> str:
    img = ImageGrab.grab()                       # 주 모니터 캡처
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, "PNG")
    return path


def look(question: str = "", persona: str = "") -> str:
    path = capture_screen()
    try:
        msgs = []
        if persona:
            msgs.append({"role": "system", "content": persona})
        msgs.append({
            "role": "user",
            "content": question.strip() or "지금 화면에 뭐가 보이는지 한국어로 설명해줘.",
            "images": [path],
        })
        r = _VC.chat(model=VISION_MODEL, messages=msgs, keep_alive="5m",
                     options={"num_predict": 1024})
        return (r.get("message", {}).get("content", "") or "").strip()
    finally:
        if os.path.exists(path):
            os.remove(path)


def read_holdings(model=None) -> dict:
    """화면(증권사 보유 종목)을 캡처해 보유 내역을 표로 추출 → holdings.txt 저장.
       analyst가 '내 보유 기준' 분석에 쓴다. 정밀 위해 기본 27b."""
    path = capture_screen()
    try:
        r = _VC.chat(
            model=model or HOLDINGS_MODEL, keep_alive="2m",
            options={"num_predict": 1536},
            messages=[{"role": "user", "content": HOLDINGS_PROMPT, "images": [path]}])
        text = (r.get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        return {"ok": False, "msg": f"보유 화면 읽기 실패: {e}"}
    finally:
        if os.path.exists(path):
            os.remove(path)
    if "보유 화면이 아님" in text or len(text) < 8:
        return {"ok": False, "msg": "보유 종목 화면이 안 보여. 증권사 보유 화면을 띄우고 다시 시도해줘."}
    try:
        with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return {"ok": True, "text": text, "saved": HOLDINGS_FILE}


def load_holdings():
    """저장된 보유 내역 텍스트(없으면 None). analyst가 보유 컨텍스트로 읽음."""
    try:
        with open(HOLDINGS_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            return t or None
    except Exception:
        return None
