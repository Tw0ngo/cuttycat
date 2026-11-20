"""Tracklist parsing and small text helpers (no Qt needed)."""
import re
from collections import Counter

def fmt(t, dec=1):
    t = round(max(t, 0.0), dec)
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    ss = f"{s:0{3 + dec if dec else 2}.{dec}f}"
    return f"{h}:{m:02d}:{ss}" if h else f"{m:02d}:{ss}"


def parse_time(s):
    parts = s.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(s)
    t = 0.0
    for p in parts:
        t = t * 60 + float(p.replace(",", "."))
    return t


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def safe(name):
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "Track"


def guess_from_filename(stem):
    """'Band - Album (Full Album 2026)' -> (Band, Album, 2026)."""
    year = ""
    m = re.search(r"\s*[\(\[]([^)\]]*?(?:full\s*album|album|lp|ep)[^)\]]*)[\)\]]", stem, re.I)
    if m:
        y = re.search(r"(19|20)\d{2}", m.group(1))
        year = y.group(0) if y else ""
        stem = stem[:m.start()] + stem[m.end():]
    if not year:
        y = re.search(r"[\(\[]((?:19|20)\d{2})[\)\]]", stem)
        if y:
            year = y.group(1)
            stem = stem.replace(y.group(0), "")
    if " - " in stem:
        a, b = stem.split(" - ", 1)
        return a.strip(), b.strip(), year
    return "", stem.strip(), year


TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?"


LEAD_TS = re.compile(rf"^[\[(]?\s*({TS})\s*[\])]?(?:\s*[-–—~]\s*[\[(]?\s*({TS})\s*[\])]?)?"
                     r"\s*(?:[-–—:|.)]\s*|\s+)")


TRAIL_TS = re.compile(rf"\s*(?:[-–—|·•]\s*)?[\[(]?\s*({TS})\s*[\])]?\s*$")


NUM = re.compile(r"^(?:track\s*|#\s*)?([A-Za-z]?\d{1,3})(?:\s*[.):\-–—/]\s*|\s+)(?=\S)", re.I)


JUNK = re.compile(r"\s*[\(\[][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|"
                  r"hq|hd|4k|full\s*album|free\s*download|out\s*now)\b[^)\]]*[\)\]]", re.I)


SEPARATORS = [("Auto-detect", None), ("Dash  ( - )", " - "), ("En dash  ( – )", " – "),
              ("Em dash  ( — )", " — "), ("Slash  ( / )", " / "), ("Bar  ( | )", " | "),
              ("Colon  ( : )", ": "), ("The word “by”", " by "), ("Tab", "\t")]


ORDER_TITLE, ORDER_ARTIST_TITLE, ORDER_TITLE_ARTIST = 0, 1, 2


TIMES_AUTO, TIMES_START, TIMES_LENGTH, TIMES_IGNORE = 0, 1, 2, 3


def _num_value(s):
    """'A2' -> (1, 2), '12' -> (0, 12): vinyl sides count as their own block."""
    m = re.match(r"([A-Za-z]?)(\d+)", s)
    if not m:
        return (0, 0)
    side = ord(m.group(1).upper()) - 64 if m.group(1) else 0
    return (side, int(m.group(2)))


def _merge_lines(text):
    """Join split-up lines: '1.' + 'Title' and 'Title' + '3:41' (Bandcamp & co)."""
    out, prefix = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:track\s*|#\s*)?[A-Za-z]?\d{1,3}\s*[.):]?", line, re.I):
            prefix = line + " "
            continue
        if re.fullmatch(rf"[\[(]?{TS}[\])]?", line) and out and not prefix:
            out[-1] += " " + line
            continue
        out.append(prefix + line)
        prefix = ""
    return out


