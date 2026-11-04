#!/usr/bin/env python3
"""Turn lists/christmas.txt into season/episode numbers and upload it to an MDBList static list.

Needs only Python 3 (standard library). Three steps:

  1. resolve  Looks every show up on TVMaze (free, no key) and writes a list with
              season and episode numbers plus the show's IMDb and TVDB IDs:
                Show | S05E10 | Episode title | tt0108778 | 79168
              This file is useful on its own for any media server tool.
  2. probe    Tries a few ways of sending ONE episode to MDBList and prints what the
              API answers, because MDBList's episode request format is not documented
              anywhere I could read. Run it once, then use the format that says added: 1.
  3. upload   Sends everything in the resolved file to your MDBList list.

Your MDBList API key goes in an environment variable, never on the command line:

  export MDBLIST_API_KEY=...        (from https://mdblist.com/preferences/)

Examples:
  python3 christmas_to_mdblist_0.1.0.py resolve lists/christmas.txt -o christmas_resolved.txt
  python3 christmas_to_mdblist_0.1.0.py probe christmas_resolved.txt --list-id 12345
  python3 christmas_to_mdblist_0.1.0.py upload christmas_resolved.txt --list-id 12345 --format B
  python3 christmas_to_mdblist_0.1.0.py upload christmas_resolved.txt --list-id 12345 --shows-only
"""
import argparse, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

VERSION = "0.1.0"
UA = "silo-seasonal-lists-tool/" + VERSION
TVMAZE = "https://api.tvmaze.com"
MDBLIST = "https://api.mdblist.com"


def http_json(url, body=None, retries=4):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code == 429 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text[:500]
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise SystemExit("Network problem reaching %s: %s" % (urllib.parse.urlparse(url).netloc, e.reason))
            time.sleep(2)


def norm(s):
    s = unicodedata.normalize("NFKD", s).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    words = s.split()
    if len(words) > 1 and words[0] in ("the", "a", "an"):
        words = words[1:]
    return "".join(words)


def parse_list(path):
    out = []
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and parts[0]:
            out.append((parts[0], " | ".join(parts[1:])))
    return out


def split_show(name):
    """'Animaniacs (1993)' -> ('Animaniacs', 1993); '(reboot)' style notes are dropped."""
    m = re.search(r"\s*\(([^)]*)\)\s*$", name)
    if not m:
        return name, None
    year = re.search(r"(19|20)\d\d", m.group(1))
    return name[:m.start()].strip(), int(year.group(0)) if year else None


def pick_show(results, base, year, reboot):
    want = norm(base)
    best, best_score = None, -1
    for r in results:
        s = r["show"]
        if norm(s.get("name") or "") != want:
            continue
        prem = (s.get("premiered") or "")[:4]
        score = 1
        if year and prem == str(year):
            score += 10
        if reboot and prem and int(prem) >= 2010:
            score += 5
        score += r.get("score", 0) / 100.0
        if score > best_score:
            best, best_score = s, score
    return best


def find_show(name, cache):
    key = "show:" + name
    if key in cache:
        return cache[key]
    base, year = split_show(name)
    reboot = "reboot" in name.lower()
    _, res = http_json("%s/search/shows?q=%s" % (TVMAZE, urllib.parse.quote(base)))
    time.sleep(0.6)
    show = pick_show(res, base, year, reboot) if isinstance(res, list) else None
    if show is None and norm(base).startswith("the") is False:
        _, res = http_json("%s/search/shows?q=%s" % (TVMAZE, urllib.parse.quote("The " + base)))
        time.sleep(0.6)
        show = pick_show(res, "The " + base, year, reboot) if isinstance(res, list) else None
    info = None
    if show:
        ext = show.get("externals") or {}
        _, eps = http_json("%s/shows/%d/episodes?specials=1" % (TVMAZE, show["id"]))
        time.sleep(0.6)
        info = {"name": show["name"], "imdb": ext.get("imdb"), "tvdb": ext.get("thetvdb"),
                "episodes": [[e["season"], e["number"], e["name"]] for e in (eps or []) if e.get("number")]}
    cache[key] = info
    return info


def match_episode(info, title):
    want = norm(title)
    parts = [norm(p) for p in title.split("/")] if "/" in title else []
    for s, n, name in info["episodes"]:
        if norm(name) == want or (parts and norm(name) in parts):
            return s, n, name
    return None


