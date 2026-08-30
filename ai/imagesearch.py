# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 이미지 역검색 — "이 사진 뭐야 / 어디서 왔어"를 자비스처럼.
#  입력: 파일경로 · 클립보드 이미지 · PC 화면캡처 (폰은 데몬에서 경로로 넘김)
#
#  3단 폴백 — 위에서 막히면 아래로 떨어진다. 항상 '정직하게' 답한다:
#   A단 (키 있으면): SerpAPI/serper Google Lens → 진짜 원본 출처 URL을 JSON으로.
#   B단 (무키 최선시도): Yandex/Lens 업로드 스크래핑. 막히면 조용히 통과.
#   C단 (항상 됨): gemma4가 '무엇인지' 식별 → web.py 로 검색·출처추적.
#
#  설계 원칙: web.py/news.py 와 동일 — urllib 표준라이브러리만, 결과는 {ok, ...} dict.
#  원본을 못 찾으면 지어내지 않고 "식별만 됨 / 원본 못 찾음"이라 말한다.
# ═══════════════════════════════════════════════════════════════
import os
import io
import re
import json
import uuid
import tempfile
import urllib.parse
import urllib.request

import web   # 기존 검색·출처추적 엔진 재사용 (C단)

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_DIR, "imagesearch_config.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

VISION_MODEL = "gemma4:12b"      # 식별용 멀티모달 (vision.py 와 동일 계열)
MAX_WIDTH = 1024               # 업로드/식별용 축소 — 속도·전송량 절약


# ── 설정(키) 로드 — 없으면 빈 dict. 키 채우면 A단이 자동으로 켜진다 ──
def _load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ensure_config_stub():
    """키 자리 파일이 없으면 빈 템플릿을 만들어 둔다(호윤이 나중에 키만 채우게)."""
    if os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"serpapi_key": "", "serper_key": "",
                       "_note": "여기에 SerpAPI 또는 serper.dev 키를 넣으면 진짜 원본추적(A단)이 켜져."},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  0) 이미지 확보 — 경로 > 클립보드 > 화면
# ═══════════════════════════════════════════════════════════════
def grab_image(path=None):
    """역검색할 이미지를 확보해 (파일경로, is_temp) 반환.
       우선순위: 명시 경로 → 클립보드 이미지/복사된 파일 → PC 화면 캡처."""
    # 1) 명시 경로
    if path and os.path.exists(path):
        return path, False

    # 2) 클립보드 (이미지 자체 또는 복사된 파일 목록)
    try:
        from PIL import ImageGrab, Image
        clip = ImageGrab.grabclipboard()
        if isinstance(clip, Image.Image):
            fd, p = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            clip.save(p, "PNG")
            return p, True
        if isinstance(clip, list):
            for item in clip:
                if isinstance(item, str) and os.path.exists(item):
                    return item, False
    except Exception:
        pass

    # 3) 화면 캡처 (vision 과 동일 소스)
    try:
        import vision
        return vision.capture_screen(), True
    except Exception:
        return None, False


