"""Pick the HDR tone-map setting by MEASURING burnt-out white pixels, not by eye.

Renders sample frames of an HDR video with several npl values and reports, for each,
the % of fully white pixels (Y >= 250) and the average brightness.
Pick the lowest npl whose white % is near zero (< 0.3%) and that is not too dark.

Usage: python tools/hdr_check.py <video> [--npl 100 200 300 400]
"""
import argparse
import subprocess

import numpy as np

from common import FFMPEG, is_hdr, probe, video_stream
from cut import video_chain


def frames(src, chain, times):
    out = []
    for t in times:
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                              "-vf", chain + ",scale=270:480,format=gray", "-f", "rawvideo", "-"],
                             check=True, capture_output=True).stdout
        if raw:
            out.append(np.frombuffer(raw, np.uint8))
    return np.concatenate(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--npl", nargs="+", type=int, default=[100, 200, 300, 400])
    a = ap.parse_args()
    info = probe(a.src)
    vs = video_stream(info)
    dur = float(info["format"]["duration"])
    times = [dur * k / 9 for k in range(1, 9)]
    if not is_hdr(vs):
        y = frames(a.src, video_chain(vs, 200), times)
        print(f"Not HDR. white={100 * (y >= 250).mean():.2f}%  mean={y.mean():.0f}")
        return
    for npl in a.npl:
        y = frames(a.src, video_chain(vs, npl), times)
        print(f"npl={npl:4d}  white={100 * (y >= 250).mean():.2f}%  mean={y.mean():.0f}")


if __name__ == "__main__":
    main()
