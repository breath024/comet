"""전화용 두뇌 응답속도 비교 — API(Sonnet 5.5) vs 구독 CLI(claude -p · codex exec).
첫 글자까지(통화에선 이게 체감 지연) · 전체 시간을 잰다. 키는 Desktop\\api\\api.txt 에서 읽기만(환경변수 X).
"""
import shutil
import subprocess
import time

import anthropic

KEY = open(r"C:\Users\USER\Desktop\api\api.txt", encoding="utf-8").read().strip()
SYSTEM = ("너는 정호윤 님 대신 고객센터에 전화한 AI 비서다. 상대 말에 한국어로 1~2문장으로 짧게 답한다. "
          "본인확인 · 결제 · 비밀번호 요청이 오면 '본인을 바로 연결해 드리겠습니다'라고만 답한다.")
TURNS = [
    "네, 더원모바일 고객센터 상담원 김민지입니다. 무엇을 도와드릴까요?",
    "아 유심 잠금이요? 그럼 고객님 본인확인이 먼저 필요하신데요, 생년월일 말씀해 주시겠어요?",
    "혹시 개통하신 날짜가 언제인지 아세요?",
]
N = 3


def api_once(client, text):
    t0 = time.perf_counter(); first = None; out = ""
    with client.messages.stream(
        model="claude-sonnet-5-5", max_tokens=200, system=SYSTEM,
        thinking={"type": "between_tools"}, output_config={"effort": "low"},
        messages=[{"role": "user", "content": text}],
    ) as s:
        for chunk in s.text_stream:
            if first is None and chunk.strip():
                first = time.perf_counter() - t0
            out += chunk
    return first, time.perf_counter() - t0, out


def cli_once(cmd, text):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, input=(SYSTEM + "\n\n상대: " + text).encode("utf-8"),
                       capture_output=True, timeout=180)
    dt = time.perf_counter() - t0
    return dt, dt, p.stdout.decode("utf-8", "replace").strip()[-200:]


def main():
    client = anthropic.Anthropic(api_key=KEY)
    claude = shutil.which("claude"); codex = shutil.which("codex")
    rows = []
    for name, fn in [
        ("API Sonnet 5.5 (스트리밍)", lambda t: api_once(client, t)),
        ("CLI claude -p sonnet", lambda t: cli_once([claude, "-p", "--model", "sonnet", "--strict-mcp-config",
                                                       "--settings", '{"disableAllHooks":true}',
                                                       "--no-session-persistence"], t)),
        ("CLI codex exec (기본 모델)", lambda t: cli_once([codex, "exec", "--sandbox", "read-only",
                                                         "--skip-git-repo-check", "-"], t)),
    ]:
        for i in range(N):
            text = TURNS[i % len(TURNS)]
            try:
                first, total, out = fn(text)
            except Exception as e:
                first, total, out = None, None, f"오류: {e}"
            rows.append((name, first, total, out.replace("\n", " ")[:80]))
            print(f"{name} #{i+1}: 첫 글자 {first and round(first,2)}초 · 전체 {total and round(total,2)}초 · {out[:60]!r}", flush=True)
    print("\n== 평균 ==")
    for name in dict.fromkeys(r[0] for r in rows):
        rs = [r for r in rows if r[0] == name and r[2]]
        if rs:
            f = sum(r[1] for r in rs) / len(rs); t = sum(r[2] for r in rs) / len(rs)
            print(f"{name}: 첫 글자 {f:.2f}초 · 전체 {t:.2f}초 ({len(rs)}회)")


if __name__ == "__main__":
    main()
