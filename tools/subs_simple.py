"""Backup subtitle style in plain ffmpeg (ASS): white with a dark outline, up to 3 words,
keywords in one colour, small pop on entry, lower part of the screen.
Only when asked for "simple subtitles", or when Remotion/HyperFrames do not work.

Usage:
  python tools/subs_simple.py --reel NAME --color "#FFB800" [--keywords מילה מילה] [--visual]
  -> work/NAME/subs.ass and work/NAME/subbed.mp4

--visual: write each subtitle in visual order with python-bidi (only if the Hebrew test
showed reversed text on this computer; see CLAUDE.md).
"""
from __future__ import annotations

import argparse
import re

from PIL import ImageFont

from common import FFMPEG, FONTS, load_json, reel_dir, run, units_from

W, MARGIN_X, FONT_SIZE, MARGIN_V = 1080, 70, 96, 560
FONT_FILE = FONTS / "Heebo-800.ttf"
FONT_NAME = "Heebo ExtraBold"


def ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def is_key(u: str, keywords: set[str]) -> bool:
    core = re.sub(r"[^\w%]", "", u)
    return bool(re.search(r"[A-Za-z0-9]", u)) or core in keywords


def chunks_of(lines, max_units, font):
    out = []
    for line in lines:
        units = units_from(line["words"])
        cur = []
        for u in units:
            if cur and (len(cur) >= max_units or u["s"] - cur[-1]["e"] > 0.4):
                out.append(cur)
                cur = []
            cur.append(u)
            text = " ".join(x["w"] for x in cur)
            if font.getlength(text) > W - 2 * MARGIN_X and len(cur) > 1:  # measured, not guessed
                cur.pop()
                out.append(cur)
                cur = [u]
        if cur:
            out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", required=True)
    ap.add_argument("--color", default="#FFB800")
    ap.add_argument("--keywords", nargs="*", default=[])
    ap.add_argument("--max-words", type=int, default=3)
    ap.add_argument("--visual", action="store_true")
    a = ap.parse_args()
    d = reel_dir(a.reel)
    ap_json = load_json(d / "approved.json")
    font = ImageFont.truetype(str(FONT_FILE), FONT_SIZE)
    chunks = chunks_of(ap_json["lines"], a.max_words, font)
    key_c = ass_color(a.color)
    kw = {re.sub(r"[^\w%]", "", k) for k in a.keywords}
    events = []
    for i, c in enumerate(chunks):
        start = c[0]["s"]
        end = c[-1]["e"] + 0.3
        if i + 1 < len(chunks):
            end = min(max(c[-1]["e"], chunks[i + 1][0]["s"] - 0.02), c[-1]["e"] + 0.6)
        units = c
        if a.visual:
            from bidi.algorithm import get_display
            units = [{**u, "w": get_display(u["w"])} for u in reversed(c)]
        text = " ".join((f"{{\\c{key_c}}}{u['w']}{{\\c&HFFFFFF&}}" if is_key(u["w"], kw) else u["w"]) for u in units)
        # U+200F (right-to-left mark) first: otherwise a line that starts with an English
        # word is laid out left-to-right and the word order flips.
        rlm = "" if a.visual else "‏"
        events.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,"
                      f"{rlm}{{\\fscx112\\fscy112\\t(0,110,\\fscx100\\fscy100)}}{text}")
    enc = 1 if a.visual else -1  # -1 = libass decides direction per line (needed for Hebrew + English)
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,7,3,2,{MARGIN_X},{MARGIN_X},{MARGIN_V},{enc}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"
    (d / "subs.ass").write_text(ass, encoding="utf-8")
    run([FFMPEG, "-v", "error", "-stats", "-y", "-i", d / "cut.mp4",
         "-vf", f"ass={d / 'subs.ass'}:fontsdir={FONTS}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         "-g", "30", "-keyint_min", "30", "-sc_threshold", "0", "-c:a", "copy",
         "-movflags", "+faststart", d / "subbed.mp4"])
    print(f"{len(events)} subtitles -> {d / 'subbed.mp4'}")


if __name__ == "__main__":
    main()
