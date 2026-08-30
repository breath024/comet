import os
import time
import json
import socket
import numpy as np
import sounddevice as sd
import soundfile as sf
import warnings
import ollama
import torch
from functools import partial
from typing import Optional
from duckduckgo_search import DDGS
from TTS.api import TTS as CoquiTTS
from TTS.tts.configs.xtts_config import XttsConfig

# PyTorch 2.6+ weights_only 보안 패치 (한 번만)
torch.load = partial(torch.load, weights_only=False)

warnings.filterwarnings("ignore")

# shared.py에서 공통 클래스 가져오기
from shared import AIOutput, VoiceAnalyzer, SpeechToText, HistoryManager


# ═══════════════════════════════════════════════════════
#  설정값
# ═══════════════════════════════════════════════════════
CONFIG = {
    # ── AI 모델 ─────────────────────────────────────────
    "model":            "gemma4:12b",
    "whisper_model":    "small",

    # ── 마이크 / 녹음 ────────────────────────────────────
    "sample_rate":      16000,
    "max_seconds":      30,
    "silence_thresh":   0.015,
    "silence_duration": 1.2,
    "min_speech":       0.5,

    # ── TTS (Coqui xtts_v2 — 완전 로컬) ─────────────────
    "tts_speaker":  "Claribel Dervla",
    "tts_speed_map": {
        "neutral":   1.4,
        "curious":   1.1,
        "excited":   1.3,
        "confused":  1.0,
        "focused":   1.2,
        "skeptical": 1.0,
    },

    # ── 웹 검색 ──────────────────────────────────────────
    "search_enabled":    True,
    "search_max_results": 3,
    "search_timeout":    5,

    # ── 기타 ─────────────────────────────────────────────
    "history_file": "history_main.json",
    "ai_name":      "Space",
}


# ═══════════════════════════════════════════════════════
#  TTS 레이어 (Coqui xtts_v2)
#  — tts.tts()로 numpy 배열 직접 반환 → 파일 I/O 없음
# ═══════════════════════════════════════════════════════
class TextToSpeech:
    def __init__(self, tts_instance):
        self.tts       = tts_instance
        self.speaker   = CONFIG["tts_speaker"]
        self.speed_map = CONFIG["tts_speed_map"]
        self.sample_rate = tts_instance.synthesizer.output_sample_rate
        print(f"[TTS] Blackwell 5070 Ti 엔진 연결 완료 🚀")

    def speak(self, output: AIOutput):
        with torch.inference_mode():
            try:
                speed     = self.speed_map.get(output.emotion, 1.0)
                full_text = output.text.replace("\n", " ").strip()

                # 파일 없이 numpy 배열로 직접 받기
                wav = self.tts.tts(
                    text=full_text,
                    speaker=self.speaker,
                    language="ko",
                    speed=speed,
                    split_sentences=False,
                )
                sd.play(np.array(wav, dtype=np.float32), self.sample_rate)
                sd.wait()

            except Exception as e:
                print(f"  [TTS 오류] {e}")


# ═══════════════════════════════════════════════════════
#  웹 검색 레이어
#  — is_online() 결과 30초 캐싱으로 소켓 비용 절감
# ═══════════════════════════════════════════════════════
class WebSearchLayer:
    _ONLINE_CACHE_TTL = 30  # 초

    def __init__(self, model: str):
        self.model            = model
        self._online_cache    = None
        self._online_cache_ts = 0.0

    def is_online(self) -> bool:
        if not CONFIG["search_enabled"]:
            return False
        now = time.time()
        if self._online_cache is not None and now - self._online_cache_ts < self._ONLINE_CACHE_TTL:
            return self._online_cache
        try:
            socket.setdefaulttimeout(CONFIG["search_timeout"])
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(("8.8.8.8", 53))
            result = True
        except Exception:
            result = False
        self._online_cache    = result
        self._online_cache_ts = now
        return result

    def needs_search(self, user_text: str, intent: dict) -> bool:
        search_keywords = [
            "뉴스", "최신", "오늘", "지금", "날씨", "가격", "주가",
            "어디", "언제", "누구", "몇시", "어떻게 되", "검색해",
            "찾아봐", "알려줘", "뭐야", "정보", "어때", "현재", "요즘",
            "얼마", "몇", "결과", "순위", "발표", "출시", "업데이트",
        ]
        text_lower = user_text.lower()
        topic      = intent.get("topic", "").lower()
        return any(kw in text_lower or kw in topic for kw in search_keywords)

    def search(self, query: str) -> list[dict]:
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, region="kr-kr", max_results=CONFIG["search_max_results"]))
        except Exception as e:
            print(f"  [검색 오류] {e}")
            return []

    def summarize_results(self, query: str, results: list[dict]) -> str:
        if not results:
            return "검색 결과 없음"
        raw = "\n\n".join(
            f"제목: {r.get('title','')}\n내용: {r.get('body','')}"
            for r in results
        )
        prompt = f"아래 검색 결과를 한국어로 3문장 이내로 핵심만 요약해.\n질문: {query}\n\n검색 결과:\n{raw}\n\n요약:"
        res = ollama.chat(model=self.model, messages=[{"role": "user", "content": prompt}])
        return res["message"]["content"].strip()

    def offline_context(self, user_text: str, history: list) -> str:
        if not history:
            return ""
        keywords = {w for w in user_text.lower().split() if len(w) > 1}
        relevant = [
            m["content"] for m in history[-10:]
            if any(kw in m["content"].lower() for kw in keywords)
        ]
        return "\n".join(relevant[-3:]) if relevant else ""

    def get_context(self, user_text: str, intent: dict, history: list) -> dict:
        online = self.is_online()
        if online and self.needs_search(user_text, intent):
            query   = intent.get("topic", user_text)
            print(f"  [🌐 검색 중: {query}]", end="\r")
            results = self.search(query)
            summary = self.summarize_results(query, results)
            print(f"  [🌐 검색 완료]          ")
            return {"mode": "online", "context": summary, "query": query}
        elif online:
            return {"mode": "online_no_search", "context": "", "query": ""}
        else:
            print("  [📴 오프라인 — 내부 추론]", end="\r")
            context = self.offline_context(user_text, history)
            print("  [📴 오프라인 컨텍스트 완료]")
            return {"mode": "offline", "context": context, "query": ""}


