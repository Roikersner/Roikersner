"""Reel cover, 1080x1920, Hebrew written in code (never by an image model).

Layout: dark background with a soft glow in one colour, a small topic line on top,
one picture in the middle, a 2-line hook title below (2nd line in colour).
Everything important sits between y=420 and y=1500 (what the profile grid shows).

Usage:
  python tools/cover.py --reel NAME --variant A --kicker "חדש בוואטסאפ לעסקים" \
      --title1 "וואטסאפ רשמי" --title2 "בלי כאב ראש" --color "#25D366" \
      [--image picture.png | --frame 12.5 | --icon chat] [--handle @name]
-> מוכן/NAME/cover-A.jpg and cover-A.png

--image: a picture on black (AI or your own photo); black is blended into the background.
--frame: a frame from the reel at that second (for a cover with your face).
--icon:  a simple shape drawn in code (free): chat, bolt, chart, check, play, star.
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from common import FFMPEG, FONTS, READY, reel_dir

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 420, 1500


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def font(weight, size):
    return ImageFont.truetype(str(FONTS / f"Heebo-{weight}.ttf"), size)


def fit_font(draw, text, weight, size, max_w):
    while size > 30:
        f = font(weight, size)
        if draw.textlength(text, font=f, direction="rtl") <= max_w:
            return f
        size -= 4
    return font(weight, size)


def background(color):
    bg = Image.new("RGB", (W, H), (8, 8, 12))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    r, gg, b = color
    g.ellipse([W / 2 - 520, 520, W / 2 + 520, 1340], fill=(r // 3, gg // 3, b // 3))
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    return ImageChops.add(bg, glow)


def icon(name, color, size=520):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = color + (255,)
    s = size
    if name == "chat":
        d.rounded_rectangle([s * .1, s * .15, s * .9, s * .72], radius=s * .14, fill=c)
        d.polygon([(s * .25, s * .7), (s * .2, s * .9), (s * .45, s * .71)], fill=c)
        for i in range(3):
            x = s * (.3 + .2 * i)
            d.ellipse([x - s * .05, s * .38, x + s * .05, s * .48], fill=(10, 10, 14, 255))
    elif name == "bolt":
        d.polygon([(s * .58, s * .05), (s * .2, s * .55), (s * .47, s * .55), (s * .4, s * .95),
                   (s * .8, s * .42), (s * .53, s * .42)], fill=c)
    elif name == "chart":
        for i, hgt in enumerate([.3, .5, .42, .8]):
            x = s * (.12 + .2 * i)
            d.rounded_rectangle([x, s * (.9 - hgt), x + s * .14, s * .9], radius=s * .03, fill=c)
    elif name == "check":
        d.ellipse([s * .08, s * .08, s * .92, s * .92], outline=c, width=int(s * .07))
        d.line([(s * .3, s * .52), (s * .45, s * .67), (s * .72, s * .36)], fill=c, width=int(s * .08), joint="curve")
    elif name == "play":
        d.rounded_rectangle([s * .08, s * .2, s * .92, s * .8], radius=s * .12, fill=c)
        d.polygon([(s * .42, s * .36), (s * .42, s * .64), (s * .65, s * .5)], fill=(10, 10, 14, 255))
    else:  # star
        pts = []
        for k in range(10):
            ang = -math.pi / 2 + k * math.pi / 5
            rad = s * (.45 if k % 2 == 0 else .2)
            pts.append((s / 2 + rad * math.cos(ang), s / 2 + rad * math.sin(ang)))
        d.polygon(pts, fill=c)
    return im


def picture_on_black(path, size=620):
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    # use brightness as alpha so the black background melts into the cover
    alpha = im.convert("L").point(lambda v: min(255, v * 3))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    return out


def frame_image(reel, t, size=760):
    p = reel_dir(reel) / f"_cover_frame_{t}.png"
    src = reel_dir(reel) / "cut.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(p)], check=True)
    im = Image.open(p).convert("RGB")
    im = im.crop((0, 0, im.width, int(im.width * 1.0)))  # square from the top (face area)
    im = im.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size, size], radius=60, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", required=True)
    ap.add_argument("--variant", default="A")
    ap.add_argument("--kicker", required=True)
    ap.add_argument("--title1", required=True)
    ap.add_argument("--title2", required=True)
    ap.add_argument("--color", default="#FFB800")
    ap.add_argument("--image")
    ap.add_argument("--frame", type=float)
    ap.add_argument("--icon", default="star")
    ap.add_argument("--handle")
    a = ap.parse_args()
    color = hex_rgb(a.color)
    img = background(color)
    d = ImageDraw.Draw(img)

    # topic line
    kf = fit_font(d, a.kicker, 800, 58, W - 200)
    d.text((W / 2, 470), a.kicker, font=kf, fill=(230, 230, 235), anchor="mm", direction="rtl")
    tw = d.textlength(a.kicker, font=kf, direction="rtl")
    d.rounded_rectangle([W / 2 - tw / 2 - 30, 515, W / 2 + tw / 2 + 30, 523], radius=4, fill=color)

    # middle picture
    if a.image:
        pic = picture_on_black(a.image)
    elif a.frame is not None:
        pic = frame_image(a.reel, a.frame)
    else:
        pic = icon(a.icon, color)
    cy = 850
    img.paste(pic, (int(W / 2 - pic.width / 2), int(cy - pic.height / 2)), pic)

    # hook title, 2 lines, second in colour
    f1 = fit_font(d, a.title1, 900, 150, W - 120)
    f2 = fit_font(d, a.title2, 900, 150, W - 120)
    y1, y2 = 1250, 1410
    for txt, f, y, fill in ((a.title1, f1, y1, (255, 255, 255)), (a.title2, f2, y2, color)):
        d.text((W / 2, y), txt, font=f, fill=fill, anchor="mm", direction="rtl",
               stroke_width=6, stroke_fill=(5, 5, 8))
    if a.handle:
        hf = font(800, 44)
        d.text((W / 2, 1790), a.handle, font=hf, fill=(200, 200, 205), anchor="mm")

    # everything important must be inside the grid area
    box = d.textbbox((W / 2, y2), a.title2, font=f2, anchor="mm", direction="rtl")
    if box[3] > SAFE_BOTTOM:
        print("WARNING: title goes below the grid area", file=sys.stderr)

    out = READY / a.reel
    out.mkdir(parents=True, exist_ok=True)
    img.save(out / f"cover-{a.variant}.png")
    img.save(out / f"cover-{a.variant}.jpg", quality=92)
    grid = img.crop((0, (H - 1440) // 2, W, (H + 1440) // 2)).resize((270, 360))  # how the profile grid shows it
    grid.save(reel_dir(a.reel) / f"cover-{a.variant}-grid.png")
    print(f"{out / f'cover-{a.variant}.jpg'} (+ .png); grid preview in work/{a.reel}/")


if __name__ == "__main__":
    main()
