# -*- coding: utf-8 -*-
"""Builds docs/hero.png, the banner at the top of the README.

The product shot is a real capture of the running application rather than
a mockup, which is the whole point: a hero built in a design tool shows
what somebody hoped the software looked like, and every detail that drifts
from the real thing is a small lie the reader finds out about thirty
seconds after installing. Everything claimed in the feature strip below it
is something the code actually does, with the measured number where there
is one.

Regenerate after a visual change:

    python docs\\make_hero.py            # uses docs/_shot.png
    python docs\\make_hero.py path.png   # or a fresh capture
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent

# Both languages, because the Russian README should not show an English
# interface: the product is bilingual and the banner is the first thing
# that either proves or undercuts that.
LANG = "ru" if "--ru" in sys.argv else "en"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SHOT = Path(args[0]) if args else HERE / ("_shot_ru.png" if LANG == "ru" else "_shot.png")
OUT = HERE / ("hero-ru.png" if LANG == "ru" else "hero.png")

COPY = {
    "en": {
        "pill": "ALL-IN-ONE AI DAW",
        "head": ("Your ideas.", "Real tracks.", "Cashout Studio."),
        "sub": [
            "A complete digital audio workstation with a native C++",
            "audio engine, and AI that runs on your own graphics card.",
            "Record, edit, mix, separate and master. No account, no",
            "uploads, nothing to cancel.",
        ],
        "cta": "Start Creating",
        "tags": ("Local", "Private", "Yours"),
        "features": [
            ("wave", "NATIVE ENGINE", ["C++ on a real-time thread.", "2.67 ms, zero dropouts."]),
            ("daw", "FULL DAW", ["Arrange, mix, VST3 plugins,", "record and export."]),
            ("brain", "AI CO-PRODUCER", ["Hears your mix and shows", "the measurement behind it."]),
            ("cut", "SEPARATION", ["Split a song into stems", "on your own GPU."]),
            ("lock", "RUNS LOCALLY", ["No account, no upload,", "no subscription."]),
        ],
    },
    "ru": {
        "pill": "ИИ-СТУДИЯ ЦЕЛИКОМ",
        "head": ("Ваши идеи.", "Готовые треки.", "Cashout Studio."),
        "sub": [
            "Полноценная студия с нативным аудиодвижком на C++",
            "и ИИ, который считает на вашей видеокарте. Запись,",
            "монтаж, сведение, разделение и мастеринг. Без аккаунта,",
            "без загрузок, без подписки.",
        ],
        "cta": "Начать",
        "tags": ("Локально", "Приватно"),
        "features": [
            ("wave", "НАТИВНЫЙ ДВИЖОК", ["C++ в реальном времени.", "2,67 мс, без пропусков."]),
            ("daw", "ПОЛНЫЙ DAW", ["Аранжировка, сведение,", "VST3, запись и экспорт."]),
            ("brain", "ИИ-СОПРОДЮСЕР", ["Слышит микс и показывает", "измерение под выводом."]),
            ("cut", "РАЗДЕЛЕНИЕ", ["Разбор песни на стемы", "на вашей видеокарте."]),
            ("lock", "ВСЁ ЛОКАЛЬНО", ["Без аккаунта, загрузок", "и подписки."]),
        ],
    },
}[LANG]

W, H = 1600, 900

# The application's own tokens. The banner and the product have to agree
# about what the brand looks like, and the only way to guarantee that is to
# read the same numbers.
BG = (7, 10, 15)
PANEL = (13, 19, 27)
ACCENT = (36, 225, 192)
ACCENT2 = (56, 189, 248)
TEXT = (230, 237, 243)
DIM = (130, 150, 171)
FAINT = (85, 105, 125)

F = "C:/Windows/Fonts/"
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(F + name, size)

BOLD, SEMI, REG, LIGHT, MONO = "segoeuib.ttf", "seguisb.ttf", "segoeui.ttf", "segoeuil.ttf", "consola.ttf"


def rounded(size, radius, fill):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=fill)
    return img


def glow(size, radius, colour, blur, alpha):
    layer = Image.new("RGBA", (size[0] + blur * 4, size[1] + blur * 4), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [blur * 2, blur * 2, blur * 2 + size[0], blur * 2 + size[1]],
        radius, fill=colour + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def tracking(draw, xy, text, fnt, fill, extra):
    """Letter-spaced text. Pillow has no tracking, and small caps without
    it read as cramped rather than as a legend."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + extra
    return x