def _jpeg_bytes(path, max_width=MAX_WIDTH, quality=85):
    """이미지를 RGB JPEG 바이트로(축소). 업로드/전송용."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_width:
        im = im.resize((max_width, int(im.height * max_width / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _small_file(path):
    """식별용 축소 파일 임시 저장 → 경로 반환(호출자가 지움)."""
    data = _jpeg_bytes(path)
    fd, p = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ── 멀티파트 본문 직접 조립 (requests 없이 urllib 으로) ──
def _multipart(fields, files):
    boundary = "----COMET" + uuid.uuid4().hex
    parts = []
    for k, v in (fields or {}).items():
        parts.append(f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, content, ctype) in (files or {}).items():
        parts.append((f"--{boundary}\r\n"
                      f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                      f"Content-Type: {ctype}\r\n\r\n").encode())
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def _http(url, data=None, headers=None, timeout=30, method=None):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = "utf-8"
        ctype = r.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ctype, re.I)
        if m:
            enc = m.group(1)
        return r.getcode(), raw.decode(enc, "ignore"), r.geturl()


# ═══════════════════════════════════════════════════════════════
#  A단) 키 기반 진짜 역검색 (SerpAPI / serper) — 키 있을 때만
#       Lens 계열 API 는 이미지가 '공개 URL'이어야 함 → 로컬은 임시호스트 업로드.
# ═══════════════════════════════════════════════════════════════
def _upload_temp(jpeg):
    """로컬 이미지를 무인증 임시 호스트(0x0.st)에 올려 공개 URL 확보(A단 보조)."""
    body, ctype = _multipart(None, {"file": ("img.jpg", jpeg, "image/jpeg")})
    try:
        code, text, _ = _http("https://0x0.st", data=body,
                              headers={"Content-Type": ctype}, timeout=30)
        url = text.strip()
        return url if url.startswith("http") else None
    except Exception:
        return None


def reverse_keyed(jpeg, cfg):
    """SerpAPI(google_lens) 또는 serper(lens)로 진짜 원본 출처를 받는다.
       키 없으면 None. 로컬 이미지는 임시호스트에 올려 URL 화 한다."""
    serp = (cfg.get("serpapi_key") or "").strip()
    serper = (cfg.get("serper_key") or "").strip()
    if not serp and not serper:
        return None

    img_url = _upload_temp(jpeg)
    if not img_url:
        return {"ok": False, "engine": "keyed", "msg": "임시 업로드 실패 — 원본추적 URL 확보 못함."}

    # SerpAPI 우선
    if serp:
        try:
            q = urllib.parse.urlencode({"engine": "google_lens", "url": img_url,
                                        "api_key": serp, "hl": "ko"})
            code, text, _ = _http("https://serpapi.com/search.json?" + q, timeout=40)
            data = json.loads(text)
            matches = data.get("visual_matches", []) or data.get("image_results", [])
            srcs = [{"title": m.get("title", ""), "url": m.get("link", ""),
                     "source": m.get("source", "")} for m in matches if m.get("link")]
            if srcs:
                return {"ok": True, "engine": "SerpAPI Google Lens",
                        "img_url": img_url, "sources": srcs[:8]}
        except Exception as e:
            pass

    # serper.dev (lens)
    if serper:
        try:
            body = json.dumps({"url": img_url}).encode()
            code, text, _ = _http("https://google.serper.dev/lens", data=body,
                                  headers={"X-API-KEY": serper,
                                           "Content-Type": "application/json"},
                                  timeout=40, method="POST")
            data = json.loads(text)
            matches = data.get("organic", []) or data.get("visual_matches", [])
            srcs = [{"title": m.get("title", ""), "url": m.get("link", ""),
                     "source": m.get("source", "")} for m in matches if m.get("link")]
            if srcs:
                return {"ok": True, "engine": "serper Lens",
                        "img_url": img_url, "sources": srcs[:8]}
        except Exception:
            pass

    return {"ok": False, "engine": "keyed", "img_url": img_url,
            "msg": "키 API 가 매치를 못 줬어."}


# ═══════════════════════════════════════════════════════════════
#  B단) 무키 최선시도 — Yandex/Lens 업로드. 막히면 None(조용히 통과).
#       ※ 실측상 결과 회수는 불안정 → 되면 보너스, 안 되면 C단으로.
# ═══════════════════════════════════════════════════════════════
def reverse_nokey(jpeg):
    """무키 스크래핑으로 원본 후보 URL을 시도. 실패 시 None."""
    # Yandex: 업로드 → 결과 페이지에서 '이 이미지가 있는 사이트' 링크 파싱 시도
    try:
        body, ctype = _multipart(
            {"rpt": "imageview"},
            {"upfile": ("blob.jpg", jpeg, "image/jpeg")})
        code, text, final = _http(
            "https://yandex.com/images/search?rpt=imageview",
            data=body, headers={"Content-Type": ctype,
                                "Accept-Language": "en,ru;q=0.8"}, timeout=40)
        # 결과 HTML 안의 외부 사이트 링크(노이즈 제외)
        urls = re.findall(r'"url":"(https?:\\?/\\?/[^"]+?)"', text)
        clean = []
        for u in urls:
            u = u.replace("\\/", "/")
            d = urllib.parse.urlparse(u).netloc
            if d and "yandex" not in d and "yastatic" not in d and u not in clean:
                clean.append(u)
        if clean:
            return {"ok": True, "engine": "Yandex(무키)",
                    "sources": [{"title": "", "url": u, "source": urllib.parse.urlparse(u).netloc}
                                for u in clean[:8]]}
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  C단) 식별 → 검색 (항상 됨, 키 0). gemma4 가 '무엇인지' 보고 web.py 가 찾는다.
# ═══════════════════════════════════════════════════════════════
IDENTIFY_PROMPT = (
    "이 이미지를 보고 '무엇인지 식별'에 필요한 단서만 JSON으로 출력해라. 설명 문장 금지, JSON만.\n"
    "필드:\n"
    '  "subject": 이미지의 정체를 한 줄로(예: "파리 에펠탑", "아이폰 15 프로", "삼성 로고").\n'
    '  "kind": 다음 중 하나 — person/place/landmark/product/logo/brand/animal/plant/food/'
    'artwork/meme/screenshot/document/text/scene/other.\n'
    '  "text": 이미지에 보이는 글자 그대로(브랜드명·간판·자막 등, 없으면 "").\n'
    '  "features": 눈에 띄는 특징 2~4개 리스트(색·모양·배경·표식).\n'
    '  "queries": 이걸 웹에서 찾기 위한 한국어 검색문구 1~3개 리스트(가장 좋은 것 먼저, '
    "고유명사·브랜드·지명을 넣어 구체적으로).\n"
    "확실치 않으면 추측이라도 가장 그럴듯한 후보로 채우되, 모르면 빈 문자열/빈 리스트로."
)


def identify(path, model=None):
    """gemma4 멀티모달로 이미지 식별 → dict(subject/kind/text/features/queries)."""
    import ollama
    cli = ollama.Client(timeout=180)
    try:
        r = cli.chat(model=model or VISION_MODEL, keep_alive="5m",
                     format="json", options={"num_predict": 512},
                     messages=[{"role": "user", "content": IDENTIFY_PROMPT,
                                "images": [path]}])
        raw = (r.get("message", {}).get("content", "") or "").strip()
        info = json.loads(raw)
    except Exception as e:
        return {"subject": "", "kind": "other", "text": "",
                "features": [], "queries": [], "_err": str(e)}
    # 정규화
    info.setdefault("subject", "")
    info.setdefault("kind", "other")
    info.setdefault("text", "")
    if not isinstance(info.get("features"), list):
        info["features"] = []
    if not isinstance(info.get("queries"), list):
        info["queries"] = [info["queries"]] if info.get("queries") else []
    return info


def identify_and_search(path, hint=""):
    """C단 본체: 식별 → 검색어 구성 → web.research 로 출처추적."""
    info = identify(path)
    queries = [q for q in (info.get("queries") or []) if q]
    if info.get("subject"):
        queries.insert(0, info["subject"])
    if hint:
        queries.insert(0, hint)
    queries = list(dict.fromkeys(queries))   # 중복 제거, 순서 보존
    if not queries:
        return {"ok": True, "engine": "식별만", "identify": info,
                "research": None,
                "msg": "이미지에서 검색할 단서를 못 뽑았어. (원본 못 찾음)"}
    research = web.research(queries[0], n_sources=4)
    return {"ok": True, "engine": "식별→웹검색", "identify": info,
            "query": queries[0], "queries": queries, "research": research}


# ═══════════════════════════════════════════════════════════════
#  오케스트레이터 — A → B → C 폴백
# ═══════════════════════════════════════════════════════════════
def search(path=None, hint=""):
    """이미지 역검색 본체. path 없으면 클립보드/화면에서 자동 확보.
       반환 {ok, engine, ...} — 위에서부터 가능한 단을 쓴다."""
    _ensure_config_stub()
    img_path, is_temp = grab_image(path)
    if not img_path:
        return {"ok": False, "msg": "역검색할 이미지를 못 찾았어. (파일 경로를 주거나, 이미지를 복사한 뒤 다시 시도해줘.)"}

    small = None
    try:
        small = _small_file(img_path)
        jpeg = _jpeg_bytes(img_path)
        cfg = _load_config()

        # A단 — 키 있을 때만
        a = reverse_keyed(jpeg, cfg)
        if a and a.get("ok"):
            a["origin"] = "A(키 API)"
            return a

        # B단 — 무키 최선시도
        b = reverse_nokey(jpeg)
        if b and b.get("ok"):
            b["origin"] = "B(무키 스크래핑)"
            return b

        # C단 — 식별 → 검색 (항상)
        c = identify_and_search(small, hint=hint)
        c["origin"] = "C(식별→검색)"
        # A단이 키없음(None)이 아니라 '실패'였다면 그 메시지도 같이 알려줌
        if a and not a.get("ok") and a.get("msg"):
            c["keyed_note"] = a["msg"]
        return c
    finally:
        for p in (small, img_path if is_temp else None):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def dispatch(action, args):
    if action == "image_search":
        return search(path=args.get("path") or None, hint=args.get("hint", ""))
    return {"ok": False, "msg": f"알 수 없는 이미지 동작: {action}"}


if __name__ == "__main__":
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    print("== 이미지 역검색 ==  입력:", arg or "(클립보드/화면)")
    res = search(path=arg)
    print("ok:", res.get("ok"), "| engine:", res.get("engine"), "| origin:", res.get("origin"))
    if res.get("identify"):
        info = res["identify"]
        print("식별:", info.get("subject"), f"({info.get('kind')})",
              "| 글자:", info.get("text"), "| 특징:", info.get("features"))
    if res.get("sources"):
        for s in res["sources"]:
            print("  -", s.get("source"), s.get("url"))
    if res.get("research") and res["research"].get("ok"):
        for s in res["research"]["sources"]:
            print(f"  [{s['n']}] ({s['tier_label']}·{s['domain']}) {s['url']}")
    if res.get("msg"):
        print("msg:", res["msg"])
