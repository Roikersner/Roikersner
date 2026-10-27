"""Cut a raw video down to the kept lines, in ONE ffmpeg pass (trim/atrim + concat).

keep.json (written by Claude after reading transcript_cut.txt):
  {"keep": [1, 2, 5, 6, 7, ...]}              # line ids from the cut transcript
  optional "ranges": [[12.3, 15.8], ...]      # extra explicit source ranges (seconds)
  optional "remove": [[s, e], ...]            # exact source ranges to drop (a stutter with no pause)
  optional "npl": 200                          # HDR tone-map peak (see hdr_check.py)

Usage:
  python tools/cut.py --reel NAME --src <video> --plan     # show what goes + new length, no render
  python tools/cut.py --reel NAME --src <video>            # render work/NAME/cut.mp4 + cutmap.json
"""
from __future__ import annotations

import argparse
import sys

from common import (FFMPEG, fmt_t, is_hdr, load_audio, load_json, noise_floor_db, probe,
                    reel_dir, rms_frames, run, save_json, video_stream)

SR = 16000
HOP = 0.01
TAIL_MAX = 0.5      # keep the end of a word up to 0.5s, judged by the audio itself
PAUSE = 0.15        # long silences are shortened to about this
INNER_SIL = 0.6     # silences longer than this inside a kept stretch get shortened


def refine(ranges, audio):
    db = rms_frames(audio, SR, HOP)
    thr = max(noise_floor_db(db) + 12, -50)
    voiced = db > thr
    n = len(voiced)

    def idx(t):
        return min(n - 1, max(0, int(t / HOP)))

    out = []
    for s, e in ranges:
        # start: back up while the voice is already on (max 0.3s); else jump to the onset (max 0.25s)
        i = idx(s)
        if voiced[i]:
            k = i
            while k > 0 and voiced[k - 1] and (i - k) * HOP < 0.3:
                k -= 1
        else:
            k = i
            while k < n - 1 and not voiced[k] and (k - i) * HOP < 0.25:
                k += 1
        s2 = max(0.0, k * HOP - PAUSE / 2)
        # end: follow the word's tail by energy, at most TAIL_MAX after the transcript end
        j = idx(e)
        while j < n - 1 and voiced[j] and (j - idx(e)) * HOP < TAIL_MAX:
            j += 1
        e2 = min(n * HOP, j * HOP + PAUSE / 2)
        out.append([s2, e2])
    out.sort()
    merged = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    # shorten long silences inside kept stretches
    final = []
    for s, e in merged:
        cur = s
        i, j_end = idx(s), idx(e)
        while i < j_end:
            if not voiced[i]:
                k = i
                while k < j_end and not voiced[k]:
                    k += 1
                if (k - i) * HOP > INNER_SIL and k < j_end:
                    final.append([cur, i * HOP + PAUSE / 2])
                    cur = k * HOP - PAUSE / 2
                i = k
            else:
                i += 1
        final.append([cur, e])
    return [[round(s, 3), round(e, 3)] for s, e in final if e - s > 0.05]


def video_chain(vs, npl):
    chain = []
    if is_hdr(vs):
        tin = {"arib-std-b67": "arib-std-b67", "smpte2084": "smpte2084"}.get(vs.get("color_transfer"), "arib-std-b67")
        chain += [f"zscale=tin={tin}:min=bt2020nc:pin=bt2020:t=linear:npl={npl}", "format=gbrpf32le",
                  "zscale=p=bt709", "tonemap=tonemap=mobius:desat=0", "zscale=t=bt709:m=bt709:r=tv"]
    chain += ["scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos", "crop=1080:1920",
              "setsar=1", "fps=30", "format=yuv420p"]
    return ",".join(chain)


def build_filter(ranges, vs, npl):
    n = len(ranges)
    f = [f"[0:v]{video_chain(vs, npl)},split={n}" + "".join(f"[v{i}]" for i in range(n)),
         "[0:a]aresample=48000,asplit=" + str(n) + "".join(f"[a{i}]" for i in range(n))]
    for i, (s, e) in enumerate(ranges):
        d = e - s
        f.append(f"[v{i}]trim=start={s}:end={e},setpts=PTS-STARTPTS[vt{i}]")
        f.append(f"[a{i}]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d=0.01,afade=t=out:st={max(0, d - 0.012):.3f}:d=0.012[at{i}]")
    f.append("".join(f"[vt{i}][at{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    f.append("[cv]fps=30[vout]")
    f.append("[ca]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    return ";".join(f)


def encode(src, flt, out, hw=True):
    cmd = [FFMPEG, "-v", "error", "-stats", "-y"] + (["-hwaccel", "auto"] if hw else []) + [
        "-i", src, "-filter_complex", flt, "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    run(cmd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()
    d = reel_dir(a.reel)
    tr = load_json(d / "transcript_cut.json")
    keep = load_json(d / "keep.json")
    ids = set(keep.get("keep", []))
    lines = tr["lines"]
    ranges = [[l["start"], l["end"]] for l in lines if l["id"] in ids] + keep.get("ranges", [])
    audio = load_audio(a.src, SR)
    final = refine(ranges, audio)
    # "remove": exact source ranges to drop with no edge adjustment (stutters inside continuous speech)
    for rs, re_ in keep.get("remove", []):
        nxt = []
        for s, e in final:
            if re_ <= s or rs >= e:
                nxt.append([s, e])
            else:
                if rs - s > 0.05:
                    nxt.append([s, rs])
                if e - re_ > 0.05:
                    nxt.append([re_, e])
        final = nxt
    new_len = sum(e - s for s, e in final)

    if a.plan:
        dropped = [l for l in lines if l["id"] not in ids]
        print(f"Original {fmt_t(tr['duration'])} -> about {fmt_t(new_len)} "
              f"(removing {tr['duration'] - new_len:.0f}s). Lines removed ({len(dropped)}):")
        for l in dropped:
            print(f"  [{l['id']:04d}] {fmt_t(l['start'])} {l['text']}")
        return

    vs = video_stream(probe(a.src))
    flt = build_filter(final, vs, keep.get("npl", 200))
    out = str(d / "cut.mp4")
    try:
        encode(a.src, flt, out, hw=True)
    except Exception:
        print("hardware decode failed, retrying without it", file=sys.stderr)
        encode(a.src, flt, out, hw=False)
    # map of output time -> source time, used by the sync check and for re-cuts
    cm, t = [], 0.0
    for s, e in final:
        cm.append({"src_s": s, "src_e": e, "out_s": round(t, 3), "out_e": round(t + e - s, 3)})
        t += e - s
    save_json({"src": a.src, "segments": cm}, d / "cutmap.json")
    print(f"cut.mp4 ready: {fmt_t(t)} ({len(final)} pieces)")


if __name__ == "__main__":
    main()
