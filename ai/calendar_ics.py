# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 구글 캘린더 — 비공개 ICS 를 직접 읽는다 (키 발급 없음·읽기전용·한 방향)
#
#   · 주소 = 캘린더 설정 > 내 캘린더 설정 > 캘린더 통합 > '비공개 주소(iCal 형식)'.
#     ★주소 하나 = 캘린더 하나다. 계정이 여럿이거나 부 캘린더를 쓰면 주소도 그만큼 넣어야 한다
#     (`ics_urls` 배열). 안 넣은 캘린더의 일정은 그냥 안 보인다 — '일정 없음'처럼 보여서 헷갈린다.
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
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from dateutil import tz
from dateutil.rrule import rrulestr

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_DIR, "calendar_config.json")
LOCAL = tz.gettz("Asia/Seoul") or tz.tzlocal()
CACHE_SEC = 300           # 같은 ICS 를 5분 안에 또 부르지 않는다(구글도 자주 치면 싫어한다)
TIMEOUT = 20

_cache = {}               # 주소 → {"at": 받은시각, "text": ICS 원문}


# ── 설정 ────────────────────────────────────────────────────────
def _cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cfg):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _cache.clear()


def urls():
    """등록된 주소 전부. ★캘린더 하나가 아니다 — 계정마다·부 캘린더마다 주소가 따로다.
       (2026-09-18: 주 캘린더만 읽다가 딴 캘린더에 넣은 일정을 통째로 못 봤다.)
       예전 설정(ics_url 하나)도 그대로 먹게 둔다."""
    cfg = _cfg()
    out = []
    for u in ([cfg.get("ics_url")] + list(cfg.get("ics_urls") or [])):
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)
    return out


def configured():
    return bool(urls())


def _check(url):
    """넣어도 되는 주소인지. 문제 있으면 사람 말로 된 이유, 괜찮으면 빈 문자열."""
    if not url.startswith("https://calendar.google.com/calendar/ical/"):
        return "구글 캘린더 iCal 주소가 아니다. 설정 > 캘린더 통합 > '비공개 주소(iCal 형식)' 를 복사해라."
    if "/public/" in url:
        return ("공개 주소다. 그건 캘린더를 공개로 바꿔야 열리고, 그러면 URL 아는 누구나 일정을 본다. "
                "같은 칸의 🔒 '비공개 주소(iCal 형식)' 를 써라.")
    return ""


def add_url(url):
    """비공개 ICS 주소 하나 추가(기존 건 안 지운다). 공개 주소는 막는다."""
    url = (url or "").strip()
    bad = _check(url)
    if bad:
        return bad
    have = urls()
    if url in have:
        return f"이미 들어 있는 주소다(현재 {len(have)}개)."
    cfg = _cfg()
    cfg["ics_urls"] = have[1:] + [url] if cfg.get("ics_url") else have + [url]
    _save(cfg)
    return f"캘린더 추가했다(총 {len(urls())}개). '일정' 이라고 쳐봐라."


def set_url(url):
    """주소를 이것 하나로 갈아치운다."""
    url = (url or "").strip()
    bad = _check(url)
    if bad:
        return bad
    _save({"ics_url": url})
    return "캘린더 주소 저장했다. '일정' 이라고 쳐봐라."


def forget_url(needle):
    """주소 일부(계정 이름 등)로 지운다."""
    needle = (needle or "").strip()
    keep = [u for u in urls() if needle and needle not in u]
    if len(keep) == len(urls()):
        return "그 조각이 든 주소가 없다."
    _save({"ics_urls": keep})
    return f"지웠다(남은 {len(keep)}개)."


def names():
    """주소 전체를 보여주지 않고 캘린더 이름만. ★주소는 그 자체가 비밀이라 찍지 않는다."""
    out = []
    for i, u in enumerate(urls(), 1):
        m = re.search(r"/ical/([^/]+)/", u)
        who = urllib.parse.unquote(m.group(1)) if m else "?"
        out.append(f"{i}. {who}")
    return out