# ------------------------------------------------------------ background
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# Lit from above and behind the product shot, the way the app's own
# surfaces are. One soft wash, not the two corner blooms of a landing page.
wash = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(wash).ellipse([W * 0.42, -H * 0.55, W * 1.25, H * 0.75],
                             fill=ACCENT + (30,))
img = Image.alpha_composite(img.convert("RGBA"), wash.filter(ImageFilter.GaussianBlur(160)))

# The grid floor from the intro animation, in perspective.
grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(grid)
horizon = int(H * 0.62)
for i in range(-14, 30):
    x = W * 0.5 + i * 62
    gd.line([(W * 0.5 + i * 12, horizon), (x, H)], fill=(60, 120, 140, 26), width=1)
step, y = 6, horizon
while y < H:
    gd.line([(0, y), (W, y)], fill=(60, 120, 140, 22), width=1)
    step = int(step * 1.42) + 1
    y += step
img = Image.alpha_composite(img, grid.filter(ImageFilter.GaussianBlur(0.4)))

d = ImageDraw.Draw(img)

# ------------------------------------------------------- the product shot
shot = Image.open(SHOT).convert("RGB")
target_w = 960
shot = shot.resize((target_w, round(shot.height * target_w / shot.width)), Image.LANCZOS)
sx, sy = W - target_w - 48, 96

img.alpha_composite(glow((shot.width, shot.height), 10, ACCENT, 46, 60),
                    (sx - 92, sy - 92))
mask = Image.new("L", shot.size, 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height - 1], 8, fill=255)
img.paste(shot, (sx, sy), mask)
d = ImageDraw.Draw(img)
d.rounded_rectangle([sx, sy, sx + shot.width - 1, sy + shot.height - 1], 8,
                    outline=(70, 110, 130), width=1)

# ---------------------------------------------------------- brand, top left
LEFT = 64
logo = Image.open(HERE.parent / "frontend/public/cashout-studio-logo.ico")
if getattr(logo, "n_frames", 1) > 1:
    best, area = logo, 0
    for i in range(logo.n_frames):
        logo.seek(i)
        if logo.width * logo.height > area:
            area, best = logo.width * logo.height, logo.copy()
    logo = best
logo = logo.convert("RGBA").resize((74, 74), Image.LANCZOS)
img.alpha_composite(glow((74, 74), 18, ACCENT, 22, 70), (LEFT - 44, 52 - 44))
img.alpha_composite(logo, (LEFT, 52))

tracking(d, (LEFT + 98, 58), "CASHOUT STUDIO", font(BOLD, 34), TEXT, 3.5)
tracking(d, (LEFT + 100, 100), "MADE BY CASHOUT PT.5", font(REG, 15), DIM, 4.2)

# ------------------------------------------------------------------- pill
py = 172
pill_w = 236
img.alpha_composite(rounded((pill_w, 38), 19, (18, 32, 40, 210)), (LEFT, py))
d.rounded_rectangle([LEFT, py, LEFT + pill_w, py + 38], 19, outline=(30, 90, 86), width=1)
d.ellipse([LEFT + 18, py + 16, LEFT + 25, py + 23], fill=ACCENT)
tracking(d, (LEFT + 36, py + 11), COPY["pill"], font(SEMI, 14), TEXT, 1.6)

# --------------------------------------------------------------- headline
hy = 232
big = font(BOLD, 66)
d.text((LEFT - 3, hy), COPY["head"][0], font=big, fill=TEXT)
d.text((LEFT - 3, hy + 78), COPY["head"][1], font=big, fill=ACCENT)
d.text((LEFT - 3, hy + 156), COPY["head"][2], font=big, fill=TEXT)

# ---------------------------------------------------------------- subcopy
sub = font(REG, 19)
lines = COPY["sub"]
for i, line in enumerate(lines):
    d.text((LEFT, 490 + i * 29), line, font=sub, fill=DIM)

# -------------------------------------------------------------------- CTA
cy = 618
cw, ch = 262, 58
img.alpha_composite(glow((cw, ch), 29, ACCENT, 26, 90), (LEFT - 52, cy - 52))
img.alpha_composite(rounded((cw, ch), 29, ACCENT + (255,)), (LEFT, cy))
d.text((LEFT + 66, cy + 16), COPY["cta"], font=font(BOLD, 21), fill=(4, 18, 26))
d.ellipse([LEFT + 14, cy + 15, LEFT + 42, cy + 43], outline=(4, 18, 26), width=2)
d.line([(LEFT + 22, cy + 29), (LEFT + 34, cy + 29)], fill=(4, 18, 26), width=2)
d.line([(LEFT + 29, cy + 24), (LEFT + 34, cy + 29)], fill=(4, 18, 26), width=2)
d.line([(LEFT + 29, cy + 34), (LEFT + 34, cy + 29)], fill=(4, 18, 26), width=2)