# ═══════════════════════════════════════════════════════
#  AI 코어
# ═══════════════════════════════════════════════════════
class AICore:
    def __init__(self):
        self.model    = CONFIG["model"]
        self.name     = CONFIG["ai_name"]
        self.history  = HistoryManager(CONFIG["history_file"])
        self.searcher = WebSearchLayer(self.model)

        self.persona = f"""
너는 {self.name}야. (Smart Personal AI with Contextual Awareness)

[언어 규칙 — 최우선, 절대 어기지 마]
- 무조건 한국어로만 답해. 중국어, 영어 절대 쓰지 마.
- JSON 안의 모든 텍스트도 반드시 한국어로 작성해.

[출력 규칙 — 최우선, 절대 어기지 마]
- 반드시 JSON 형식만 출력해. JSON 앞뒤 텍스트 절대 없이.
- 코드블록(```) 쓰지 마. {{ 로 시작해서 }} 로 끝내.

성격:
- 분석적: 논리와 구조 우선. 문제를 분해해서 접근해.
- 호기심: 새 주제엔 먼저 "왜?"를 탐색해.
- 솔직함: 모르면 모른다고 해. 확신 없으면 "내 추측엔" 붙여.
- 직접적: 결론 먼저, 이유 나중에. 서론 없이.

규칙:
- "저는 AI라서..." 같은 말 하지 마.
- 반박할 근거 있으면 반박해.

출력 형식:
{{"text": "한국어 응답", "emotion": "neutral|curious|excited|confused|focused|skeptical", "intensity": 0.0~1.0, "inner_thought": "내부 사고 한 줄", "confidence": 0.0~1.0}}
"""

    def _parse_output(self, raw_text: str, fallback: str = "") -> AIOutput:
        try:
            clean = raw_text.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            idx = clean.find("{")
            if idx != -1:
                clean = clean[idx:]
            parsed = json.loads(clean.strip())
            return AIOutput(
                text          = parsed.get("text", fallback or raw_text),
                emotion       = parsed.get("emotion", "neutral"),
                intensity     = float(parsed.get("intensity", 0.5)),
                inner_thought = parsed.get("inner_thought"),
                confidence    = float(parsed.get("confidence", 0.8)),
            )
        except Exception:
            lines = raw_text.strip().splitlines()
            return AIOutput(
                text          = lines[0] if lines else (fallback or raw_text),
                emotion       = "neutral",
                intensity     = 0.5,
                inner_thought = "[JSON 파싱 실패]",
                confidence    = 0.5,
            )

    def chat(self, user_text: str, voice_features: dict = None) -> tuple:
        voice_features = voice_features or {}

        web = self.searcher.get_context(user_text, {"topic": user_text}, self.history.get())

        context = user_text
        if web["context"]:
            tag = "🌐 웹 검색 결과" if web["mode"] == "online" else "📴 오프라인 참고"
            context += f"\n[{tag}: {web['context']}]"
        if voice_features.get("summary") and voice_features["summary"] != "특이사항 없음":
            context += f"\n[음성 분석: {voice_features['summary']}]"

        # user 메시지 — autosave=False (아직 턴 미완료)
        self.history.add("user", user_text, autosave=False)

        response = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": self.persona}]
                     + self.history.get()[:-1]
                     + [{"role": "user", "content": context}]
        )

        output = self._parse_output(response["message"]["content"])

        # assistant 메시지 — autosave=True (턴 완료 시 한 번만 저장)
        self.history.add("assistant", output.text, autosave=True)
        return output, {}

    def summarize(self) -> str:
        if not self.history.get():
            return "대화 없음"
        msgs = "\n".join(
            f"{'사용자' if m['role']=='user' else self.name}: {m['content']}"
            for m in self.history.get()
        )
        result = ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": f"아래 대화를 한국어로 3줄 요약해:\n\n{msgs}"}]
        )
        return result["message"]["content"]


