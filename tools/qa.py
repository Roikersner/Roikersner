"""Full check of a finished reel before it is handed over, then (with --deliver) the handover.

Checks (each line OK or FIX):
  format    1080x1920, 9:16, 30fps, H.264/AAC, under 300MB
  sync      sound AND picture compared to the source at several points (by content, not length)
  white     no burnt-out white areas
  subs      no subtitle before its word is spoken (ASS styles) / none touches the screen edge (measured)
  frames    writes work/NAME/qa_frames.jpg: look at it before delivering

Usage:
  python tools/qa.py --reel NAME [--video work/NAME/subbed.mp4] [--deliver]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import (FFMPEG, READY, fps_of, load_audio, load_json, probe, reel_dir, run, video_stream)
from cut import video_chain

MAX_MB, PHONE_MB = 300, 30
results: list[tuple[str, bool, str]] = []


def check(name, ok, msg):
    results.append((name, ok, msg))
    print(f"{'OK ' if ok else 'FIX'}  {name:8s} {msg}")


def gray_frames(src, start, dur, chain, size=(90, 160), fps=5):
    w, h = size
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                          "-vf", f"{chain},scale={w}:{h},fps={fps},format=gray", "-f", "rawvideo", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def src_time(cm, t):
    for s in cm:
        if s["out_s"] <= t < s["out_e"]:
            return s["src_s"] + (t - s["out_s"])
    return None


def check_sync(video, cm, src):
    vs_src = video_stream(probe(src))
    chain_src = video_chain(vs_src, 200)
    ra, sa = load_audio(video), load_audio(src)
    sr = 16000
    # pick points in the middle of long-enough pieces, spread over the whole reel
    pieces = [s for s in cm if s["out_e"] - s["out_s"] > 1.6]
    if not pieces:
        check("sync", False, "no piece long enough to compare")
        return
    idx = np.linspace(0, len(pieces) - 1, min(6, len(pieces))).astype(int)
    a_off, v_off = [], []
    for i in idx:
        p = pieces[i]
        t = (p["out_s"] + p["out_e"]) / 2
        ts_ = src_time(cm, t)
        if ts_ is None or ts_ < 2.5:
            continue
        # audio: cross-correlate a 1s window, search +-0.5s
        r = ra[int((t - 0.5) * sr):int((t + 0.5) * sr)]
        s = sa[int((ts_ - 1.0) * sr):int((ts_ + 1.0) * sr)]
        if len(r) and len(s) > len(r):
            c = np.correlate(s, r, mode="valid")
            env = np.abs(c)
            if env.max() > 2.5 * np.median(env):  # a clear match; skip ambiguous spots
                a_off.append((np.argmax(c) - (len(s) - len(r)) / 2) / sr)
        # picture: one reel frame vs source frames around the mapped time (top 60%, avoids subtitles)
        rf = gray_frames(video, t, 0.2, "null", (180, 320), fps=5)[:1]
        sf = gray_frames(src, ts_ - 2.5, 5.0, chain_src, (180, 320), fps=10)
        if len(rf) and len(sf):
            mad = np.array([np.abs(rf[0][:190] - f[:190]).mean() for f in sf])
            if mad.min() < 0.6 * np.median(mad):  # skip spots with no movement: nothing to compare
                v_off.append((int(np.argmin(mad)) - len(sf) // 2) / 10)
    worst_a = max((abs(x) for x in a_off), default=0)
    worst_v = max((abs(x) for x in v_off), default=0)
    enough = len(a_off) >= 2 and len(v_off) >= 2
    check("sync", enough and worst_a <= 0.05 and worst_v <= 0.2,
          f"sound checked at {len(a_off)} points (off by up to {worst_a * 1000:.0f}ms), "
          f"picture at {len(v_off)} points (off by up to {worst_v:.1f}s)"
          + ("" if enough else "; too few clear points, check by watching"))


def check_white(video, dur):
    fr = np.concatenate([gray_frames(video, dur * k / 9, 0.1, "null", (270, 480), fps=10)[:1] for k in range(1, 9)])
    white = 100 * (fr[:, 60:420] >= 250).mean()  # ignore the subtitle area at the bottom
    check("white", white < 0.5, f"{white:.2f}% fully white pixels")


def check_subs(d, video, clean, dur):
    ass = d / "subs.ass"
    times = []
    if ass.exists() and (d / "approved.json").exists():
        onsets = sorted(w["s"] for w in load_json(d / "approved.json")["words"])
        early = 0
        for line in ass.read_text(encoding="utf-8").splitlines():
            if line.startswith("Dialogue:"):
                h, m, s = line.split(",")[1].split(":")
                st = int(h) * 3600 + int(m) * 60 + float(s)
                times.append(st + 0.3)
                nxt = min((o for o in onsets if o >= st - 0.25), default=st)
                early += st < nxt - 0.03
        check("timing", early == 0, f"{early} subtitles appear before their word")
    if not clean.exists():
        check("edges", False, "no clean cut.mp4 to compare against")
        return
    if not times:
        times = list(np.linspace(1, dur - 1, 40))
    over = []
    for t in times[:: max(1, len(times) // 60)]:
        a = gray_frames(video, t, 0.05, "null", (540, 960), fps=30)[:1]
        b = gray_frames(clean, t, 0.05, "null", (540, 960), fps=30)[:1]
        if len(a) and len(b):
            diff = np.abs(a[0] - b[0]) > 60
            if diff[:, :6].any() or diff[:, -6:].any():
                over.append(t)
    check("edges", not over, "no subtitle touches the screen edge" if not over
          else "touching the edge at " + ", ".join(f"{t:.1f}s" for t in over[:8]))


def contact_sheet(video, dur, out):
    times = np.linspace(dur * 0.06, dur * 0.94, 8)
    tiles = []
    for i, t in enumerate(times):
        p = out.parent / f"_qa{i}.png"
        run([FFMPEG, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=360:640", p], quiet=True)
        tiles.append(p)
    run([FFMPEG, "-v", "error", "-y"] + sum([["-i", p] for p in tiles], []) +
        ["-filter_complex", "".join(f"[{i}]" for i in range(8)) + "xstack=inputs=8:layout=" +
         "|".join(f"{(i % 4) * 360}_{(i // 4) * 640}" for i in range(8)), "-frames:v", "1", out], quiet=True)
    for p in tiles:
        p.unlink()


def deliver(reel, video, dur):
    out_dir = READY / reel
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / "reel.mp4"
    if Path(video).stat().st_size / 1e6 > MAX_MB:
        kbps = int((MAX_MB * 0.9 * 8000) / dur) - 192
        run([FFMPEG, "-v", "error", "-y", "-i", video, "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
             "-maxrate", f"{kbps * 2}k", "-bufsize", f"{kbps * 4}k", "-g", "30", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-movflags", "+faststart", final])
    else:
        shutil.copy(video, final)
    kbps = min(3500, max(300, int((PHONE_MB * 0.9 * 8000) / dur) - 96))
    run([FFMPEG, "-v", "error", "-y", "-i", final, "-vf", "scale=720:1280", "-c:v", "libx264", "-preset", "medium",
         "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 3}k", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_dir / "reel-phone.mp4"])
    print(f"delivered: {final} ({final.stat().st_size / 1e6:.0f}MB) and reel-phone.mp4 "
          f"({(out_dir / 'reel-phone.mp4').stat().st_size / 1e6:.0f}MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", required=True)
    ap.add_argument("--video")
    ap.add_argument("--deliver", action="store_true")
    a = ap.parse_args()
    d = reel_dir(a.reel)
    video = Path(a.video) if a.video else d / "subbed.mp4"
    info = probe(video)
    vs = video_stream(info)
    dur = float(info["format"]["duration"])
    size = video.stat().st_size / 1e6
    au = [s for s in info["streams"] if s["codec_type"] == "audio"]
    fmt_ok = (vs["width"], vs["height"]) == (1080, 1920) and abs(fps_of(vs) - 30) < 0.1 \
        and vs["codec_name"] == "h264" and au and au[0]["codec_name"] == "aac"
    check("format", fmt_ok, f"{vs['width']}x{vs['height']} {fps_of(vs):.2f}fps {vs['codec_name']}/"
          f"{au[0]['codec_name'] if au else 'no audio'}")
    check("size", True, f"{size:.0f}MB" + (" (will compress under 300MB on delivery)" if size > MAX_MB else ""))
    cm = load_json(d / "cutmap.json")
    check_sync(video, cm["segments"], cm["src"])
    check_white(video, dur)
    check_subs(d, video, d / "cut.mp4", dur)
    contact_sheet(video, dur, d / "qa_frames.jpg")
    print(f"look at {d / 'qa_frames.jpg'} (Hebrew straight, nothing cut at the edges)")
    if a.deliver:
        if not all(ok for _, ok, _ in results):
            sys.exit("Not delivering: fix the FIX lines first.")
        deliver(a.reel, video, dur)


if __name__ == "__main__":
    main()
