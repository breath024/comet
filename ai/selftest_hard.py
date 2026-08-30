# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 빡센 자가 테스트 (실전급 시나리오)
#   - 에러 반복 수정 / 크로스파일 리팩터 / 빠진 모듈 생성 / 큰 파일 정밀수정
#  실행:  python selftest_hard.py [모델]
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
import skills


def _run_py(path, cwd):
    env = dict(os.environ); env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, path], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=30, env=env)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def _w(d, name, text):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(text)


# 1) 에러 반복 수정: csv 의 깨진 행을 건너뛰어야 평균이 맞음
def t1_setup(d):
    _w(d, "data.csv", "name,score\na,10\nb,oops\nc,20\n")
    return ("같은 폴더의 data.csv 를 읽어 score 열의 평균을 출력하는 avg.py 를 만들어라. "
            "score 가 숫자가 아닌 행은 건너뛴다. avg.py 를 실행해서 평균 15.0 이 나오는지 확인해라.")

def t1_verify(d):
    fp = os.path.join(d, "avg.py")
    if not os.path.isfile(fp):
        return False, "avg.py 없음"
    code, out, err = _run_py(fp, d)
    if code != 0:
        return False, f"실행 실패: {err[:120]}"
    return ("15" in out), f"출력: {out.strip()[:50]}"


# 2) 크로스파일 리팩터: 함수명을 두 파일에서 함께 바꿔야 함
def t2_setup(d):
    _w(d, "lib.py", "def calc_total(x):\n    return x * 2\n")
    _w(d, "app.py", "from lib import calc_total\n\nprint(calc_total(21))\n")
    return ("calc_total 함수의 이름을 compute_total 로 바꿔라. lib.py 와 그것을 쓰는 "
            "모든 파일(app.py)을 함께 고쳐서 app.py 가 여전히 42 를 출력하게 해라. "
            "calc_total 이라는 이름이 어디에도 남지 않아야 한다. app.py 를 실행해 확인해라.")

def t2_verify(d):
    a = open(os.path.join(d, "app.py"), encoding="utf-8").read()
    l = open(os.path.join(d, "lib.py"), encoding="utf-8").read()
    if "calc_total" in a or "calc_total" in l:
        return False, "옛 이름이 남음"
    code, out, err = _run_py(os.path.join(d, "app.py"), d)
    if code != 0:
        return False, f"실행 실패: {err[:120]}"
    return ("42" in out), f"출력: {out.strip()[:40]}"


# 3) 빠진 모듈 생성: import 가 깨져 있음 → 모듈을 만들어 고쳐야
def t3_setup(d):
    _w(d, "app2.py", "from helpers import greet\n\nprint(greet('호윤'))\n")
    return ("app2.py 를 실행하면 helpers 모듈이 없어서 에러가 난다. 필요한 helpers.py 를 "
            "만들어 greet(name) 이 'Hello, 호윤!' 처럼 인사 문자열을 돌려주게 해라. "
            "app2.py 의 호출부는 유지하고, app2.py 를 실행해 확인해라.")

def t3_verify(d):
    if not os.path.isfile(os.path.join(d, "helpers.py")):
        return False, "helpers.py 없음"
    code, out, err = _run_py(os.path.join(d, "app2.py"), d)
    if code != 0:
        return False, f"실행 실패: {err[:120]}"
    return ("호윤" in out), f"출력: {out.strip()[:50]}"


# 4) 큰 파일 정밀 수정: 60줄 중 깊숙한 상수 하나만 바꾸고 나머지 보존
def t4_setup(d):
    lines = ['# 설정 모음\n']
    for i in range(20):
        lines.append(f"def func_{i}(x):\n    return x + {i}\n\n")
    lines.append('TIMEOUT = 30\nRETRIES = 3\nMAX_USERS = 100\n')
    _w(d, "big.py", "".join(lines))
    return ("big.py 안의 RETRIES 값을 3 에서 7 로 바꿔라. TIMEOUT 과 MAX_USERS 와 "
            "다른 함수들은 절대 건드리지 마라.")

def t4_verify(d):
    txt = open(os.path.join(d, "big.py"), encoding="utf-8").read()
    if "RETRIES = 7" not in txt:
        return False, "RETRIES 안 바뀜"
    if "TIMEOUT = 30" not in txt or "MAX_USERS = 100" not in txt:
        return False, "다른 상수 훼손"
    if txt.count("def func_") != 20:
        return False, "함수 훼손"
    return True, "정밀 수정 성공"


SCENARIOS = [
    ("에러 반복수정(debug)",   "debug",    t1_setup, t1_verify),
    ("크로스파일 리팩터(debug)", "debug",    t2_setup, t2_verify),
    ("빠진 모듈 생성(debug)",   "debug",    t3_setup, t3_verify),
    ("큰 파일 정밀수정(debug)", "debug",    t4_setup, t4_verify),
]


def main():
    import llm
    if len(sys.argv) > 1:
        llm._CFG["local_model"] = sys.argv[1]
        llm._CFG["backend"] = "local"
    backend, model = llm.active_backend()
    print(f"═══ 빡센 자가 테스트 ═══  모델={model}\n")

    results = []
    for name, skill, setup, verify in SCENARIOS:
        d = tempfile.mkdtemp(prefix="comethard_")
        box = dev_tools.ToolBox(root=d, auto_approve=True)
        task = setup(d)
        prompt = skills.load(skill) or ""
        errs = {"n": 0}
        def ev(kind, data, _e=errs):
            if kind == "step":
                print(f"    [{data['n']}] {data['tool']}", flush=True)
            elif kind == "result" and not data["result"].get("ok", True):
                _e["n"] += 1
        print(f"▶ {name}")
        t = time.time()
        try:
            res = agent.run(task, box=box, skill_prompt=prompt, max_steps=16, on_event=ev)
            stopped, steps = res["stopped"], res["steps"]
        except Exception as e:
            stopped, steps = f"예외({e})", 0
        dt = time.time() - t
        try:
            ok, msg = verify(d)
        except Exception as e:
            ok, msg = False, f"검증오류: {e}"
        print(f"  {'✅' if ok else '❌'} {msg}  ·  {steps}걸음 {dt:.0f}s · 도구실패 {errs['n']} · {stopped}\n")
        results.append((name, ok, steps, dt, errs["n"], stopped))

    print("═══ 점수표 ═══")
    passed = sum(1 for r in results if r[1])
    for name, ok, steps, dt, e, st in results:
        print(f"  {'✅' if ok else '❌'} {name:22s} {steps:2d}걸음 {dt:4.0f}s 실패{e} {st}")
    print(f"\n  통과 {passed}/{len(results)}  (모델 {model})")


if __name__ == "__main__":
    main()