def cmd_resolve(a):
    cache_path = a.output + ".cache.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    entries = parse_list(a.list)
    rows, missed_show, missed_ep = [], [], []
    for i, (show, title) in enumerate(entries, 1):
        m = re.fullmatch(r"[sS](\d+)[eE](\d+)(?:\s*\|\s*(.*))?", title)
        info = find_show(show, cache)
        if i % 25 == 0:
            json.dump(cache, open(cache_path, "w"))
            print("  %d / %d lines" % (i, len(entries)), file=sys.stderr)
        if not info:
            missed_show.append("%s | %s" % (show, title))
            continue
        if m:
            found = (int(m.group(1)), int(m.group(2)), m.group(3) or "")
        else:
            found = match_episode(info, title)
        if not found:
            missed_ep.append("%s | %s" % (show, title))
            continue
        rows.append("%s | S%02dE%02d | %s | %s | %s" % (show, found[0], found[1], found[2] or title, info["imdb"] or "", info["tvdb"] or ""))
    json.dump(cache, open(cache_path, "w"))
    with open(a.output, "w", encoding="utf-8") as f:
        f.write("# Show | SxxEyy | Episode title | show IMDb ID | show TVDB ID\n" + "\n".join(rows) + "\n")
    with open(a.output + ".unmatched.txt", "w", encoding="utf-8") as f:
        f.write("# Show not found on TVMaze\n" + "\n".join(missed_show) + "\n\n# Show found, episode title not matched\n" + "\n".join(missed_ep) + "\n")
    print("Resolved %d of %d lines. %d shows not found, %d episodes not matched (see %s.unmatched.txt)." % (len(rows), len(entries), len(missed_show), len(missed_ep), a.output))


def read_resolved(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        p = [x.strip() for x in line.split("|")]
        m = re.fullmatch(r"S(\d+)E(\d+)", p[1]) if len(p) >= 5 else None
        if m and p[3]:
            rows.append({"show": p[0], "season": int(m.group(1)), "episode": int(m.group(2)), "imdb": p[3], "tvdb": p[4]})
    return rows


def body_for(fmt, rows):
    """Candidate request bodies. MDBList documents movies and shows by IMDb/TMDB ID; the
    episode layout below is a best guess, which is why `probe` exists."""
    if fmt == "A":
        return {"episodes": [{"imdb": r["imdb"], "season": r["season"], "episode": r["episode"]} for r in rows]}
    if fmt == "B":
        return {"episodes": [{"show": {"imdb": r["imdb"]}, "season": r["season"], "episode": r["episode"]} for r in rows]}
    if fmt == "C":
        shows = {}
        for r in rows:
            shows.setdefault(r["imdb"], {}).setdefault(r["season"], []).append({"episode": r["episode"]})
        return {"shows": [{"imdb": i, "seasons": [{"season": s, "episodes": e} for s, e in sorted(ss.items())]} for i, ss in shows.items()]}
    if fmt == "shows":
        return {"shows": [{"imdb": i} for i in dict.fromkeys(r["imdb"] for r in rows)]}
    raise SystemExit("unknown format " + fmt)


def send(list_id, body):
    key = os.environ.get("MDBLIST_API_KEY")
    if not key:
        raise SystemExit("Set MDBLIST_API_KEY first: export MDBLIST_API_KEY=...")
    url = "%s/lists/%d/items/add?apikey=%s" % (MDBLIST, list_id, urllib.parse.quote(key))
    return http_json(url, body)


def cmd_probe(a):
    rows = read_resolved(a.resolved)
    if not rows:
        raise SystemExit("No usable rows (need season, episode and a show IMDb ID).")
    one = rows[:1]
    print("Testing with: %s S%02dE%02d" % (one[0]["show"], one[0]["season"], one[0]["episode"]))
    for fmt in ("A", "B", "C"):
        status, resp = send(a.list_id, body_for(fmt, one))
        print("format %s -> HTTP %s: %s" % (fmt, status, json.dumps(resp)[:300]))
    print("\nUse the format whose answer shows an episode (or season) 'added' or 'existing' of 1, then:\n  upload %s --list-id %d --format <A|B|C>" % (a.resolved, a.list_id))


def cmd_upload(a):
    rows = read_resolved(a.resolved)
    fmt = "shows" if a.shows_only else a.format
    if not fmt:
        raise SystemExit("Choose --format A|B|C (run probe first) or --shows-only.")
    if fmt == "shows":
        batches = [body_for("shows", rows)]
        batches = [{"shows": batches[0]["shows"][i:i + 100]} for i in range(0, len(batches[0]["shows"]), 100)]
    else:
        batches = [body_for(fmt, rows[i:i + 100]) for i in range(0, len(rows), 100)]
    for n, body in enumerate(batches, 1):
        status, resp = send(a.list_id, body)
        print("batch %d/%d -> HTTP %s: %s" % (n, len(batches), status, json.dumps(resp)[:300]))
        if status != 200:
            raise SystemExit("Stopping: MDBList refused this batch.")
        time.sleep(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("list")
    r.add_argument("-o", "--output", default="christmas_resolved.txt")
    pr = sub.add_parser("probe")
    pr.add_argument("resolved")
    pr.add_argument("--list-id", type=int, required=True)
    u = sub.add_parser("upload")
    u.add_argument("resolved")
    u.add_argument("--list-id", type=int, required=True)
    u.add_argument("--format", choices=["A", "B", "C"])
    u.add_argument("--shows-only", action="store_true")
    a = p.parse_args()
    {"resolve": cmd_resolve, "probe": cmd_probe, "upload": cmd_upload}[a.cmd](a)


if __name__ == "__main__":
    main()
