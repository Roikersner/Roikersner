#!/usr/bin/env bash
# One-time (and per-cloud-session) setup for the reels folder. Safe to run again.
#   bash setup.sh            full: tools + Python packages + transcription models (~5GB)
#   bash setup.sh --quick    tools + packages now, models downloaded in the background
set -u
cd "$(dirname "$0")"
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1
ok()   { echo "OK    $*"; }
miss() { echo "MISSING $*"; }

mkdir -p להעלאה לאישור מוכן work fonts

# ffmpeg (Mac: ffmpeg-full; elsewhere: the system one must have libass + zimg)
FF=ffmpeg; [ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ] && FF=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
if command -v "$FF" >/dev/null; then
  F=$("$FF" -hide_banner -filters 2>/dev/null)
  for f in subtitles zscale tonemap loudnorm; do
    echo "$F" | grep -q " $f " && ok "ffmpeg: $f" || miss "ffmpeg: $f (Mac: brew install ffmpeg-full)"
  done
else
  miss "ffmpeg (Mac: brew install ffmpeg-full; Windows: winget install Gyan.FFmpeg)"
fi

# Python packages in .venv
if command -v uv >/dev/null; then
  [ -x .venv/bin/python ] || uv venv -q .venv
  uv pip install -q --python .venv/bin/python faster-whisper python-bidi numpy pillow fonttools
else
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/pip install -q faster-whisper python-bidi numpy pillow fonttools
fi
.venv/bin/python -c "import faster_whisper, bidi, numpy, PIL, fontTools" && ok "python packages" || miss "python packages"

# Heebo, static weights 800/900 (ffmpeg can't pick a weight from a variable font)
if [ ! -f fonts/Heebo-800.ttf ]; then
  curl -sSfL -o fonts/Heebo-VF.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/heebo/Heebo%5Bwght%5D.ttf"
  for w in 800 900; do .venv/bin/fonttools varLib.instancer -q --update-name-table fonts/Heebo-VF.ttf wght=$w -o fonts/Heebo-$w.ttf; done
fi
[ -f fonts/Heebo-800.ttf ] && ok "font Heebo 800/900" || miss "font Heebo"

command -v node >/dev/null && ok "node $(node -v)" || miss "node (needed for Remotion / HyperFrames)"
[ -d .claude/skills ] && ls .claude/skills | grep -qi remotion && ok "Remotion skills" \
  || echo "TODO  Remotion/HyperFrames skills: ask Claude to run 'npx skills add remotion-dev/skills' and 'npx skills add heygen-com/hyperframes' in this folder"

# Transcription models (large-v3 ~3.1GB, ivrit-ai ~1.6GB) from Hugging Face
if curl -sS -o /dev/null -m 10 https://huggingface.co 2>/dev/null; then
  if [ $QUICK = 1 ]; then
    nohup .venv/bin/python tools/transcribe.py --download > work/models-download.log 2>&1 &
    echo "...   transcription models downloading in the background (work/models-download.log)"
  else
    .venv/bin/python tools/transcribe.py --download && ok "transcription models"
  fi
else
  miss "transcription models: huggingface.co is not reachable from here (cloud: allow huggingface.co in the environment's network settings)"
fi
exit 0
