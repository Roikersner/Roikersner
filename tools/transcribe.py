"""Word-level transcription with faster-whisper, split at silences.

Two passes in the workflow:
  cut pass:       --model large-v3   on the raw video   (decides what to cut)
  subtitle pass:  --model ivrit      on the cut video    (text for subtitles, with --align-onsets)

Each silence-separated chunk is transcribed on its own with
condition_on_previous_text=False, otherwise repeated takes merge into one line.
A word longer than 2.5s means takes were merged: that chunk is re-transcribed in
smaller pieces automatically.

Usage:
  python tools/transcribe.py <media> --model large-v3 --out work/<reel>/transcript_cut.json
  python tools/transcribe.py <media> --model ivrit --align-onsets --out work/<reel>/transcript_subs.json
  python tools/transcribe.py --download        # fetch both models once
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

from common import fmt_t, load_audio, noise_floor_db, rms_frames, save_json

SR = 16000
HOP = 0.01
MODELS = {"large-v3": "large-v3", "ivrit": "ivrit-ai/whisper-large-v3-turbo-ct2"}
LONG_WORD = 2.5


def get_model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(MODELS.get(name, name), device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 4)


def speech_mask(db, floor):
    return db > max(floor + 12, -50)


def split_chunks(audio, min_sil=0.35, max_len=20.0, offset=0.0):
    """Return [(start, end)] speech chunks separated by silences >= min_sil seconds."""
    db = rms_frames(audio, SR, HOP)
    mask = speech_mask(db, noise_floor_db(db))
    # close tiny gaps, find silence runs
    n = len(mask)
    chunks, i = [], 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n:
            if mask[j]:
                j += 1
                continue
            k = j
            while k < n and not mask[k]:
                k += 1
            if (k - j) * HOP >= min_sil or k >= n:
                break
            j = k
        chunks.append([i * HOP, j * HOP])
        i = j
    # drop blips, merge very short chunks into a close neighbour, cap length
    chunks = [c for c in chunks if c[1] - c[0] >= 0.12]
    merged = []
    for c in chunks:
        if merged and (c[1] - c[0] < 0.8 or merged[-1][1] - merged[-1][0] < 0.8) \
                and c[0] - merged[-1][1] < 0.5 and c[1] - merged[-1][0] <= max_len:
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    out = []
    for s, e in merged:
        while e - s > max_len:  # hard split very long runs (no silence found)
            out.append((s, s + max_len))
            s += max_len
        out.append((s, e))
    return [(s + offset, e + offset) for s, e in out]


def transcribe_chunk(model, audio, s, e, base=0.0):
    pad = 0.15
    a0 = max(0, int((s - base - pad) * SR))
    a1 = min(len(audio), int((e - base + pad) * SR))
    seg_audio = audio[a0:a1]
    t0 = base + a0 / SR
    segs, _ = model.transcribe(seg_audio, language="he", word_timestamps=True,
                               condition_on_previous_text=False, beam_size=5,
                               temperature=0.0, vad_filter=False)
    words = []
    for sg in segs:
        for w in sg.words or []:
            words.append({"w": w.word.strip(), "s": round(t0 + w.start, 3),
                          "e": round(t0 + w.end, 3), "p": round(w.probability, 3)})
    return [w for w in words if w["w"]]


def align_onsets(words, audio):
    """Move each word start to where the voice actually starts (so subtitles never lead speech)."""
    db = rms_frames(audio, SR, HOP)
    floor = noise_floor_db(db)
    thr = max(floor + 10, -52)
    for w in words:
        i0 = int(max(0, w["s"] - 0.05) / HOP)
        i1 = int(min(w["e"] - 0.04, w["s"] + 0.35) / HOP)
        for i in range(i0, max(i0, i1)):
            if i < len(db) and db[i] > thr:
                w["s"] = round(max(w["s"], i * HOP), 3)
                break
    return words


def group_lines(words, gap=0.45, max_words=12):
    """Group words into transcript lines at pauses / sentence ends."""
    lines, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap or len(cur) >= max_words):
            lines.append(cur)
            cur = []
        cur.append(w)
        if w["w"][-1:] in ".?!":
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [{"id": i + 1, "start": l[0]["s"], "end": l[-1]["e"],
             "text": " ".join(x["w"] for x in l), "words": l} for i, l in enumerate(lines)]


def run(media, model_name, out, align=False):
    t_start = time.time()
    audio = load_audio(media, SR)
    dur = len(audio) / SR
    print(f"audio {dur:.1f}s, model {model_name}", file=sys.stderr)
    model = get_model(model_name)
    chunks = split_chunks(audio)
    words = []
    for n, (s, e) in enumerate(chunks, 1):
        cw = transcribe_chunk(model, audio, s, e)
        if any(w["e"] - w["s"] > LONG_WORD for w in cw):
            # merged takes: re-transcribe this region in small pieces
            sub = split_chunks(audio[int(s * SR):int(e * SR)], min_sil=0.15, max_len=6.0, offset=s)
            if len(sub) > 1:
                print(f"  re-transcribing {fmt_t(s)}-{fmt_t(e)} in {len(sub)} pieces (merged takes)", file=sys.stderr)
                cw = [w for ss, ee in sub for w in transcribe_chunk(model, audio, ss, ee)]
        for w in cw:
            if w["e"] - w["s"] > LONG_WORD:
                w["long"] = True
        words.extend(cw)
        print(f"  chunk {n}/{len(chunks)} {fmt_t(s)}-{fmt_t(e)}", file=sys.stderr)
    words.sort(key=lambda w: w["s"])
    if align:
        words = align_onsets(words, audio)
    lines = group_lines(words)
    save_json({"source": str(media), "model": MODELS.get(model_name, model_name),
               "duration": dur, "lines": lines}, out)
    txt = os.path.splitext(out)[0] + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        for l in lines:
            flag = " ⚠LONG" if any(w.get("long") for w in l["words"]) else ""
            f.write(f"[{l['id']:04d}] {fmt_t(l['start'])}-{fmt_t(l['end'])} {l['text']}{flag}\n")
    print(f"done in {time.time() - t_start:.0f}s -> {out} and {txt}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="?")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--out")
    ap.add_argument("--align-onsets", action="store_true")
    ap.add_argument("--download", action="store_true", help="download both models and exit")
    a = ap.parse_args()
    if a.download:
        for m in MODELS:
            print("downloading", MODELS[m], file=sys.stderr)
            get_model(m)
        print("models ready")
    else:
        run(a.media, a.model, a.out, a.align_onsets)
