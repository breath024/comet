# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 구글 캘린더 — 비공개 ICS 를 직접 읽는다 (키 발급 없음·읽기전용·한 방향)
#
#   · 주소 = 캘린더 설정 > 내 캘린더 설정 > 캘린더 통합 > '비공개 주소(iCal 형식)'.
#     `calendar_config.json` 에 넣는다. ★이 파일은 공유 금지 — 주소만 알면 일정이 다 보인다
#     (.gitignore 에 올려뒀다). 유출되면 캘린더 설정에서 '주소 재설정'.
#   · ★공개 주소(/public/basic.ics)는 캘린더를 공개로 바꿔야 열린다(안 바꾸면 404, 실측).
#     공개로 바꾸면 URL 아는 누구나 본다 — 하지 말 것.
#   · icalendar 모듈이 없어서 직접 파싱한다(있는 건 dateutil 뿐). 반복 규칙만 dateutil.rrule.
#   · 구글 할 일(gtasks.py)과 다른 창구다 — 거긴 날짜 마감만, 여기는 '시각 있는 약속'이 온다.
#
#   확인:  python calendar_ics.py            (이레 안 일정)
#          python calendar_ics.py --days 30
# ═══════════════════════════════════════════════════════════════
import json
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

from dateutil import tz
from dateutil.rrule import rrulestr

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_DIR, "calendar_config.json")
LOCAL = tz.gettz("Asia/Seoul") or tz.tzlocal()
CACHE_SEC = 300           # 같은 ICS 를 5분 안에 또 부르지 않는다(구글도 자주 치면 싫어한다)
TIMEOUT = 20

_cache = {"at": 0.0, "text": ""}


# ── 설정 ────────────────────────────────────────────────────────
def _cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def configured():
    return bool(_cfg().get("ics_url"))


def set_url(url):
    """비공개 ICS 주소 저장. 공개 주소를 넣으면 막는다(캘린더를 공개로 바꾸게 되는 길)."""
    url = (url or "").strip()
    if not url.startswith("https://calendar.google.com/calendar/ical/"):
        return "구글 캘린더 iCal 주소가 아니다. 설정 > 캘린더 통합 > '비공개 주소(iCal 형식)' 를 복사해라."
    if "/public/" in url:
        return ("공개 주소다. 그건 캘린더를 공개로 바꿔야 열리고, 그러면 URL 아는 누구나 일정을 본다. "
                "같은 칸의 🔒 '비공개 주소(iCal 형식)' 를 써라.")
    cfg = _cfg()
    cfg["ics_url"] = url
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _cache["at"] = 0.0
    return "캘린더 주소 저장했다. '일정' 이라고 쳐봐라."


# ── 가져오기 ────────────────────────────────────────────────────
def fetch(force=False):
    """ICS 원문. 실패는 예외로 올린다(조용히 0건이 되면 죽은 걸 모른다)."""
    url = _cfg().get("ics_url")
    if not url:
        raise RuntimeError("캘린더 주소 미설정 — '캘린더 주소 <ICS URL>' 로 넣어라.")
    if not force and _cache["text"] and time.time() - _cache["at"] < CACHE_SEC:
        return _cache["text"]
    req = urllib.request.Request(url, headers={"User-Agent": "COMET/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        text = r.read().decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        raise RuntimeError("ICS 가 아니다 — 주소를 다시 확인해라(로그인 페이지가 왔을 수 있다).")
    _cache.update({"at": time.time(), "text": text})
    return text


# ── 파싱 ────────────────────────────────────────────────────────
def _unfold(text):
    """RFC5545 접힌 줄(다음 줄이 공백·탭으로 시작) 펴기."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v):
    return (v.replace("\\n", "\n").replace("\\N", "\n")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _split(line):
    """'DTSTART;TZID=Asia/Seoul:20260918T140000' → ('DTSTART', {'TZID':…}, '2026…')"""
    i = line.find(":")
    if i < 0:
        return "", {}, ""
    head, value = line[:i], line[i + 1:]
    bits = head.split(";")
    params = {}
    for p in bits[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return bits[0].upper(), params, value


def _to_dt(value, params):
    """ICS 날짜/시각 → (datetime[로컬], 종일여부). 종일은 그날 00:00 으로 둔다."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=LOCAL), True
    if value.endswith("Z"):
        d = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=tz.UTC)
        return d.astimezone(LOCAL), False
    d = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    zone = tz.gettz(params.get("TZID", "")) or LOCAL
    return d.replace(tzinfo=zone).astimezone(LOCAL), False


def parse(text):
    """VEVENT 들 → dict 목록(아직 반복은 안 펼친 상태)."""
    events, cur = [], None
    for line in _unfold(text):
        u = line.strip()
        if u == "BEGIN:VEVENT":
            cur = {"exdate": [], "rrule": "", "status": "", "recurrence_id": None}
            continue
        if u == "END:VEVENT":
            if cur and cur.get("start"):
                events.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        name, params, value = _split(line)
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _to_dt(value, params)
        elif name == "DTEND":
            cur["end"], _ = _to_dt(value, params)
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value).strip()
        elif name == "LOCATION":
            cur["location"] = _unescape(value).strip()
        elif name == "RRULE":
            cur["rrule"] = value.strip()
        elif name == "EXDATE":
            for v in value.split(","):
                try:
                    cur["exdate"].append(_to_dt(v, params)[0])
                except Exception:
                    pass
        elif name == "STATUS":
            cur["status"] = value.strip().upper()
        elif name == "RECURRENCE-ID":          # 반복 중 한 번만 바꾼 것
            try:
                cur["recurrence_id"] = _to_dt(value, params)[0]
            except Exception:
                pass
        elif name == "UID":
            cur["uid"] = value.strip()
    return events


