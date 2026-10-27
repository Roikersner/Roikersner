"""Check a raw video: resolution, fps, HDR, size, and whether it looks compressed in transfer.

Usage: python tools/probe.py <video>
"""
import sys
from pathlib import Path

from common import fps_of, is_hdr, probe, rotation, video_stream


def main(path: str) -> None:
    info = probe(path)
    vs = video_stream(info)
    w, h = int(vs["width"]), int(vs["height"])
    if abs(rotation(vs)) in (90, 270):
        w, h = h, w
    dur = float(info["format"]["duration"])
    size_mb = Path(path).stat().st_size / 1e6
    mb_per_min = size_mb / (dur / 60) if dur else 0
    fps = fps_of(vs)
    hdr = is_hdr(vs)
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]

    print(f"file:        {path}")
    print(f"resolution:  {w}x{h} ({'portrait' if h > w else 'landscape'})")
    print(f"fps:         {fps:.2f}")
    print(f"codec:       {vs.get('codec_name')} {vs.get('pix_fmt')}")
    print(f"HDR:         {'yes' if hdr else 'no'} (transfer={vs.get('color_transfer')}, primaries={vs.get('color_primaries')})")
    print(f"duration:    {dur:.1f}s")
    print(f"size:        {size_mb:.0f}MB ({mb_per_min:.0f}MB per minute)")
    print(f"audio:       {audio[0].get('codec_name')} {audio[0].get('sample_rate')}Hz" if audio else "audio:       NONE")

    warnings = []
    if max(w, h) <= 1280:
        warnings.append("Resolution is 720p or lower: the file was probably compressed on the way (WhatsApp?) or shot at low quality.")
    if mb_per_min < 60 and max(w, h) >= 1920:
        warnings.append("Very low bitrate for this resolution: looks compressed in transfer.")
    if not audio:
        warnings.append("No audio track.")
    print("verdict:     " + ("looks original, good quality" if not warnings else "CHECK: " + " ".join(warnings)))


if __name__ == "__main__":
    main(sys.argv[1])
