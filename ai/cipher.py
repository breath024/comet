# -*- coding: utf-8 -*-
"""고전 암호(아핀·시저·ROT13)를 모델 안 거치고 코드가 직접 푼다.

★왜 있나 — 2026-09-18 로그. "poten 을 k1=7 k2=2 로 아핀 암호" 를 물었더니
  gemma4:12b 가 fzhje → zwfep → dwfep 로 세 번 만에 겨우 맞췄다. 중간에
  "(15*7+2)%26 = 105%26" 이라고 쓴 줄까지 있다(107 이 맞다). 곱셈·나머지는
  모델이 틀릴 수 있어도 코드는 안 틀린다 — 이슈 알림의 "숫자는 파이썬이" 와 같은 원칙.

붙는 자리: comet.Comet.respond() 의 즉답 구간. command() 가 None 을 주면
그대로 평소 대화로 흘러간다("아핀 암호가 뭐야?" 같은 설명 질문은 모델이 답해야 한다).

말투 주의: 이 답은 소리로도 읽힌다(daemon 이 respond() 반환값을 그대로 TTS 로 넘긴다).
글자별 대응표는 백틱 안에 넣는다 — voice.clean_for_speech 가 인라인 코드를 통째로
지우므로 화면에만 남고 "피 열다섯 화살표 디" 로 읽히지 않는다.
"""
import re
import time

A = ord("a")
N = 26
FOLLOW_SEC = 900        # 직전 암호 turn 의 열쇠를 물려받는 시간 (열쇠 없이 "암호화해줘" 만 올 때)

# 마지막으로 성공한 풀이 — 호윤은 열쇠를 한 번만 말하고 다음 줄에선 안 말한다
#   (로그: "poten을 k1=7 k2=2 로 아핀" → 다음 턴 "poten을 암호화 해서 출력해주면 돼")
_last = {}

KIND = re.compile(r"아핀|affine|시저|씨저|시이저|카이사르|케사르|caesar|rot\s*-?\s*13|로트\s*13", re.I)
ENC = re.compile(r"암호화|암호로\s*(바꿔|만들|변환)|암호문으로|인코딩|encrypt|encode")
DEC = re.compile(r"복호화|복호|해독|디코딩|decrypt|decode|평문|원문|원래\s*(말|글|단어)")
# ★"풀어" 는 어느 쪽도 아니다 — "아핀 암호로 풀어줘" 는 한국어로 '계산해줘'에 가깝다.
#   2026-09-18 에 호윤이 "풀어저ㅜ" 라고 쓴 건 사실 암호화였다. 단정하지 말고 둘 다 준다.

# 대상 단어에서 걸러낼 것 — 암호 이름·연산 이름이 단어로 잡히면 엉뚱한 걸 암호화한다
SKIP = {"rot", "affine", "caesar", "mod", "mode", "comet", "key", "text", "word",
        "ai", "gpt", "encrypt", "decrypt", "encode", "decode", "cipher"}


# ═══════════════════════════════════════════════════════════════
#  계산
# ═══════════════════════════════════════════════════════════════
def _map(word, fn):
    """알파벳만 바꾸고 나머지(숫자·기호·한글)는 그대로. 대문자는 대문자로."""
    out = []
    for ch in word:
        if "a" <= ch.lower() <= "z" and ch.isascii():
            up = ch.isupper()
            got = chr(A + fn(ord(ch.lower()) - A))
            out.append(got.upper() if up else got)
        else:
            out.append(ch)
    return "".join(out)


def _inv(k1):
    """26 을 법으로 하는 역원. 서로소가 아니면 None (그때는 복호화 자체가 불가능)."""
    for i in range(1, N):
        if k1 * i % N == 1:
            return i
    return None


def affine_enc(word, k1, k2):
    return _map(word, lambda x: (k1 * x + k2) % N)


def affine_dec(word, k1, k2):
    inv = _inv(k1)
    if inv is None:
        return None
    return _map(word, lambda y: inv * (y - k2) % N)


def caesar(word, n):
    return _map(word, lambda x: (x + n) % N)


def _eul(word):
    """영어 단어 뒤의 을/를 — 소리로 읽히니 'poten 를' 은 어색하다. 끝 글자 모음이면 '를'."""
    tail = re.sub(r"[^A-Za-z]", "", word)[-1:].lower()
    return "를" if tail in "aeiouy" else "을"


def trace(word, fn):
    """p(15)→d  o(14)→w … — 화면용 대응표."""
    out = []
    for ch in word:
        if "a" <= ch.lower() <= "z" and ch.isascii():
            x = ord(ch.lower()) - A
            out.append(f"{ch}({x})→{_map(ch, fn)}")
    return "  ".join(out)


# ═══════════════════════════════════════════════════════════════
#  말에서 뽑아내기
# ═══════════════════════════════════════════════════════════════
def _kind(t):
    m = KIND.search(t)
    if not m:
        return None
    s = m.group(0).lower().replace(" ", "").replace("-", "")
    if s in ("rot13", "로트13"):
        return "rot13"
    if s in ("아핀", "affine"):
        return "affine"
    return "caesar"


