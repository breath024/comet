# -*- coding: utf-8 -*-
# analyst 모델 A/B/C 비교 (일회용). 근거 1회 수집 → 3구성 동일 근거로 판단 → md 저장.
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import analyst

Q = " ".join(sys.argv[1:]) or "엔비디아 지금 사도 될까"
M14, M27 = "qwen3:14b", "gemma3:27b"
CONFIGS = [
    ("A. 14b / 14b (현재 기본)", M14, M14),
    ("B. 27b / 27b (전부 고성능)", M27, M27),
    ("C. 27b / 14b (하이브리드 추천)", M27, M14),
]

print(f"질문: {Q}\n수집(1회, 공통 근거)...")
ev = analyst.gather(Q)
print(f"  티커 {ev['ticker']} · 가격 {'O' if ev['px'] else 'X'} · 본문 {ev['main'].get('count',0)}건\n")

out = [f"# analyst 모델 비교 — \"{Q}\"\n",
       f"공통 근거: 티커 {ev['ticker']}, 본문 {ev['main'].get('count',0)}건 (한 번 수집해 3개 구성에 동일 적용 = 공정 비교)\n",
       "## 📈 실데이터 (공통)\n" + analyst._px_lines(ev['px']) + "\n"]
timing = []
for name, m1, m2 in CONFIGS:
    print(f"=== {name} 실행 중 ===")
    t = time.time()
    r = analyst.judge(ev, m1, m2, keep_alive="5m", verbose=True)
    dt = time.time() - t
    timing.append((name, dt))
    print(f"  → {dt:.0f}초\n")
    out.append(f"\n---\n\n# {name}  · {dt:.0f}초\n")
    out.append("## 🧠 1차 (컨센서스+2차효과)\n" + (r.get('pass1') or r.get('msg', '')) + "\n")
    out.append("## 🔴 2차 (역발상+사실감사)\n" + (r.get('pass2') or '') + "\n")

out.append("\n---\n\n## 속도 요약\n")
for name, dt in timing:
    out.append(f"- {name}: {dt:.0f}초")

path = r"C:\Users\USER\Desktop\analyst_비교.md"
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("저장:", path, "\n속도:")
for name, dt in timing:
    print(f"  {name}: {dt:.0f}초")
