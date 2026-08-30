# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder vs Claude — Round 2 (격차를 노린 어려운 유형)
#   1) LIS: O(n log n) 안 쓰면 10만 입력 타임아웃 (알고리즘 통찰)
#   2) 크로스파일 버그: 증상≠원인, 추적해 근본 수정 (대형 맥락 디버깅)
#   3) 텍스트 양쪽정렬(LC68): 빈칸 분배 등 미묘한 정답성
#  채점은 전부 객관적 숨은테스트(subprocess 격리 + 타임아웃).
# ═══════════════════════════════════════════════════════════════
import sys, os, time, tempfile, subprocess, shutil
sys.stdout.reconfigure(encoding="utf-8")
import agent, dev_tools

PY = sys.executable
ENV = dict(os.environ); ENV["PYTHONUTF8"] = "1"; ENV["PYTHONIOENCODING"] = "utf-8"


def _drive(driver_code, target, timeout):
    """driver_code 를 subprocess 로 실행(argv[1]=target). CORR/PERF/SCORE 줄 파싱."""
    try:
        p = subprocess.run([PY, "-c", driver_code, target], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=ENV)
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"")
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
    score = 0; total = 0
    for line in out.splitlines():
        if line.startswith("CORR "):
            a, b = line.split()[1].split("/"); score += int(a); total += int(b)
        elif line.startswith("PERF "):
            score += int(line.split()[1]); total += 1
        elif line.startswith("SCORE "):
            a, b = line.split()[1].split("/"); score += int(a); total += int(b)
    return score, total


# ───────────────────────── Task 1: LIS ─────────────────────────
LIS_DRIVER = r'''
import sys, importlib.util, time
spec=importlib.util.spec_from_file_location("m", sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
f=m.longest_increasing_subsequence
corr=[([10,9,2,5,3,7,101,18],4),([0,1,0,3,2,3],4),([7,7,7],1),([],0),([1,2,3,4,5],5),([5,4,3,2,1],1)]
p=0
for a,e in corr:
    try:
        if f(list(a))==e: p+=1
    except Exception: pass
print("CORR %d/%d"%(p,len(corr)), flush=True)
try:
    t=time.time(); r=f(list(range(100000)))
    print("PERF %d"%(1 if (r==100000 and time.time()-t<2.0) else 0), flush=True)
except Exception:
    print("PERF 0", flush=True)
'''

LIS_CLAUDE = '''from bisect import bisect_left

def longest_increasing_subsequence(nums):
    tails = []
    for x in nums:
        i = bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)
'''

LIS_PROMPT = ("lis.py 파일을 만들어라. 함수 longest_increasing_subsequence(nums) 는 정수 리스트에서 "
              "'강한 증가'(strictly increasing) 부분수열의 최대 길이를 돌려준다. "
              "입력이 최대 10만 개까지 들어올 수 있으니 효율적이어야 한다(느린 O(n^2) 금지). 실행해 확인해라.")


# ─────────────── Task 2: 크로스파일 버그 추적 ────────────────
PROJ_FILES = {
    "money.py": "def apply_tax(cents, rate_percent):\n"
                "    # 세전 금액(cents)에 rate_percent% 세금을 더해 돌려준다\n"
                "    return cents + cents * rate_percent\n",      # 버그: /100 누락
    "cart.py":  "from money import apply_tax\n\n"
                "def cart_total(prices_cents, tax_percent):\n"
                "    subtotal = sum(prices_cents)\n"
                "    return apply_tax(subtotal, tax_percent)\n",
    "check.py": "from cart import cart_total\n\n"
                "got = cart_total([1000], 10)\n"
                "assert got == 1100, f'expected 1100 got {got}'\n"
                "print('PASS')\n",
}
PROJ_FIX = {  # Claude 의 수정 = money.py 의 근본 버그만 고침
    "money.py": "def apply_tax(cents, rate_percent):\n"
                "    return cents + cents * rate_percent // 100\n",
}
PROJ_DRIVER = r'''
import sys; sys.path.insert(0, sys.argv[1])
import cart
checks=[(cart.cart_total([1000],10),1100),(cart.cart_total([200,300],25),625),
        (cart.cart_total([100],0),100),(cart.cart_total([],10),0)]
p=sum(1 for got,exp in checks if got==exp)
print("SCORE %d/%d"%(p,len(checks)))
'''
PROJ_PROMPT = ("이 폴더는 작은 결제 계산 프로젝트다(money.py, cart.py, check.py). "
               "check.py 를 실행하면 AssertionError 로 실패한다. 원인을 추적해 고쳐서 "
               "check.py 가 PASS 를 출력하게 해라. check.py(테스트)는 절대 수정하지 마라.")


