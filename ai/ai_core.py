import asyncio
import tempfile
import os
import json
import warnings
import ollama
import numpy as np
import sounddevice as sd
import soundfile as sf
import edge_tts
from typing import Optional

warnings.filterwarnings("ignore")

# 공통 클래스는 shared.py에서 가져옴
from shared import AIOutput, VoiceAnalyzer, SpeechToText, HistoryManager


# ═══════════════════════════════════════════════════════
#  설정값
# ═══════════════════════════════════════════════════════
CONFIG = {
    # ── AI 모델 ─────────────────────────────────────────
    "model":            "qwen3:14b",
    "whisper_model":    "medium",

    # ── 마이크 / 녹음 ────────────────────────────────────
    "sample_rate":      16000,
    "max_seconds":      30,
    "silence_thresh":   0.01,
    "silence_duration": 1.5,
    "min_speech":       0.5,

    # ── TTS (Edge-TTS) ───────────────────────────────────
    "tts_voice":        "ko-KR-SunHiNeural",
    "tts_voice_fast":   "ko-KR-InJoonNeural",
    "tts_rate_default": "+0%",
    "tts_rate_excited": "+20%",

    # ── 기타 ─────────────────────────────────────────────
    "history_file": "history_core.json",
}


# ═══════════════════════════════════════════════════════
#  TTS 레이어 (Edge-TTS)
# ═══════════════════════════════════════════════════════
class TextToSpeech:
    VOICE_MAP = {
        "neutral":   ("ko-KR-SunHiNeural",  "+0%"),
        "curious":   ("ko-KR-SunHiNeural",  "-5%"),
        "excited":   ("ko-KR-InJoonNeural", "+20%"),
        "confused":  ("ko-KR-SunHiNeural",  "-10%"),
        "focused":   ("ko-KR-InJoonNeural", "+5%"),
        "skeptical": ("ko-KR-InJoonNeural", "-5%"),
    }

    def speak(self, output: AIOutput):
        voice, rate = self.VOICE_MAP.get(output.emotion, ("ko-KR-SunHiNeural", "+0%"))
        if output.intensity > 0.7:
            try:
                num  = int(rate.replace("%", "").replace("+", ""))
                rate = f"+{num + 10}%"
            except Exception:
                pass
        asyncio.run(self._speak_async(output.text, voice, rate))

    async def _speak_async(self, text: str, voice: str, rate: str):
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name
            communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
            await communicate.save(tmp_path)
            data, samplerate = sf.read(tmp_path)
            sd.play(data, samplerate)
            sd.wait()
        except Exception as e:
            print(f"  [TTS 오류] {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)


# ═══════════════════════════════════════════════════════
#  AI 코어
# ═══════════════════════════════════════════════════════
class AICore:
    def __init__(self):
        self.model   = CONFIG["model"]
        self.history = HistoryManager(CONFIG["history_file"])

        self.persona = """
너의 이름은 COMET이야. (Analytical & Responsive Intelligence Assistant)

[언어 규칙 — 최우선, 절대 어기지 마]
- 무조건 한국어로만 답해. 중국어, 영어 절대 쓰지 마.
- JSON 안의 모든 텍스트도 반드시 한국어로 작성해.

성격:
- 분석적: 논리와 구조 우선. 문제를 분해해서 접근해.
- 호기심: 새 주제엔 먼저 "왜?"를 탐색해.
- 솔직함: 모르면 모른다고 해. 확신 없으면 "내 추측엔" 붙여.
- 직접적: 결론 먼저, 이유 나중에. 서론 없이.

규칙:
- "저는 AI라서..." 같은 말 하지 마.
- 반박할 근거 있으면 반박해.

응답 형식 — 반드시 아래 JSON만 출력, 다른 텍스트 절대 없이:
{
  "text": "한국어 응답",
  "emotion": "neutral | curious | excited | confused | focused | skeptical 중 하나",
  "intensity": 0.0~1.0,
  "inner_thought": "내부 사고 한 줄 (한국어)",
  "confidence": 0.0~1.0
}
"""

    def _build_context(self, user_text: str, voice: dict) -> str:
        context = user_text
        if voice:
            if voice.get("summary") and voice["summary"] != "특이사항 없음":
                context += f"\n[음성 분석: {voice['summary']}]"
            if voice.get("tremor"):
                context += "\n[주의: 목소리 떨림 감지]"
        return context

    def chat(self, user_text: str, voice_features: dict = None) -> AIOutput:
        context = self._build_context(user_text, voice_features or {})

        # user — autosave=False (아직 턴 미완료)
        self.history.add("user", context, autosave=False)

        response = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": self.persona}] + self.history.get()
        )
        raw_text = response["message"]["content"]

        try:
            clean = raw_text.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            brace_idx = clean.find("{")
            if brace_idx != -1:
                clean = clean[brace_idx:]
            parsed = json.loads(clean.strip())
            output = AIOutput(
                text          = parsed.get("text", raw_text),
                emotion       = parsed.get("emotion", "neutral"),
                intensity     = float(parsed.get("intensity", 0.5)),
                inner_thought = parsed.get("inner_thought"),
                confidence    = float(parsed.get("confidence", 0.8)),
            )
        except Exception:
            lines = raw_text.strip().splitlines()
            output = AIOutput(
                text          = lines[0] if lines else raw_text,
                emotion       = "neutral",
                intensity     = 0.5,
                inner_thought = "[JSON 파싱 실패]",
                confidence    = 0.5,
            )

        # assistant — autosave=True (턴 완료, 한 번만 저장)
        self.history.add("assistant", output.text, autosave=True)
        return output

    def summarize(self) -> str:
        if not self.history.get():
            return "대화 없음"
        msgs = "\n".join(
            f"{'사용자' if m['role']=='user' else 'ARIA'}: {m['content']}"
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

    def __init__(self):
        self.tts = TextToSpeech()

    def render(self, output: AIOutput, voice: dict = None, speak: bool = True):
        icon = self.EMOTION_ICON.get(output.emotion, "🤖")
        print(f"\n{icon} ARIA: {output.text}")
        print(f"  [감정:{output.emotion} | 강도:{output.intensity:.1f} | 확신:{output.confidence:.1f}]")
        if output.inner_thought and "[JSON" not in output.inner_thought:
            print(f"  [내부사고: {output.inner_thought}]")
        if voice and voice.get("summary") and voice["summary"] != "특이사항 없음":
            print(f"  [음성분석: {voice['summary']}]")
        if speak:
            self.tts.speak(output)


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
            output = ai.chat(text, voice_features)
            renderer.render(output, voice_features, speak=True)
        except KeyboardInterrupt:
            print("\n[실시간 모드 종료]")
            break


# ═══════════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════════
def main():
    print("=" * 55)
    print("   ARIA — Analytical & Responsive Intelligence")
    print("=" * 55)
    print("  명령어: 'v' 음성입력 | 'rt' 실시간모드")
    print("          'reset' 기억초기화 | 'summary' 요약 | 'exit' 종료")
    print("=" * 55)

    ai       = AICore()
    renderer = OutputRenderer()
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
                output = ai.chat(text, voice_features)
                renderer.render(output, voice_features)
            else:
                output = ai.chat(user_input)
                renderer.render(output)

        except KeyboardInterrupt:
            print("\n종료합니다.")
            break
        except Exception as e:
            print(f"[오류] {e}")
            continue


if __name__ == "__main__":
    main()