def _occurrences(ev, lo, hi):
    """한 이벤트가 [lo, hi] 안에서 언제 도는지 — 반복이면 펼친다."""
    start = ev["start"]
    if not ev.get("rrule"):
        return [start] if lo <= start <= hi else []
    try:
        rule = rrulestr(ev["rrule"], dtstart=start)
    except Exception:
        return [start] if lo <= start <= hi else []
    ex = {d.replace(microsecond=0) for d in ev.get("exdate", [])}
    return [d for d in rule.between(lo, hi, inc=True) if d.replace(microsecond=0) not in ex]


def events(days=7, now=None, force=False):
    """지금부터 days 일 안의 일정 목록(시간순). 각 항목: start·summary·all_day·location."""
    now = now or datetime.now(LOCAL)
    lo = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hi = lo + timedelta(days=days + 1)
    raw = parse(fetch(force=force))

    # 반복 중 한 번만 고친 것(RECURRENCE-ID)은 그 회차를 대체한다
    overrides = {}
    for ev in raw:
        if ev.get("recurrence_id") and ev.get("uid"):
            overrides[(ev["uid"], ev["recurrence_id"].replace(microsecond=0))] = ev

    out, seen = [], set()

    def add(uid, when, src):
        # ★같은 회차를 두 번 넣지 않는다 — 옮겨진 회차는 '반복 펼치기'와 '대체본' 양쪽에서
        #   들어올 수 있다(검증에서 앞당긴 미팅이 두 줄로 나왔다).
        key = (uid, when.replace(microsecond=0).isoformat())
        if key in seen:
            return
        seen.add(key)
        out.append({"start": when, "summary": src.get("summary") or "(제목 없음)",
                    "all_day": src.get("all_day", False),
                    "location": src.get("location", "")})

    for ev in raw:
        if ev.get("status") == "CANCELLED" or ev.get("recurrence_id"):
            continue
        for when in _occurrences(ev, lo, hi):
            use = overrides.get((ev.get("uid"), when.replace(microsecond=0)), ev)
            if use.get("status") == "CANCELLED":
                continue
            add(ev.get("uid"), use["start"] if use is not ev else when, use)

    # 창 밖 회차를 창 안으로 옮긴 대체본은 위에서 안 잡힌다 — 여기서 줍는다(중복은 add 가 막음)
    for ev in raw:
        if ev.get("recurrence_id") and ev.get("status") != "CANCELLED" and lo <= ev["start"] <= hi:
            add(ev.get("uid"), ev["start"], ev)

    out.sort(key=lambda e: e["start"])
    return out


# ── 보여주기 (todo_box 화면과 같은 결) ──────────────────────────
_WD = "월화수목금토일"


def _when(e, today):
    d = e["start"].date()
    if d == today:
        head = "오늘"
    elif d == today + timedelta(days=1):
        head = "내일"
    else:
        head = f"{d.month}/{d.day}({_WD[d.weekday()]})"
    return head if e["all_day"] else f"{head} {e['start']:%H:%M}"


def digest(days=7, now=None):
    """'일정' 화면 한 덩어리. 없으면 빈 문자열(빈 알림을 안 만들게)."""
    try:
        evs = events(days=days, now=now)
    except Exception as e:
        return f"[캘린더] 못 읽음: {e}"
    if not evs:
        return ""
    today = (now or datetime.now(LOCAL)).date()
    lines = [f"📅 캘린더 {len(evs)}건 (이레 안)" if days == 7 else f"📅 캘린더 {len(evs)}건"]
    for e in evs:
        loc = f"  @{e['location']}" if e.get("location") else ""
        lines.append(f"- {_when(e, today)}  {e['summary']}{loc}")
    return "\n".join(lines)


def speech(days=1, now=None):
    """아침에 읊어줄 한 줄(오늘·내일 것만, 짧게). 없으면 빈 문자열."""
    try:
        evs = events(days=days, now=now)
    except Exception:
        return ""
    if not evs:
        return ""
    today = (now or datetime.now(LOCAL)).date()
    bits = [f"{_when(e, today)} {e['summary']}" for e in evs[:4]]
    return "일정 " + ", ".join(bits) + " 있어."


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    print(digest(days=days) or "일정 없음")