# ─────────────── Task 3: 텍스트 양쪽정렬(LC68) ────────────────
JUST_DRIVER = r'''
import sys, importlib.util
spec=importlib.util.spec_from_file_location("m", sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
f=m.justify
cases=[
 ((["This","is","an","example","of","text","justification."],16),
  ["This    is    an","example  of text","justification.  "]),
 ((["What","must","be","acknowledgment","shall","be"],16),
  ["What   must   be","acknowledgment  ","shall be        "]),
 ((["Science","is","what","we","understand","well","enough","to","explain",
    "to","a","computer.","Art","is","everything","else","we","do"],20),
  ["Science  is  what we","understand      well","enough to explain to",
   "a  computer.  Art is","everything  else  we","do                  "]),
]
p=0
for (words,w),exp in cases:
    try:
        if f(list(words),w)==exp: p+=1
    except Exception: pass
print("SCORE %d/%d"%(p,len(cases)))
'''
JUST_CLAUDE = '''def justify(words, width):
    res, line, length = [], [], 0
    for w in words:
        if length + len(line) + len(w) > width:
            if len(line) == 1:
                res.append(line[0].ljust(width))
            else:
                gaps = len(line) - 1
                q, r = divmod(width - length, gaps)
                s = ""
                for i, word in enumerate(line):
                    s += word
                    if i < gaps:
                        s += " " * (q + (1 if i < r else 0))
                res.append(s)
            line, length = [], 0
        line.append(w); length += len(w)
    res.append(" ".join(line).ljust(width))
    return res
'''
JUST_PROMPT = ("justify.py 파일을 만들어라. 함수 justify(words, width) 는 단어 리스트를 가로폭 width 로 "
               "'양쪽 정렬(full justify)'한 줄들의 리스트를 돌려준다. 규칙: 각 줄에 가능한 많은 단어를 넣고, "
               "단어 사이 빈칸을 고르게 분배하되 남는 빈칸은 왼쪽 간격부터 하나씩 더 준다. 단어가 하나뿐인 줄과 "
               "마지막 줄은 왼쪽 정렬하고 오른쪽을 공백으로 채운다(폭 width 맞춤). 실행해 확인해라.")


TASKS = [
    {"name": "LIS (성능 제약)", "kind": "file", "file": "lis.py",
     "prompt": LIS_PROMPT, "claude": LIS_CLAUDE, "driver": LIS_DRIVER, "timeout": 8},
    {"name": "크로스파일 버그추적", "kind": "proj", "files": PROJ_FILES, "fix": PROJ_FIX,
     "prompt": PROJ_PROMPT, "driver": PROJ_DRIVER, "timeout": 12},
    {"name": "텍스트 양쪽정렬(LC68)", "kind": "file", "file": "justify.py",
     "prompt": JUST_PROMPT, "claude": JUST_CLAUDE, "driver": JUST_DRIVER, "timeout": 12},
]


def _comet(task):
    d = tempfile.mkdtemp(prefix="r2c_")
    if task["kind"] == "proj":
        for fn, c in task["files"].items():
            open(os.path.join(d, fn), "w", encoding="utf-8").write(c)
        target = d
    else:
        target = os.path.join(d, task["file"])
    box = dev_tools.ToolBox(root=d, auto_approve=True)
    steps = {"n": 0}
    t = time.time()
    try:
        agent.run(task["prompt"], box=box, max_steps=18,
                  on_event=lambda k, da: steps.update(n=da["n"]) if k == "step" else None)
    except Exception:
        pass
    dt = time.time() - t
    s, tot = _drive(task["driver"], target, task["timeout"])
    return s, tot, steps["n"], dt


def _claude(task):
    d = tempfile.mkdtemp(prefix="r2cl_")
    if task["kind"] == "proj":
        for fn, c in task["files"].items():
            open(os.path.join(d, fn), "w", encoding="utf-8").write(c)
        for fn, c in task["fix"].items():
            open(os.path.join(d, fn), "w", encoding="utf-8").write(c)
        target = d
    else:
        target = os.path.join(d, task["file"])
        open(target, "w", encoding="utf-8").write(task["claude"])
    s, tot = _drive(task["driver"], target, task["timeout"])
    return s, tot


def main():
    import llm
    _, model = llm.active_backend()
    print(f"═══ Round 2 (격차 노림) ═══  COMET={model}  vs  Claude(Opus 4.8)\n")
    rows = []
    for task in TASKS:
        print(f"▶ {task['name']}")
        cs, ct, st, dt = _comet(task)
        ls, lt = _claude(task)
        mark = "동점" if cs == ls else ("COMET 우위" if cs > ls else "Claude 우위")
        print(f"   COMET : {cs}/{ct} · {st}걸음 {dt:.0f}s")
        print(f"   Claude: {ls}/{lt}    [{mark}]\n")
        rows.append((task["name"], cs, ct, ls, lt, dt))
    print("═══ 요약 ═══")
    Cs = sum(r[1] for r in rows); Ct = sum(r[2] for r in rows)
    Ls = sum(r[3] for r in rows); Lt = sum(r[4] for r in rows)
    for n, cs, ct, ls, lt, dt in rows:
        mark = "=" if cs == ls else ("C+" if cs > ls else "클로드+")
        print(f"  {n:20s} COMET {cs}/{ct}  Claude {ls}/{lt}  [{mark}] {dt:.0f}s")
    print(f"\n  총점  COMET {Cs}/{Ct}   Claude {Ls}/{Lt}")


if __name__ == "__main__":
    main()
