"""Transcript approval file: make it, then read it back after the user edits it.

  python tools/approval.py make --reel NAME    # work/NAME/transcript_subs.json -> לאישור/NAME.md
  python tools/approval.py read --reel NAME    # לאישור/NAME.md -> work/NAME/approved.json + summary
  python tools/approval.py asis --reel NAME    # user said not to wait: build from the unapproved text

Corrections the user made are remembered in work/NAME/corrections.json and applied
automatically to a new approval file after a re-cut.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys

from common import APPROVAL, fmt_t, load_json, reel_dir, save_json

LOW_CONF = 0.5
LINE_RE = re.compile(r"^- \[(\d{2}:\d{2}\.\d)\]\s*(?:⚠\s*)?(.*)$")
HEADER = ("# ", "מתקנים", "> ")


def phrases_from(words, max_words=7, gap=0.3):
    out, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap or len(cur) >= max_words):
            out.append(cur)
            cur = []
        cur.append(w)
        if w["w"][-1:] in ".?!,":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def apply_corrections(tokens, corr):
    for wrong, right in corr.items():
        wl, rl = wrong.split(), right.split()
        i = 0
        while i <= len(tokens) - len(wl):
            if tokens[i:i + len(wl)] == wl:
                tokens[i:i + len(wl)] = rl
                i += len(rl)
            else:
                i += 1
    return tokens


def repeats(words, n=5, window=20.0):
    """A 5-word phrase said twice within 20s = a duplicate take left in the cut."""
    hits = []
    norm = [re.sub(r"[^\w]", "", w["w"]) for w in words]
    for i in range(len(words) - n):
        key = norm[i:i + n]
        for j in range(i + n, len(words) - n + 1):
            if words[j]["s"] - words[i]["s"] > window:
                break
            if norm[j:j + n] == key:
                hits.append((words[i]["s"], words[j]["s"], " ".join(w["w"] for w in words[i:i + n])))
                break
    return hits


def make(reel):
    d = reel_dir(reel)
    tr = load_json(d / "transcript_subs.json")
    words = [w for l in tr["lines"] for w in l["words"]]
    corr = load_json(d / "corrections.json") if (d / "corrections.json").exists() else {}
    ph = phrases_from(words)
    rows, saved = [], []
    for p in ph:
        toks = [w["w"] for w in p]
        fixed = apply_corrections(list(toks), corr) if corr else toks
        warn = any(w["p"] < LOW_CONF for w in p)
        rows.append(f"- [{fmt_t(p[0]['s'])}] {'⚠ ' if warn else ''}{' '.join(fixed)}")
        saved.append({"t": fmt_t(p[0]["s"]), "words": p, "prefilled": fixed})
    save_json(saved, d / "phrases.json")
    md = APPROVAL / f"{reel}.md"
    md.write_text(f"# {reel}: תמלול לאישור\n\n"
                  "מתקנים רק מילים. לא נוגעים בזמן שבסוגריים. "
                  "שורה בלי כתובית: מוחקים את הטקסט ומשאירים את הסוגריים.\n\n"
                  + "\n".join(rows) + "\n", encoding="utf-8")
    n_warn = sum("⚠" in r for r in rows)
    print(f"{md} ({len(rows)} lines, {n_warn} marked ⚠)")
    for a, b, txt in repeats(words):
        print(f"REPEAT: '{txt}' at {fmt_t(a)} and again at {fmt_t(b)}: a duplicate take survived the cut. Fix before continuing.")


def retime(orig, new_tokens):
    """Give each new token a time, using the original word timings."""
    ow = [w["w"] for w in orig]
    out = []
    sm = difflib.SequenceMatcher(a=ow, b=new_tokens, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out += [{"w": new_tokens[j1 + k], "s": orig[i1 + k]["s"], "e": orig[i1 + k]["e"]} for k in range(i2 - i1)]
        elif op in ("replace", "insert"):
            if i2 > i1:
                s, e = orig[i1]["s"], orig[i2 - 1]["e"]
            else:  # pure insert: squeeze next to the neighbour
                s = orig[i1 - 1]["e"] if i1 > 0 else orig[0]["s"]
                e = orig[i1]["s"] if i1 < len(orig) else orig[-1]["e"]
                if e - s < 0.1:
                    s, e = (s - 0.1, s) if i1 > 0 else (e, e + 0.1)
            toks = new_tokens[j1:j2]
            total = sum(len(t) for t in toks) or 1
            t = s
            for tok in toks:
                dt = (e - s) * len(tok) / total
                out.append({"w": tok, "s": round(t, 3), "e": round(t + dt, 3)})
                t += dt
    return out


def read(reel, as_is=False):
    d = reel_dir(reel)
    saved = load_json(d / "phrases.json")
    by_t = {p["t"]: p for p in saved}
    edited = {}
    unclear = []
    if not as_is:
        for n, line in enumerate((APPROVAL / f"{reel}.md").read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith(HEADER):
                continue
            m = LINE_RE.match(line)
            if not m or m.group(1) not in by_t:
                unclear.append((n, line))
                continue
            edited[m.group(1)] = m.group(2).strip()
    if unclear:
        print("UNCLEAR lines (edited time or a note?). Ask the user about each, do not guess:")
        for n, line in unclear:
            print(f"  line {n}: {line}")
        sys.exit(2)
    corr = load_json(d / "corrections.json") if (d / "corrections.json").exists() else {}
    lines, n_words, n_deleted, n_missing = [], 0, 0, 0
    for p in saved:
        orig = p["words"]
        if as_is:
            new = p["prefilled"]
        elif p["t"] not in edited:
            n_missing += 1
            new = []
        else:
            new = edited[p["t"]].split()
        if not new:
            n_deleted += p["t"] in edited or as_is
            continue
        ow = [w["w"] for w in orig]
        sm = difflib.SequenceMatcher(a=ow, b=new, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != "equal":
                n_words += max(i2 - i1, j2 - j1)
                if op == "replace":
                    corr[" ".join(ow[i1:i2])] = " ".join(new[j1:j2])
        lines.append({"t": p["t"], "words": retime(orig, new)})
    save_json(corr, d / "corrections.json")
    save_json({"approved": not as_is, "lines": lines,
               "words": [w for l in lines for w in l["words"]]}, d / "approved.json")
    if as_is:
        print("Built from the UNAPPROVED transcript (tell the user).")
    else:
        extra = f", {n_missing} lines missing from the file (treated as no subtitle)" if n_missing else ""
        print(f"{n_words} words corrected, {n_deleted} lines without subtitle{extra}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["make", "read", "asis"])
    ap.add_argument("--reel", required=True)
    a = ap.parse_args()
    {"make": lambda: make(a.reel), "read": lambda: read(a.reel), "asis": lambda: read(a.reel, True)}[a.action]()