x = LEFT + cw + 22
for i, word in enumerate(COPY["tags"]):
    if i:
        d.text((x, cy + 19), "/", font=font(REG, 17), fill=FAINT)
        x += 20
    x += 0
    d.text((x, cy + 19), word, font=font(SEMI, 17), fill=DIM)
    x += d.textlength(word, font=font(SEMI, 17)) + 14

# --------------------------------------------------------- feature strip
strip_y = 722
d.line([(LEFT, strip_y - 28), (W - 48, strip_y - 28)], fill=(28, 44, 58), width=1)

FEATURES = COPY["features"]

col_w = (W - LEFT - 48) // len(FEATURES)
for i, (icon, title, body) in enumerate(FEATURES):
    cx = LEFT + i * col_w
    box = 52
    img.alpha_composite(rounded((box, box), 4, (16, 28, 36, 200)), (cx, strip_y))
    d.rounded_rectangle([cx, strip_y, cx + box, strip_y + box], 4,
                        outline=(30, 78, 78), width=1)
    ix, iy = cx + box // 2, strip_y + box // 2
    if icon == "wave":
        for k, hgt in enumerate((7, 13, 18, 11, 6)):
            bx = ix - 16 + k * 8
            d.line([(bx, iy - hgt), (bx, iy + hgt)], fill=ACCENT, width=2)
    elif icon == "daw":
        d.rounded_rectangle([ix - 15, iy - 11, ix + 15, iy + 11], 2, outline=ACCENT, width=2)
        d.line([(ix - 6, iy - 11), (ix - 6, iy + 11)], fill=ACCENT, width=2)
        d.line([(ix + 5, iy - 4), (ix + 5, iy + 11)], fill=ACCENT, width=2)
    elif icon == "brain":
        d.ellipse([ix - 14, iy - 14, ix + 14, iy + 14], outline=ACCENT, width=2)
        d.arc([ix - 8, iy - 9, ix + 8, iy + 3], 180, 360, fill=ACCENT, width=2)
        d.line([(ix, iy + 3), (ix, iy + 9)], fill=ACCENT, width=2)
    elif icon == "cut":
        d.line([(ix - 13, iy - 13), (ix + 11, iy + 11)], fill=ACCENT, width=2)
        d.line([(ix + 11, iy - 13), (ix - 13, iy + 11)], fill=ACCENT, width=2)
        d.ellipse([ix - 16, iy + 8, ix - 8, iy + 16], outline=ACCENT, width=2)
        d.ellipse([ix + 8, iy + 8, ix + 16, iy + 16], outline=ACCENT, width=2)
    else:
        d.rounded_rectangle([ix - 11, iy - 2, ix + 11, iy + 14], 2, outline=ACCENT, width=2)
        d.arc([ix - 7, iy - 14, ix + 7, iy + 2], 180, 360, fill=ACCENT, width=2)

    tracking(d, (cx + box + 16, strip_y + 4), title, font(BOLD, 14), ACCENT, 1.1)
    for k, line in enumerate(body):
        d.text((cx + box + 16, strip_y + 25 + k * 18), line, font=font(REG, 14), fill=DIM)

# --------------------------------------------------------------- footer
rng = random.Random(11)
base = H - 46
for x in range(0, W, 4):
    t = abs((x - W / 2) / (W / 2))
    amp = (1.0 - t * 0.55) * (12 + rng.random() * 26)
    d.line([(x, base - amp), (x, base + amp)], fill=(22, 78, 86), width=2)

foot = font(REG, 13)
label = "CASHOUT STUDIO"
w1 = sum(d.textlength(c, font=foot) + 3.4 for c in label)
label2 = "MADE BY CASHOUT PT.5"
w2 = sum(d.textlength(c, font=foot) + 3.4 for c in label2)
total = w1 + 34 + w2
start = (W - total) / 2
d.rectangle([start - 26, base - 16, start + total + 26, base + 16], fill=BG)
x = tracking(d, (start, base - 8), label, foot, FAINT, 3.4)
d.text((x + 10, base - 8), "\u00b7", font=foot, fill=(40, 60, 76))
tracking(d, (x + 26, base - 8), label2, foot, FAINT, 3.4)

img.convert("RGB").save(OUT, optimize=True)
print(f"wrote {OUT}  {W}x{H}  {OUT.stat().st_size // 1024} KB")
