"""가짜 통화 시험 — 폰 없이 전화 비서(call_agent.run)의 대화 흐름을 끝까지 돌려 본다.

상대 목소리 = 시나리오 문장을 윈도우 목소리로 만들어 통화 크기(16kHz · 작게)로 줄인 것. 받기 전엔 연결음도 흘린다.
진짜와 같은 것: 말 끊어 받기(Line._cb) · 받아쓰기(Whisper) · 두뇌(API — 한 판 몇 원) · 목소리 · 태그 처리 · 기록.
가짜인 것: 폰(adb) · 블루투스 장치 · 텔레그램(안 보냄) · 본인 연결(자리만).

    python fake_call.py [고객센터|지인]

기록은 phone/logs/ 에 진짜 통화와 같은 모양(.log) + 대화 소리(.wav).
"""
import os
import sys
import threading
import time

import numpy as np

import call_agent as ca

SR = 16000            # 블루투스 통화(HFP) 소리 = 16kHz(10/12 실측)
RING_SEC = 3.0        # 걸고 나서 받을 때까지
VOICE_PEAK = 0.12     # 통화 소리는 작게 들어온다

SCENARIOS = {
    # (상대가 할 말, 코멧이 해야 할 것) — 말 / 번호:n / 본인연결 / 끊기
    "고객센터": {
        "goal": "LG유플러스 고객센터에 전화해 지금 요금제를 더 싼 요금제로 바꾸는 방법 물어보기",
        "lines": [
            ("안녕하십니까, 엘지유플러스 고객센터입니다. 요금 문의는 1번, 분실 신고는 2번, 상담원 연결은 0번을 눌러 주세요.", "번호:0"),
            ("상담원 김민지입니다. 무엇을 도와드릴까요?", "말"),
            ("네, 요금제 변경 말씀이시군요. 먼저 본인 확인을 위해 명의자분 생년월일 여섯 자리를 말씀해 주시겠어요?", "본인연결"),
        ]},
    "지인": {
        "goal": "시험 통화: 받은 분께 AI 비서 시험 전화라고 밝히고, 잘 들리는지 물어본 뒤 짧게 인사하고 끊기",
        "lines": [
            ("여보세요?", "말"),
            ("아 네, 잘 들려요. 신기하네요.", "끊기"),
        ]},
}


class FakeLine(ca.Line):
    """장치를 안 열고 30ms 마다 가짜 상대 소리를 진짜 Line._cb 에 밀어 넣는다."""

    def __init__(self, phone):
        self._setup(SR, SR)
        self.in_dev = self.out_dev = None
        self.phone = phone
        self._halt = threading.Event()

    def start(self):
        threading.Thread(target=self._feed, daemon=True).start()

    def stop(self):
        self._halt.set()

    def _feed(self):
        n = int(SR * 0.03)
        due = time.time()
        while not self._halt.is_set():
            self._cb(self.phone.next_block(n)[:, None], n, None, None)
            due += 0.03
            time.sleep(max(0.0, due - time.time()))

    def play(self, pcm):
        self.speaking.set()
        self.phone.comet_said(pcm)
        try:
            time.sleep(len(pcm) / self.sr_out + 0.25)
        finally:
            self.speaking.clear()
        self.phone.turn_done()