def _keys(t):
    """k1/k2 (a/b 표기도) 를 뽑는다. 이름이 없으면 남은 정수들로 메운다."""
    def one(names):
        for nm in names:
            m = re.search(nm + r"\s*[=:]\s*(-?\d+)", t, re.I)
            if m:
                return int(m.group(1)), m.span()
        return None, None

    k1, s1 = one([r"k\s*_?\s*1", r"\ba\b"])
    k2, s2 = one([r"k\s*_?\s*2", r"\bb\b"])
    if k1 is not None and k2 is not None:
        return k1, k2, [s1, s2]

    # 이름 없이 숫자만 던진 경우 — 이름으로 이미 먹은 자리는 빼고 센다
    spans = [s for s in (s1, s2) if s]
    nums = [m for m in re.finditer(r"-?\d+", t)
            if not any(s[0] <= m.start() < s[1] for s in spans)]
    rest = [int(m.group()) for m in nums]
    spans += [m.span() for m in nums]
    if k1 is None and k2 is None and len(rest) >= 2:
        return rest[0], rest[1], spans
    if k1 is None and len(rest) >= 1:
        return rest[0], k2, spans
    if k2 is None and len(rest) >= 1:
        return k1, rest[0], spans
    return k1, k2, spans


def _target(t, spans):
    """암호화할 대상. 열쇠 숫자가 있던 자리와 암호 이름은 지우고 찾는다.

    ★자리를 유지하려고 지울 때 같은 길이의 공백으로 바꾼다 — 짧게 지우면
      뒤따르는 열쇠 span 이 밀려 엉뚱한 글자를 가린다.
    ★"Hello World!" 처럼 띄어쓴 것도 한 덩어리로 본다. 한글이 끼면 거기서 끊긴다
      ("poten을 암호화" → poten 까지).
    """
    s = list(KIND.sub(lambda m: " " * len(m.group(0)), t))
    for a, b in spans:
        for i in range(a, min(b, len(s))):
            s[i] = " "
    s = "".join(s)
    for m in re.finditer(r"[A-Za-z][A-Za-z'\-]*(?:[ ,.!?]+[A-Za-z][A-Za-z'\-]*)*[!?.]?", s):
        toks = re.split(r"([ ,.!?]+)", m.group(0))   # [단어, 구분자, 단어, …]
        letters = lambda w: re.sub(r"[^A-Za-z]", "", w).lower()
        while toks and letters(toks[0]) in SKIP:     # 양끝의 지시어만 떼고 가운데는 대상으로 본다
            toks = toks[2:]
        while toks and letters(toks[-1]) in SKIP:
            toks = toks[:-2]
        w = "".join(toks).strip(" ,'\"‘’“”")
        if len(letters(w)) >= 2:
            return w
    return None


# ═══════════════════════════════════════════════════════════════
#  창구
# ═══════════════════════════════════════════════════════════════
def command(text):
    """암호 계산이면 답 문자열, 아니면 None(= 평소 대화로 흘림)."""
    t = (text or "").strip()
    if not t:
        return None

    kind = _kind(t)
    enc, dec = bool(ENC.search(t)), bool(DEC.search(t))
    fresh = (time.time() - _last.get("at", 0)) < FOLLOW_SEC

    if kind is None:
        # 암호 이름을 다시 안 말한 되물음 — 방금 그 암호를 이어서 쓴다
        if not (fresh and (enc or dec)):
            return None
        kind = _last["kind"]

    k1, k2, spans = _keys(t)
    if kind == "rot13":
        k1, k2, spans = 1, 13, spans
    elif fresh and _last.get("kind") == kind:
        if k1 is None:
            k1 = _last.get("k1")
        if k2 is None:
            k2 = _last.get("k2")

    word = _target(t, spans)
    if not word:
        # "아핀 암호가 뭐야" 같은 설명 질문 — 모델이 답할 일이다
        if fresh and _last.get("word") and (enc or dec):
            word = _last["word"]
        else:
            return None

    if kind == "affine":
        if k1 is None or k2 is None:
            return None                      # 열쇠를 모르면 모델이 되묻게 둔다
        if _inv(k1) is None:
            good = ", ".join(str(i) for i in range(1, N) if _inv(i) is not None)
            return (f"k1이 {k1}이면 아핀 암호가 성립 안 해. k1은 26이랑 서로소여야 하거든 "
                    f"— 쓸 수 있는 건 {good} 뿐이야.")
        name, note = f"아핀 암호(k1은 {k1}, k2는 {k2})", ""
        E = lambda x: (k1 * x + k2) % N
        iv = _inv(k1)
        D = lambda y: iv * (y - k2) % N
    else:
        n = (k2 if kind == "rot13" else (k1 if k1 is not None else 3)) % N
        name, note = ("ROT13", "") if kind == "rot13" else (f"시저 암호({n}칸)", "")
        if kind == "caesar" and k1 is None:
            note = " 칸 수를 안 말해서 기본 3칸으로 했어."
        E = lambda x: (x + n) % N
        D = lambda y: (y - n) % N

    _last.update(kind=kind, k1=k1, k2=k2, word=word, at=time.time())

    head = f"{word}{_eul(word)} {name} 로"
    if kind == "rot13" or (enc == dec):      # ROT13 은 양방향이 같다 / 방향을 안 밝혔으면 둘 다
        a, b = _map(word, E), _map(word, D)
        if a == b:
            return f"{head} 돌리면 {a} 야.{note}\n`{trace(word, E)}`"
        return (f"{head} 돌리면 — 암호화는 {a}, 복호화는 {b} 야.{note} "
                f"어느 쪽인지 말해주면 하나만 줄게.\n"
                f"`암호화  {trace(word, E)}`\n"
                f"`복호화  {trace(word, D)}`")
    fn, verb = (E, "암호화") if enc else (D, "복호화")
    return f"{head} {verb}하면 {_map(word, fn)} 야.{note}\n`{trace(word, fn)}`"


if __name__ == "__main__":
    import sys
    print(command(" ".join(sys.argv[1:])) or "(암호 계산 아님 — 모델로 넘어간다)")
