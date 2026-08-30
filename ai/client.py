# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 콘솔 클라이언트 — 데몬에 붙어서 대화 (외부 콘솔/다른 PC)
#   사용:  python client.py               (이 PC의 데몬)
#          python client.py 192.168.0.x   (다른 기기의 데몬)
# ═══════════════════════════════════════════════════════════════
import os
import sys
import json
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 8765
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon_token.txt")


def _token():
    # 우선순위: 환경변수 → 같은 폴더 토큰파일(로컬) → 직접 입력(원격)
    tok = os.environ.get("COMET_TOKEN", "").strip()
    if not tok and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
    if not tok:
        tok = input("접속 토큰: ").strip()
    return tok


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    url = f"http://{host}:{PORT}/chat"
    token = _token()
    print(f"COMET 클라이언트 → {host}:{PORT}   (종료: exit)")
    while True:
        try:
            text = input("\n나: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "종료", "quit"):
            break
        try:
            req = urllib.request.Request(
                url, data=json.dumps({"text": text}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                reply = json.loads(r.read().decode("utf-8")).get("reply", "")
            print(f"\nCOMET: {reply}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("  [인증 실패] 토큰이 틀림")
            else:
                print(f"  [오류] {e}")
        except Exception as e:
            print(f"  [연결 오류] {e}")


if __name__ == "__main__":
    main()