# ── 가져오기 ────────────────────────────────────────────────────
def _fetch_one(url, force=False):
    """주소 하나의 ICS 원문. 실패는 예외로 올린다(조용히 0건이 되면 죽은 걸 모른다)."""
    hit = _cache.get(url)
    if not force and hit and time.time() - hit["at"] < CACHE_SEC:
        return hit["text"]
    req = urllib.request.Request(url, headers={"User-Agent": "COMET/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        text = r.read().decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        raise RuntimeError("ICS 가 아니다 — 주소를 다시 확인해라(로그인 페이지가 왔을 수 있다).")
    _cache[url] = {"at": time.time(), "text": text}
    return text


def fetch(force=False):
    """첫 캘린더의 ICS 원문(한 개만 쓰던 시절 호출부 호환)."""
    got = urls()
    if not got:
        raise RuntimeError("캘린더 주소 미설정 — '캘린더 주소 <ICS URL>' 로 넣어라.")
    return _fetch_one(got[0], force)


def fetch_all(force=False):
    """등록된 캘린더 전부 → [{"name":…, "text":…, "error":…}].
       ★하나가 죽어도 나머지는 보여준다 — 다 같이 죽으면 그날 일정이 통째로 사라진다."""
    got = urls()
    if not got:
        raise RuntimeError("캘린더 주소 미설정 — '캘린더 주소 <ICS URL>' 로 넣어라.")
    out = []
    for url in got:
        m = re.search(r"/ical/([^/]+)/", url)
        who = urllib.parse.unquote(m.group(1)) if m else "캘린더"
        try:
            out.append({"name": who, "text": _fetch_one(url, force), "error": ""})
        except Exception as e:
            out.append({"name": who, "text": "", "error": str(e)[:120]})
    return out


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


def _calname(text, fallback):
    m = re.search(r"^X-WR-CALNAME:(.+)$", text, re.M)
    return _unescape(m.group(1).strip()) if m else fallback


# 마지막 events() 때 못 읽은 캘린더들. digest 가 이걸 보고 '조용한 0건'을 구분한다.
last_errors = []


def events(days=7, now=None, force=False):
    """지금부터 days 일 안의 일정 목록(시간순). 등록된 캘린더를 전부 합친다.
       각 항목: start·summary·all_day·location·cal."""
    now = now or datetime.now(LOCAL)
    lo = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hi = lo + timedelta(days=days + 1)

    out, seen = [], set()
    del last_errors[:]

    def add(uid, when, src, cal):
        # ★같은 회차를 두 번 넣지 않는다 — 옮겨진 회차는 '반복 펼치기'와 '대체본' 양쪽에서
        #   들어올 수 있다(검증에서 앞당긴 미팅이 두 줄로 나왔다).
        #   캘린더가 여럿이면 같은 약속이 두 캘린더에 걸쳐 있을 수도 있어 uid 로 한 번 더 걸러진다.
        key = (uid, when.replace(microsecond=0).isoformat())
        if key in seen:
            return
        seen.add(key)
        out.append({"start": when, "summary": src.get("summary") or "(제목 없음)",
                    "all_day": src.get("all_day", False),
                    "location": src.get("location", ""), "cal": cal})

    for feed in fetch_all(force=force):
        if feed["error"]:
            last_errors.append(f"{feed['name']}: {feed['error']}")
            continue
        cal = _calname(feed["text"], feed["name"])
        raw = parse(feed["text"])

        # 반복 중 한 번만 고친 것(RECURRENCE-ID)은 그 회차를 대체한다
        overrides = {}
        for ev in raw:
            if ev.get("recurrence_id") and ev.get("uid"):
                overrides[(ev["uid"], ev["recurrence_id"].replace(microsecond=0))] = ev

        for ev in raw:
            if ev.get("status") == "CANCELLED" or ev.get("recurrence_id"):
                continue
            for when in _occurrences(ev, lo, hi):
                use = overrides.get((ev.get("uid"), when.replace(microsecond=0)), ev)
                if use.get("status") == "CANCELLED":
                    continue
                add(ev.get("uid"), use["start"] if use is not ev else when, use, cal)

        # 창 밖 회차를 창 안으로 옮긴 대체본은 위에서 안 잡힌다 — 여기서 줍는다(중복은 add 가 막음)
        for ev in raw:
            if ev.get("recurrence_id") and ev.get("status") != "CANCELLED" and lo <= ev["start"] <= hi:
                add(ev.get("uid"), ev["start"], ev, cal)

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


def digest(days=7, now=None, force=False):
    """'일정' 화면 한 덩어리. 없으면 빈 문자열(빈 알림을 안 만들게).
       force=True 면 5분 캐시를 건너뛴다('새로고침' 하고 되물을 때)."""
    try:
        evs = events(days=days, now=now, force=force)
    except Exception as e:
        return f"[캘린더] 못 읽음: {e}"
    warn = ("\n⚠ 못 읽은 캘린더 — " + " / ".join(last_errors)) if last_errors else ""
    if not evs:
        # 못 읽은 게 있으면 '없다'고 하면 안 된다 — 진짜 없는 건지 못 본 건지 구분해줘야 한다
        return warn.strip()
    today = (now or datetime.now(LOCAL)).date()
    lines = [f"📅 캘린더 {len(evs)}건 (이레 안)" if days == 7 else f"📅 캘린더 {len(evs)}건"]
    many = len({e.get("cal") for e in evs}) > 1      # 캘린더가 둘 이상 섞였을 때만 출처를 붙인다
    for e in evs:
        loc = f"  @{e['location']}" if e.get("location") else ""
        src = f"  〔{e['cal']}〕" if many and e.get("cal") else ""
        lines.append(f"- {_when(e, today)}  {e['summary']}{loc}{src}")
    return "\n".join(lines) + warn


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
    if "--add" in sys.argv:                       # python calendar_ics.py --add <ICS URL>
        print(add_url(sys.argv[sys.argv.index("--add") + 1]))
        raise SystemExit
    if "--list" in sys.argv:
        print("\n".join(names()) or "등록된 캘린더 없음")
        raise SystemExit
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    print(f"[캘린더 {len(urls())}개] " + " · ".join(names()))
    print(digest(days=days) or "일정 없음")