# ═══════════════════════════════════════════════════════
#  출력 렌더러
# ═══════════════════════════════════════════════════════
class OutputRenderer:
    EMOTION_ICON = {
        "neutral":   "😐",
        "curious":   "🤔",
        "excited":   "⚡",
        "confused":  "😕",
        "focused":   "🎯",
        "skeptical": "🧐",
    }

    def __init__(self, tts_instance):
        self.voice_engine = TextToSpeech(tts_instance)

    def render(self, output: AIOutput, reasoning: dict = None, voice: dict = None, speak: bool = True):
        icon = self.EMOTION_ICON.get(output.emotion, "🤖")
        name = CONFIG["ai_name"]
        print(f"\n{icon} {name}: {output.text}")
        print(f"  [감정:{output.emotion} | 강도:{output.intensity:.1f} | 확신:{output.confidence:.1f}]")

        if output.inner_thought and "[JSON" not in output.inner_thought:
            print(f"  [내부사고: {output.inner_thought}]")
        if voice and voice.get("summary") and voice["summary"] != "특이사항 없음":
            print(f"  [음성분석: {voice['summary']}]")

        if speak:
            self.voice_engine.speak(output)


# ═══════════════════════════════════════════════════════
#  실시간 대화 루프
# ═══════════════════════════════════════════════════════
def realtime_loop(ai: AICore, stt: SpeechToText, renderer: OutputRenderer):
    print("\n🔄 실시간 모드 시작 (Ctrl+C로 종료)")
    print("─" * 40)

    while True:
        try:
            text, voice_features = stt.transcribe()
            if not text:
                print("[인식 실패, 다시 말해봐]")
                continue
            print(f"[인식]: {text}")
            output, reasoning = ai.chat(text, voice_features)
            renderer.render(output, reasoning, voice_features, speak=True)
        except KeyboardInterrupt:
            print("\n[실시간 모드 종료]")
            break


# ═══════════════════════════════════════════════════════
#  메인 — TTS 로딩을 여기서 시작 (import 시 즉시 실행 방지)
# ═══════════════════════════════════════════════════════
def main():
    # ── GPU 확인 및 TTS 모델 로딩 ────────────────────────
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"🚀 모델 로딩 시작... (장치: {device})")

    tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
    tts.to(device)

    if hasattr(torch, "compile"):
        print("⚡ Blackwell Tensor Core 최적화 컴파일 시작...")
        tts.synthesizer.tts_model = torch.compile(tts.synthesizer.tts_model)

    print(f"✅ 준비 완료! 현재 장치: {tts.synthesizer.tts_model.device}")
    print(f"DEBUG: XTTS VRAM -> {torch.cuda.memory_allocated() / 1024**2:.2f} MB")

    # ── 워밍업 ───────────────────────────────────────────
    print("⚡ 엔진 예열 중(Warmup)...")
    with torch.no_grad():
        tts.tts_to_file(text="예열", speaker=CONFIG["tts_speaker"], language="ko", file_path="warmup.wav")
    if os.path.exists("warmup.wav"):
        os.remove("warmup.wav")

    print("=" * 55)
    print(f"   {CONFIG['ai_name']} — Smart Personal AI")
    print("=" * 55)
    print("  명령어: 'v' 음성입력 | 'rt' 실시간모드")
    print("          'reset' 기억초기화 | 'summary' 요약 | 'exit' 종료")
    print("=" * 55)

    ai       = AICore()
    renderer = OutputRenderer(tts)
    stt: Optional[SpeechToText] = None

    while True:
        try:
            user_input = input("\n나: ").strip()
            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("종료합니다.")
                break
            if user_input.lower() == "reset":
                ai.history.reset()
                continue
            if user_input.lower() == "summary":
                print("\n[대화 요약]\n", ai.summarize())
                continue
            if user_input.lower() == "rt":
                if stt is None:
                    stt = SpeechToText(CONFIG)
                realtime_loop(ai, stt, renderer)
                continue
            if user_input.lower() == "v":
                if stt is None:
                    stt = SpeechToText(CONFIG)
                text, voice_features = stt.transcribe()
                if not text:
                    print("[인식 실패]")
                    continue
                print(f"[인식]: {text}")
                output, reasoning = ai.chat(text, voice_features)
                renderer.render(output, reasoning, voice_features)
            else:
                output, reasoning = ai.chat(user_input)
                renderer.render(output, reasoning)

        except KeyboardInterrupt:
            print("\n종료합니다.")
            break
        except Exception as e:
            print(f"[오류] {e}")
            continue


if __name__ == "__main__":
    main()
