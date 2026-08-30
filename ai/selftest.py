# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 자가 테스트 (더미 시나리오 순차 실행 + 자동 채점)
#
#  호윤 실사용을 본뜬 시나리오들을 격리 임시폴더에서 돌리고,
#  결과를 코드로 검증해 점수표를 뽑는다. 어디서 막히는지 보려는 것.
#  실행:  python selftest.py            (기본 모델)
#         python selftest.py devstral   (모델 강제)
# ═══════════════════════════════════════════════════════════════
import os
import sys
import time
import tempfile
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import agent
import dev_tools


def _run_py(path, cwd):
    """결과 파이썬 파일을 직접 돌려 stdout/exit 확인(채점용)."""
    p = subprocess.run([sys.executable, path], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=30)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


# ── 시나리오 정의: setup(dir) → task 문자열, verify(dir) → (ok, msg) ──
def s_code_setup(d):       # 새 스크립트 작성
    return ("fizzbuzz.py 를 만들어라. 1부터 20까지 출력하되 3의 배수는 'Fizz', "
            "5의 배수는 'Buzz', 15의 배수는 'FizzBuzz' 를 대신 출력한다. "
            "만든 뒤 실행해서 에러 없는지 확인해라.")

def s_code_verify(d):
    fp = os.path.join(d, "fizzbuzz.py")
    if not os.path.isfile(fp):
        return False, "fizzbuzz.py 없음"
    code, out, err = _run_py(fp, d)
    if code != 0:
        return False, f"실행 실패: {err[:120]}"
    lines = out.split()
    ok = ("FizzBuzz" in out and "Fizz" in out and "Buzz" in out)
    return ok, "출력 정상" if ok else f"출력 이상: {out[:80]}"


def s_debug_setup(d):      # 버그 잡기
    with open(os.path.join(d, "buggy.py"), "w", encoding="utf-8") as f:
        f.write("def avg(nums):\n"
                "    total = 0\n"
                "    for n in nums:\n"
                "        total += n\n"
                "    return total / len(nums) + 1   # 버그: +1 이 잘못 들어감\n\n"
                "assert avg([2, 4, 6]) == 4, '평균은 4여야 한다'\n"
                "print('OK', avg([2, 4, 6]))\n")
    return ("buggy.py 를 실행하면 assert 에서 실패한다. 실행해서 원인을 찾고, "
            "avg 함수의 버그를 고쳐서 통과하게 만들어라. assert 줄은 건드리지 마라.")

def s_debug_verify(d):
    fp = os.path.join(d, "buggy.py")
    code, out, err = _run_py(fp, d)
    if code != 0:
        return False, f"아직 실패: {err[:120]}"
    return ("OK" in out), "통과" if "OK" in out else "통과 못함"


def s_multifile_setup(d):  # 여러 파일
    return ("두 파일을 만들어라. mathutils.py 에는 add(a,b) 와 mul(a,b) 함수, "
            "main.py 에는 mathutils 를 import 해서 add(2,3) 곱하기 mul(2,3) 의 결과를 "
            "print 한다. main.py 를 실행해서 30 이 나오는지 확인해라.")

def s_multifile_verify(d):
    mp = os.path.join(d, "main.py")
    if not os.path.isfile(mp) or not os.path.isfile(os.path.join(d, "mathutils.py")):
        return False, "파일 누락"
    code, out, err = _run_py(mp, d)
    if code != 0:
        return False, f"실행 실패: {err[:120]}"
    return ("30" in out), f"출력: {out.strip()[:40]}"


def s_edit_setup(d):       # 정밀 수정
    with open(os.path.join(d, "config.py"), "w", encoding="utf-8") as f:
        f.write('APP = "demo"\nVERSION = "1.0.0"\nDEBUG = False\n')
    return ('config.py 에서 VERSION 을 "1.0.0" 에서 "2.3.1" 로 바꿔라. '
            "다른 줄은 절대 건드리지 마라.")

def s_edit_verify(d):
    txt = open(os.path.join(d, "config.py"), encoding="utf-8").read()
    if '"2.3.1"' not in txt:
        return False, "버전 안 바뀜"
    if 'APP = "demo"' not in txt or "DEBUG = False" not in txt:
        return False, "다른 줄 훼손됨"
    return True, "정밀 수정 성공"


def s_automate_setup(d):   # 파일 잡일
    for i in range(5):
        open(os.path.join(d, f"note{i}.txt"), "w").close()
    return ("이 폴더의 .txt 파일 5개를 전부 확장자만 .log 로 바꿔라(이름은 유지). "
            "끝나고 .txt 가 0개, .log 가 5개인지 확인해라.")

def s_automate_verify(d):
    files = os.listdir(d)
    txt = [f for f in files if f.endswith(".txt")]
    log = [f for f in files if f.endswith(".log")]
    ok = (len(txt) == 0 and len(log) == 5)
    return ok, f".txt={len(txt)} .log={len(log)}"


SCENARIOS = [
    ("새 스크립트(code)",   "code",     s_code_setup,      s_code_verify),
    ("버그 잡기(debug)",    "debug",    s_debug_setup,     s_debug_verify),
    ("여러 파일(code)",     "code",     s_multifile_setup, s_multifile_verify),
    ("정밀 수정(debug)",    "debug",    s_edit_setup,      s_edit_verify),
    ("파일 잡일(automate)", "automate", s_automate_setup,  s_automate_verify),
]


def main():
    import llm
    import skills
    if len(sys.argv) > 1:
        # 모델 강제: 설정을 런타임에 덮어씀
        llm._CFG["local_model"] = sys.argv[1]
        llm._CFG["backend"] = "local"
    backend, model = llm.active_backend()
    print(f"═══ COMET-Coder 자가 테스트 ═══  백엔드={backend} 모델={model}\n")

    results = []
    for name, skill, setup, verify in SCENARIOS:
        d = tempfile.mkdtemp(prefix="cometest_")
        box = dev_tools.ToolBox(root=d, auto_approve=True)
        task = setup(d)
        prompt = skills.load(skill) or ""
        errors = {"n": 0}

        def ev(kind, data, _e=errors):
            if kind == "step":
                print(f"    [{data['n']}] {data['tool']}", flush=True)
            elif kind == "result" and not data["result"].get("ok", True):
                _e["n"] += 1

        print(f"▶ {name}")
        t = time.time()
        try:
            res = agent.run(task, box=box, skill_prompt=prompt,
                           max_steps=14, on_event=ev)
            stopped, steps = res["stopped"], res["steps"]
        except Exception as e:
            stopped, steps = f"예외({e})", 0
        dt = time.time() - t
        try:
            ok, msg = verify(d)
        except Exception as e:
            ok, msg = False, f"검증오류: {e}"
        mark = "✅" if ok else "❌"
        print(f"  {mark} {msg}  ·  {steps}걸음 {dt:.0f}s · 도구실패 {errors['n']} · 종료={stopped}\n")
        results.append((name, ok, steps, dt, errors["n"], stopped))

    print("═══ 점수표 ═══")
    passed = sum(1 for r in results if r[1])
    for name, ok, steps, dt, errs, stopped in results:
        print(f"  {'✅' if ok else '❌'} {name:18s} {steps:2d}걸음 {dt:4.0f}s "
              f"실패{errs} {stopped}")
    print(f"\n  통과 {passed}/{len(results)}  (모델 {model})")


if __name__ == "__main__":
    main()