def parse_tracklist(text, order=None, sep=None, times=TIMES_AUTO, strip_junk=True):
    """Parse a pasted tracklist.

    Returns (entries, info). entries: [dict(num, artist, title, start, length)].
    info: dict(order, sep, times) with what was actually used/detected.
    """
    rows = []
    for raw in _merge_lines(text):
        line = raw.strip().strip("​﻿\"'")
        line = re.sub(r"^[\s•·*▶►▸➤→♪♫]+", "", line)
        if not line or not re.search(r"\w", line):
            continue
        lead = lead_end = None
        m = LEAD_TS.match(line)
        if m and m.end() < len(line):
            lead = parse_time(m.group(1))
            lead_end = parse_time(m.group(2)) if m.group(2) else None
            line = line[m.end():]
        n = NUM.match(line)
        num = n.group(1) if n else None
        rest = line[n.end():] if n else line
        if lead is None and n:          # "1. 00:00 Title"
            m = LEAD_TS.match(rest)
            if m and m.end() < len(rest):
                lead = parse_time(m.group(1))
                lead_end = parse_time(m.group(2)) if m.group(2) else None
                rest = rest[m.end():]
        rows.append(dict(raw=line, num=num, rest=rest, lead=lead, lead_end=lead_end))
    if not rows:
        return [], dict(order=order or ORDER_TITLE, sep=sep, times=None)

    # strip track numbers only if most lines have them and they count upward
    nums = [r["num"] for r in rows if r["num"]]
    use_nums = len(nums) >= max(2, 0.6 * len(rows)) and \
        sum(_num_value(b) > _num_value(a) for a, b in zip(nums, nums[1:])) >= 0.7 * (len(nums) - 1)
    for r in rows:
        line = r["rest"] if (use_nums and r["num"]) or r["lead"] is not None else r["raw"]
        if not (use_nums and r["num"]):
            r["num"] = None
        m = TRAIL_TS.search(line)
        r["trail"] = None
        if m and m.start() > 0:
            r["trail"] = parse_time(m.group(1))
            line = line[:m.start()]
        if strip_junk:
            line = JUNK.sub("", line)
        r["text"] = line.strip(" \t-–—|:·")

    # separator
    if sep is None:
        best, cnt = None, 0
        for _, s in SEPARATORS[1:]:
            c = sum(s in r["text"] for r in rows)
            if c > cnt:
                best, cnt = s, c
        sep = best if cnt >= max(1, 0.5 * len(rows)) else None

    # artist / title order
    if order is None:
        order = ORDER_TITLE
        if sep:
            left = Counter(r["text"].split(sep, 1)[0].strip().lower() for r in rows if sep in r["text"])
            right = Counter(r["text"].rsplit(sep, 1)[1].strip().lower() for r in rows if sep in r["text"])
            lm = left.most_common(1)[0][1] if left else 0
            rm = right.most_common(1)[0][1] if right else 0
            order = ORDER_TITLE_ARTIST if rm > lm and rm > 1 else ORDER_ARTIST_TITLE

    # times
    leads = [r["lead"] for r in rows]
    trails = [r["trail"] for r in rows]
    n_lead = sum(x is not None for x in leads)
    n_trail = sum(x is not None for x in trails)
    mode = times
    if times == TIMES_AUTO:
        mode = None
        if n_lead >= 0.6 * len(rows):
            mode = TIMES_START
        elif n_trail >= 0.6 * len(rows):
            vals = [x for x in trails if x is not None]
            increasing = all(b >= a for a, b in zip(vals, vals[1:]))
            mode = TIMES_START if vals and vals[0] < 1 and increasing and len(vals) > 1 \
                else TIMES_LENGTH
    entries = []
    for r in rows:
        text = r["text"]
        artist, title = None, text
        if sep and sep in text and order != ORDER_TITLE:
            if order == ORDER_ARTIST_TITLE:
                artist, title = (x.strip() for x in text.split(sep, 1))
            else:
                title, artist = (x.strip() for x in text.rsplit(sep, 1))
        start = length = None
        if mode == TIMES_START:
            start = r["lead"] if r["lead"] is not None else r["trail"]
            if r["lead_end"] is not None:
                length = r["lead_end"] - start
        elif mode == TIMES_LENGTH:
            length = r["trail"] if r["trail"] is not None else r["lead"]
        title = re.sub(r'^["“”„\']+|["“”\']+$', "", (title or "").strip()).strip()
        entries.append(dict(num=r["num"], artist=artist or None, title=title or None,
                            start=start, length=length))
    if mode == TIMES_START:
        for a, b in zip(entries, entries[1:]):
            if a["start"] is not None and b["start"] is not None and a["length"] is None:
                a["length"] = b["start"] - a["start"]
    elif mode == TIMES_LENGTH and all(e["length"] is not None for e in entries[:-1]):
        t = 0.0
        for e in entries:
            e["start"] = t
            t += e["length"] or 0
    return entries, dict(order=order, sep=sep, times=mode)


def yt_entries(info):
    """Tracks from the video's chapters, or from timestamps in the description."""
    chapters = info.get("chapters") or []
    if len(chapters) >= 2:
        text = "\n".join(f"{fmt(c.get('start_time') or 0, 0)} {c.get('title', '')}" for c in chapters)
        return parse_tracklist(text)[0], "chapters"
    desc = info.get("description") or ""
    lines = [l for l in desc.splitlines() if re.search(TS, l)]
    if len(lines) >= 3:
        es = parse_tracklist("\n".join(lines))[0]
        if es and all(e["start"] is not None for e in es):
            return es, "description"
    lines = description_tracklist(desc)
    if len(lines) >= 4:
        es = parse_tracklist("\n".join(lines))[0]
        if len(es) >= 4:
            return es, "description-untimed"
    return [], None


TRACK_LINE = re.compile(r"^(?:track\s*\d{1,3}\b|#?\s*[A-Za-z]?\d{1,3}\s*[.):\-–—]\s+\S)", re.I)


def description_tracklist(desc):
    """Pick the tracklist part out of a video description (lines like 'Track 3: A - B' or '3. A - B')."""
    out = []
    for raw in desc.splitlines():
        line = raw.strip()
        if not line or "http" in line or len(line) > 160:
            continue
        line = re.sub(r"^\((?:lead[- ]?in|intro|outro|bonus)\)\s*", "", line, flags=re.I)
        if TRACK_LINE.match(line) or (out and " - " in line and len(line) < 120):
            out.append(line)
        elif " - " in line and len(line) < 120:
            out.append(line)  # e.g. an unnumbered lead-in right before the list
    numbered = [l for l in out if TRACK_LINE.match(l)]
    return out if len(numbered) >= 3 else []
