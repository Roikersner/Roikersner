"""Shared helpers for the reels tools: ffmpeg paths, probing, audio loading."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"
WORK = ROOT / "work"
UPLOAD = ROOT / "להעלאה"
APPROVAL = ROOT / "לאישור"
READY = ROOT / "מוכן"

MAC_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin")


def _tool(name: str) -> str:
    # On a Mac the regular Homebrew ffmpeg lacks libass and zimg, so prefer ffmpeg-full.
    cand = MAC_FFMPEG_FULL / name
    if cand.exists():
        return str(cand)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found. Run setup.sh first.")
    return found


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")


def run(cmd: list[str], quiet: bool = False) -> subprocess.CompletedProcess:
    if not quiet:
        print("+", " ".join(str(c) for c in cmd[:6]), "..." if len(cmd) > 6 else "", file=sys.stderr)
    return subprocess.run([str(c) for c in cmd], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE if quiet else None)


def probe(path: str | Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def video_stream(info: dict) -> dict:
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def is_hdr(vs: dict) -> bool:
    return vs.get("color_transfer") in ("smpte2084", "arib-std-b67") or vs.get("color_primaries") == "bt2020"


def rotation(vs: dict) -> int:
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(sd["rotation"])
    return int(vs.get("tags", {}).get("rotate", 0))


def fps_of(vs: dict) -> float:
    num, den = vs.get("avg_frame_rate", "0/1").split("/")
    return float(num) / float(den) if float(den) else 0.0


def load_audio(path: str | Path, sr: int = 16000):
    """Decode any media file to mono float32 numpy array."""
    import numpy as np
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def rms_frames(audio, sr: int = 16000, hop: float = 0.01):
    """RMS energy in dB per `hop` seconds."""
    import numpy as np
    h = int(sr * hop)
    n = len(audio) // h
    a = audio[: n * h].reshape(n, h)
    rms = np.sqrt((a ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def noise_floor_db(db) -> float:
    import numpy as np
    return float(np.percentile(db, 10))


def fmt_t(t: float) -> str:
    m = int(t // 60)
    return f"{m:02d}:{t - 60 * m:04.1f}"


_LATIN_NUM = re.compile(r"[A-Za-z0-9]")
_PREFIX_ONLY = re.compile(r"^[ולמבהשכ]{1,3}-$")


def units_from(words: list[dict]) -> list[dict]:
    """Join words that must stay together in one subtitle so they don't flip or split:
    runs of English words / numbers ("Cloud API", "25%"), a bare Hebrew prefix with a hyphen
    ("ל-" + "Cloud"), and a lone "%" or punctuation mark onto its neighbour."""
    out: list[dict] = []
    for w in words:
        txt = w["w"]
        if out:
            prev = out[-1]["w"]
            glue = (
                _PREFIX_ONLY.match(prev)                                    # "ל-" + next word
                or re.fullmatch(r"[%.,!?:;]+", txt)                          # lone % or punctuation
                or (_LATIN_NUM.search(prev) and _LATIN_NUM.search(txt)
                    and not re.search(r"[֐-׿]", txt.split("-")[0] if "-" in txt else txt)
                    and not prev.endswith((".", ",", "?", "!")))           # English/number run
            )
            if glue:
                sep = "" if (_PREFIX_ONLY.match(prev) or re.fullmatch(r"[%.,!?:;]+", txt)) else " "
                out[-1] = {"w": prev + sep + txt, "s": out[-1]["s"], "e": w["e"]}
                continue
        out.append({"w": txt, "s": w["s"], "e": w["e"]})
    return out


def reel_dir(reel: str) -> Path:
    d = WORK / reel
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(p: str | Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(obj, p: str | Path) -> None:
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
