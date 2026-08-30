# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 앱 아이콘 생성 (PWA용) — 한 번 실행해서 PNG를 굽는다.
#   혜성(comet) 모티프 + 데몬 UI와 같은 다크 배경(#0e0f12).
#   결과: web/icon-180.png(apple) / icon-192 / icon-512 / icon-512-maskable
#   데몬은 이 PNG들을 정적으로 서빙만 함(PIL 의존성은 빌드 단계에만).
# ═══════════════════════════════════════════════════════════════
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
os.makedirs(OUT, exist_ok=True)

BG_TOP = (21, 23, 28)      # #15171c
BG_BOT = (14, 15, 18)      # #0e0f12  (데몬 theme_color와 동일)
BLUE   = (42, 109, 244)    # #2a6df4  (전송 버튼 색)
WHITE  = (236, 240, 255)

S = 1024                   # 고해상도로 그린 뒤 축소


def _bg(pad_frac=0.0):
    """라운드 배경 + 세로 그라데이션 + 코너 블루 글로우."""
    img = Image.new("RGB", (S, S), BG_BOT)
    px = img.load()
    for y in range(S):
        t = y / (S - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        for x in range(S):
            px[x, y] = (r, g, b)
    return img.convert("RGBA")


def _comet(layer_size):
    """혜성: 우상단 머리 → 좌하단 꼬리. 글로우 레이어 + 코어 레이어."""
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)

    hx, hy = int(S * 0.66), int(S * 0.34)     # 머리
    tx, ty = int(S * 0.24), int(S * 0.74)      # 꼬리 끝
    steps = 80
    head_r = int(S * 0.085)
    for i in range(steps):
        f = i / (steps - 1)                    # 0=머리, 1=꼬리
        x = int(hx + (tx - hx) * f)
        y = int(hy + (ty - hy) * f)
        r = max(1, int(head_r * (1 - f) ** 1.6))
        a_glow = int(120 * (1 - f) ** 1.3)
        gd.ellipse([x - r * 2, y - r * 2, x + r * 2, y + r * 2],
                   fill=(BLUE[0], BLUE[1], BLUE[2], a_glow))
        a_core = int(230 * (1 - f) ** 1.2)
        cr = max(1, int(r * 0.6))
        cd.ellipse([x - cr, y - cr, x + cr, y + cr],
                   fill=(WHITE[0], WHITE[1], WHITE[2], a_core))

    # 밝은 머리 + 블루 헤일로
    gd.ellipse([hx - head_r * 2, hy - head_r * 2, hx + head_r * 2, hy + head_r * 2],
               fill=(BLUE[0], BLUE[1], BLUE[2], 150))
    cd.ellipse([hx - head_r, hy - head_r, hx + head_r, hy + head_r], fill=WHITE + (255,))

    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.045))
    core = core.filter(ImageFilter.GaussianBlur(S * 0.006))

    # 반짝임(4각 별) 두 개
    spk = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(spk)
    for (sx, sy, ss) in [(S * 0.30, S * 0.30, S * 0.045), (S * 0.78, S * 0.62, S * 0.028)]:
        sx, sy = int(sx), int(sy)
        ss = int(ss)
        sd.polygon([(sx, sy - ss), (sx + ss * 0.18, sy - ss * 0.18),
                    (sx + ss, sy), (sx + ss * 0.18, sy + ss * 0.18),
                    (sx, sy + ss), (sx - ss * 0.18, sy + ss * 0.18),
                    (sx - ss, sy), (sx - ss * 0.18, sy - ss * 0.18)], fill=WHITE + (235,))
    spk = spk.filter(ImageFilter.GaussianBlur(S * 0.002))

    return glow, core, spk


def _round_mask(radius_frac):
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    rad = int(S * radius_frac)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=255)
    return m


def build(maskable=False):
    bg = _bg()
    glow, core, spk = _comet(S)
    if maskable:
        # 마스커블: 콘텐츠를 가운데 80%로 축소해 원형 크롭에도 안 잘리게
        canvas = bg.copy()
        for layer in (glow, core, spk):
            small = layer.resize((int(S * 0.8), int(S * 0.8)), Image.LANCZOS)
            canvas.alpha_composite(small, (int(S * 0.1), int(S * 0.1)))
        return canvas.convert("RGB")
    out = bg
    for layer in (glow, core, spk):
        out = Image.alpha_composite(out, layer)
    # 라운드 코너(apple/안드로이드 일반 아이콘)
    mask = _round_mask(0.22)
    rounded = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rounded.paste(out, (0, 0), mask)
    return rounded


def save(img, name, size):
    img.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, name))
    print("  →", name, f"{size}x{size}")


def main():
    normal = build(maskable=False)
    save(normal, "icon-180.png", 180)
    save(normal, "icon-192.png", 192)
    save(normal, "icon-512.png", 512)
    maskable = build(maskable=True)
    save(maskable, "icon-512-maskable.png", 512)
    print("완료:", OUT)


if __name__ == "__main__":
    main()
