#!/usr/bin/env python3
"""Convert text copied from the Christmas Specials wiki table into list lines.

Input: the table text as copied from the page (a show name, a tab, then one
quoted episode on the same line, or a block of indented quoted episodes).
Output: one `Show | Episode title` line per episode.
Usage: fandom_to_list.py copied.txt > christmas.txt
"""
import re
import sys

QUOTE = re.compile(r'[“"]([^“”"]+)[”"]')
HEADINGS = {"live action shows", "animated shows", "hybrid shows"}


def parse(text):
    show = None
    seen = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low in HEADINGS or low.startswith("show") and "christmas episode" in low:
            show = None
            continue
        indented = raw[:1] in (" ", "\t")
        if indented:
            titles = QUOTE.findall(line) or [line.strip('“”" ')]
        elif "\t" in raw:
            name, _, rest = raw.partition("\t")
            show = name.strip()
            rest = rest.strip()
            titles = QUOTE.findall(rest)
            if not titles and rest.startswith(('"', "“")):
                titles = [rest.lstrip('"“').strip()]  # closing quote missing on the page
            elif not titles and rest:
                titles = [rest]  # some entries have no quote marks at all
        else:
            continue
        if not show:
            continue
        for t in titles:
            t = t.strip()
            key = (show.lower(), t.lower())
            if t and key not in seen:
                seen.add(key)
                yield show, t


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        rows = list(parse(f.read()))
    for show, title in rows:
        print(f"{show} | {title}")
    print(f"{len(rows)} episodes from {len({s for s, _ in rows})} shows", file=sys.stderr)
