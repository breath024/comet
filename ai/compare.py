# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder vs Claude Code — 같은 프롬프트, 같은 숨은 테스트로 채점
#
#  COMET 쪽: 에이전트가 실제로 파일을 만든다(qwen3-coder:30b).
#  Claude 쪽: claude_solution(아래 문자열)을 같은 파일명으로 쓴다.
#  둘 다 동일한 test() 로 검증 → 공정 비교. 난이도 쉬움→어려움.
# ═══════════════════════════════════════════════════════════════
import sys, os, time, tempfile, importlib.util
sys.stdout.reconfigure(encoding="utf-8")

import agent, dev_tools

_uid = [0]
def _load(path):
    _uid[0] += 1
    spec = importlib.util.spec_from_file_location(f"mod{_uid[0]}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── 테스트들 (모듈 m 을 받아 통과 개수/총개수 반환) ───────────────
def test_dedupe(m):
    cases = [
        (([1,2,1,3,2,1],), [1,2,3]),
        (([],), []),
        ((['x','x','y'],), ['x','y']),
        (([{'a':1},{'a':1},{'b':2}],), [{'a':1},{'b':2}]),   # 해시불가 함정
    ]
    p = 0
    for args, exp in cases:
        try:
            if m.dedupe(*args) == exp: p += 1
        except Exception: pass
    return p, len(cases)

def test_lru(m):
    checks = []
    try:
        c = m.LRUCache(2)
        c.put(1,1); c.put(2,2)
        checks.append(c.get(1) == 1)
        c.put(3,3)                       # 2 가 쫓겨남(1을 방금 get 했으니)
        checks.append(c.get(2) is None)
        checks.append(c.get(3) == 3)
        c.put(4,4)                       # 1 이 쫓겨남
        checks.append(c.get(1) is None)
        checks.append(c.get(3) == 3)
        checks.append(c.get(4) == 4)
    except Exception:
        pass
    return sum(1 for x in checks if x), 6

def test_expr(m):
    cases = [("2+3*4",14),("(2+3)*4",20),("10/4",2.5),("2*(3+(4-1))",12),("1+2+3",6)]
    p = 0
    for s, exp in cases:
        try:
            if abs(m.evaluate(s) - exp) < 1e-9: p += 1
        except Exception: pass
    return p, len(cases)


# ── Claude(나)의 답 — 요구사항만 보고 짠 것(숨은 테스트 안 봄) ──────
CLAUDE_DEDUPE = '''def dedupe(items):
    seen_hashable = set()
    seen_unhashable = []
    out = []
    for x in items:
        try:
            if x in seen_hashable:
                continue
            seen_hashable.add(x)
        except TypeError:          # dict 등 해시 불가
            if x in seen_unhashable:
                continue
            seen_unhashable.append(x)
        out.append(x)
    return out
'''

CLAUDE_LRU = '''from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return None
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
'''

CLAUDE_EXPR = '''def evaluate(expr):
    s = expr.replace(" ", "")
    pos = 0
    def parse_expr():
        nonlocal pos
        v = parse_term()
        while pos < len(s) and s[pos] in "+-":
            op = s[pos]; pos += 1
            r = parse_term()
            v = v + r if op == "+" else v - r
        return v
    def parse_term():
        nonlocal pos
        v = parse_factor()
        while pos < len(s) and s[pos] in "*/":
            op = s[pos]; pos += 1
            r = parse_factor()
            v = v * r if op == "*" else v / r
        return v
    def parse_factor():
        nonlocal pos
        if s[pos] == "(":
            pos += 1
            v = parse_expr()
            pos += 1
            return v
        start = pos
        while pos < len(s) and (s[pos].isdigit() or s[pos] == "."):
            pos += 1
        return float(s[start:pos])
    return float(parse_expr())
'''

TASKS = [
    {"name": "중복제거(해시불가 함정)", "file": "dedupe.py", "test": test_dedupe,
     "claude": CLAUDE_DEDUPE,
     "prompt": ("dedupe.py 파일을 만들어라. 함수 dedupe(items) 는 리스트에서 중복을 제거하되 "
                "원래 순서를 유지한다. dict 처럼 해시 불가능한 원소가 들어와도 동작해야 한다. "
                "만든 뒤 직접 실행해 확인해라.")},
    {"name": "LRU 캐시", "file": "lru.py", "test": test_lru,
     "claude": CLAUDE_LRU,
     "prompt": ("lru.py 파일을 만들어라. class LRUCache(capacity) 를 구현한다. get(key) 는 값을 "
                "돌려주고 없으면 None, put(key, value) 로 넣는다. 용량을 넘으면 가장 오래 안 쓴 "
                "항목을 버린다(get/put 모두 사용기록 갱신). 모든 연산 O(1), 표준 라이브러리만. 실행해 확인해라.")},
    {"name": "수식 파서(eval 금지)", "file": "parse_expr.py", "test": test_expr,
     "claude": CLAUDE_EXPR,
     "prompt": ("parse_expr.py 파일을 만들어라. 함수 evaluate(expr) 는 문자열 사칙연산식(+,-,*,/ 와 "
                "괄호)을 eval 없이 직접 파싱해 계산하고 float 로 돌려준다. 연산자 우선순위와 괄호를 "
                "정확히 지켜야 한다. 예: evaluate('2+3*4')==14. 실행해 확인해라.")},
]


def run_comet(task):
    d = tempfile.mkdtemp(prefix="cmp_comet_")
    box = dev_tools.ToolBox(root=d, auto_approve=True)
    steps = {"n": 0}
    def ev(k, da):
        if k == "step": steps["n"] = da["n"]
    t = time.time()
    try:
        agent.run(task["prompt"], box=box, max_steps=16, on_event=ev)
    except Exception:
        pass
    dt = time.time() - t
    total = task["test"](object())[1]      # object() 는 속성 없음 → (0, 총개수) 안전 반환
    fp = os.path.join(d, task["file"])
    if not os.path.isfile(fp):
        return 0, total, steps["n"], dt, 0, False
    try:
        m = _load(fp); p, tot = task["test"](m)
        loc = sum(1 for ln in open(fp, encoding="utf-8") if ln.strip())
        return p, tot, steps["n"], dt, loc, True
    except Exception:
        return 0, total, steps["n"], dt, 0, False


def run_claude(task):
    d = tempfile.mkdtemp(prefix="cmp_claude_")
    fp = os.path.join(d, task["file"])
    open(fp, "w", encoding="utf-8").write(task["claude"])
    m = _load(fp); p, tot = task["test"](m)
    loc = sum(1 for ln in task["claude"].splitlines() if ln.strip())
    return p, tot, loc


def main():
    import llm
    _, model = llm.active_backend()
    print(f"═══ 같은 프롬프트 비교 ═══  COMET={model}  vs  Claude(Opus 4.8)\n")
    rows = []
    for task in TASKS:
        print(f"▶ {task['name']}")
        cp, ctot, csteps, cdt, cloc, ok = run_comet(task)
        clp, cltot, clloc = run_claude(task)
        print(f"   COMET : {cp}/{ctot} 통과 · {csteps}걸음 {cdt:.0f}s · {cloc}줄"
              + ("" if ok else " · (파일 못만듦)"))
        print(f"   Claude: {clp}/{cltot} 통과 · {clloc}줄\n")
        rows.append((task["name"], cp, ctot, cdt, clp, cltot))
    print("═══ 요약 ═══")
    cs = sum(r[1] for r in rows); ct = sum(r[2] for r in rows)
    ls = sum(r[4] for r in rows); lt = sum(r[5] for r in rows)
    for n, cp, ctot, cdt, clp, cltot in rows:
        mark = "동점" if cp == clp else ("COMET↓" if cp < clp else "COMET↑")
        print(f"  {n:22s} COMET {cp}/{ctot}  Claude {clp}/{cltot}  [{mark}] {cdt:.0f}s")
    print(f"\n  총점  COMET {cs}/{ct}   Claude {ls}/{lt}")


if __name__ == "__main__":
    main()