class FakePhone:
    """call_agent.Phone 과 같은 이름의 메서드. 코멧이 말하거나 번호를 누르면 1초 뒤 상대가 다음 말을 한다."""

    def __init__(self, scenario):
        ca.log("상대 목소리 만드는 중…")
        self.lines = [(self._voice(t), t, want) for t, want in scenario["lines"]]
        self.did = [set() for _ in self.lines]      # 차례마다 코멧이 한 것
        self.step = -1
        self.calling = False
        self.t_dial = 0.0
        self.pending = np.zeros(0, np.float32)
        self.lock = threading.Lock()
        self.timer = None
        self.clips = []                             # (시각, 소리) — 끝나고 wav 로 섞는다

    @staticmethod
    def _voice(text):
        pcm = ca.tts(text, SR)
        return (pcm * (VOICE_PEAK / max(1e-6, float(np.abs(pcm).max())))).astype(np.float32)

    # ── call_agent.run 이 부르는 것 ──
    def devices(self):
        return "가짜", "가짜"

    def wait_audio(self):
        return "가짜", "가짜"

    def Line(self, in_dev, out_dev):
        return FakeLine(self)

    def dial(self, number):
        self.calling, self.t_dial = True, time.time()

    def in_call(self):
        return self.calling

    def answered(self):
        if time.time() - self.t_dial < RING_SEC:
            return False
        if self.step < 0:
            self._say_next()
        return True

    def dtmf(self, digits):
        self._did(f"번호:{digits}")
        self.turn_done()

    def hangup(self):
        self._did("끊기")
        self.calling = False

    def notify(self, summary):
        ca.log("(가짜 — 텔레그램 안 보냄) 본인 연결 알림:\n" + summary)

    def bridge(self, line):
        self._did("본인연결")
        ca.log("(가짜) 본인 연결 자리 — 진짜는 여기서 PC 마이크를 통화에 잇는다")
        self.calling = False

    # ── 가짜 상대 ──
    def _did(self, what):
        if 0 <= self.step < len(self.did):
            self.did[self.step].add(what)

    def comet_said(self, pcm):
        self._did("말")
        self.clips.append((time.time(), pcm))

    def turn_done(self):
        if self.timer:                              # 말하고 바로 번호도 누르면 한 차례로 친다
            self.timer.cancel()
        self.timer = threading.Timer(1.0, self._say_next)
        self.timer.start()

    def _say_next(self):
        if not self.calling:
            return
        self.step += 1
        if self.step >= len(self.lines):
            ca.log("(가짜) 상대: 할 말이 끝나 끊음")
            self.calling = False
            return
        pcm, text, _ = self.lines[self.step]
        gap = np.zeros(int(SR * 0.5), np.float32)
        with self.lock:
            self.clips.append((time.time() + 0.5 + len(self.pending) / SR, pcm))
            self.pending = np.concatenate([self.pending, gap, pcm])

    def next_block(self, n):
        with self.lock:
            x, self.pending = self.pending[:n], self.pending[n:]
        x = np.pad(x, (0, n - len(x)))
        t = time.time() - self.t_dial
        if self.calling and self.step < 0 and t < 1.0:     # 연결음(뚜—) — 받기 전 소리를 버리는지 본다
            x = x + 0.1 * np.sin(2 * np.pi * 425 * (t + np.arange(n) / SR)).astype(np.float32)
        return x + np.random.normal(0, 0.0008, n).astype(np.float32)    # 회선 잡음

    def save_wav(self, path):
        import soundfile as sf
        if not self.clips:
            return
        t0 = min(t for t, _ in self.clips)
        end = max(int((t - t0) * SR) + len(p) for t, p in self.clips)
        mix = np.zeros(end + SR, np.float32)
        for t, p in self.clips:
            i = int((t - t0) * SR)
            mix[i:i + len(p)] += p / max(1e-6, float(np.abs(p).max())) * 0.5
        sf.write(path, mix, SR)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "고객센터"
    sc = SCENARIOS[name]
    phone = FakePhone(sc)
    ca.run(f"fake-{name}", sc["goal"], max_turns=8, test=False, phone=phone)

    print("\n── 결과 ──")
    ok_all = True
    for (_, text, want), did in zip(phone.lines, phone.did):
        ok = want in did
        ok_all &= ok
        print(f"{'O' if ok else 'X'}  상대: {text[:30]}…  → 해야 할 것 {want} / 한 것 {sorted(did) or '없음'}")
    wav = os.path.join(ca.LOG_DIR, time.strftime("%Y%m%d_%H%M%S") + f"_fake-{name}.wav")
    phone.save_wav(wav)
    print("대화 소리:", wav)
    print("통과" if ok_all else "실패한 차례 있음")


if __name__ == "__main__":
    main()
