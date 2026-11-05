#!/usr/bin/env python3
"""Turn lists/christmas.txt into season/episode numbers and upload it to an MDBList static list.

Needs only Python 3 (standard library). Three steps:

  1. resolve  Looks every show up on TVMaze (free, no key) and writes a list with
              season and episode numbers plus the show's IMDb and TVDB IDs:
                Show | S05E10 | Episode title | tt0108778 | 79168
              This file is useful on its own for any media server tool.
  2. probe    Tries a few ways of sending ONE episode to MDBList and prints what the
              API answers. Run it once, then use the format that says added or existing: 1.
  3. upload   Sends everything in the resolved file to your MDBList list.

Your MDBList API key goes in an environment variable, never on the command line:

  export MDBLIST_API_KEY=...        (from https://mdblist.com/preferences/)

Examples:
  python3 christmas_to_mdblist_0.3.0.py resolve lists/christmas.txt -o christmas_resolved.txt
  python3 christmas_to_mdblist_0.3.0.py probe christmas_resolved.txt --list-id 12345
  python3 christmas_to_mdblist_0.3.0.py upload christmas_resolved.txt --list-id 12345 --format D
  python3 christmas_to_mdblist_0.3.0.py upload christmas_resolved.txt --list-id 12345 --shows-only
"""
import argparse, difflib, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

VERSION = "0.3.0"
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
    s = unicodedata.normalize("NFKD", s).lower().replace("&", " and ")
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


def candidates(name, cache):
    """TVMaze shows that could be the one meant: exact name first, then similar names."""
    base, year = split_show(name)
    want = norm(base)
    seen, out = set(), []
    queries = [base, "The " + base if not want.startswith("the") else base[4:]]
    head = re.split(r"[,:(]| aka ", base)[0].strip()
    if head and head != base:
        queries.append(head)
    for q in queries:
        key = "q2:" + q.lower()
        if key not in cache:
            _, res = http_json("%s/search/shows?q=%s" % (TVMAZE, urllib.parse.quote(q)))
            cache[key] = res if isinstance(res, list) else []
            time.sleep(0.6)
        for r in cache[key]:
            sh = r["show"]
            n = norm(sh.get("name") or "")
            exact = n == want
            similar = len(want) >= 5 and (want in n or n in want) and min(len(n), len(want)) >= 0.5 * max(len(n), len(want))
            if sh["id"] in seen or not (exact or similar):
                continue
            seen.add(sh["id"])
            prem = (sh.get("premiered") or "")[:4]
            rank = (2 if exact else 0) + (3 if year and prem == str(year) else 0) + r.get("score", 0) / 100.0
            out.append((rank, exact, sh))
        if any(e for _, e, _ in out):
            break
    out.sort(key=lambda t: -t[0])
    return out[:4]


def episodes_of(show, cache):
    key = "eps2:%d" % show["id"]
    if key not in cache:
        _, eps = http_json("%s/shows/%d/episodes?specials=1" % (TVMAZE, show["id"]))
        time.sleep(0.6)
        cache[key] = [[e["season"], e["number"], e["name"]] for e in (eps or []) if isinstance(e, dict) and e.get("number")]
    return cache[key]


def title_equal(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 10 and short in long_ and len(short) >= 0.8 * len(long_):
        return True
    return len(short) >= 8 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.92


def match_episode(eps, title):
    parts = [p for p in title.split("/")] if "/" in title else []
    for s, n, name in eps:
        if title_equal(name, title) or any(title_equal(name, p) for p in parts):
            return s, n, name
    return None


def pick_show(name, titles, cache):
    """Choose the TVMaze show whose episodes match the most list titles for this show name.
    This separates a show from its revival or remake (Doctor Who, Scrubs, Ghosts)."""
    best = None
    for rank, exact, show in candidates(name, cache):
        eps = episodes_of(show, cache)
        hits = sum(1 for t in titles if match_episode(eps, t))
        if not exact and hits == 0:
            continue  # a similar name is only trusted when its episodes agree
        score = hits * 100 + rank
        if best is None or score > best[0]:
            best = (score, show, eps, hits)
        if exact and titles and hits >= 0.8 * len(titles):
            break
    return best


def cmd_resolve(a):
    cache_path = a.output + ".cache.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    entries = parse_list(a.list)
    by_show = {}
    for show, title in entries:
        by_show.setdefault(show, []).append(title)
    chosen = {}
    for i, (show, titles) in enumerate(by_show.items(), 1):
        plain = [t for t in titles if not re.match(r"[sS]\d+[eE]\d+", t)]
        chosen[show] = pick_show(show, plain, cache)
        if i % 20 == 0:
            json.dump(cache, open(cache_path, "w"))
            print("  %d / %d shows" % (i, len(by_show)), file=sys.stderr)
    json.dump(cache, open(cache_path, "w"))
    rows, missed_show, missed_ep = [], [], []
    for show, title in entries:
        pick = chosen[show]
        if not pick:
            missed_show.append("%s | %s" % (show, title))
            continue
        _, sh, eps, _ = pick
        m = re.fullmatch(r"[sS](\d+)[eE](\d+)(?:\s*\|\s*(.*))?", title)
        found = (int(m.group(1)), int(m.group(2)), m.group(3) or "") if m else match_episode(eps, title)
        if not found:
            missed_ep.append("%s | %s   [TVMaze show used: %s, %d episodes]" % (show, title, sh["name"], len(eps)))
            continue
        ext = sh.get("externals") or {}
        rows.append("%s | S%02dE%02d | %s | %s | %s" % (show, found[0], found[1], found[2] or title, ext.get("imdb") or "", ext.get("thetvdb") or ""))
    with open(a.output, "w", encoding="utf-8") as f:
        f.write("# Show | SxxEyy | Episode title | show IMDb ID | show TVDB ID\n" + "\n".join(rows) + "\n")
    with open(a.output + ".unmatched.txt", "w", encoding="utf-8") as f:
        f.write("# Show not found on TVMaze\n" + "\n".join(missed_show) + "\n\n# Show found, episode title not matched\n" + "\n".join(missed_ep) + "\n")
    print("Resolved %d of %d lines. %d lines for shows not found, %d episodes not matched (see %s.unmatched.txt)." % (len(rows), len(entries), len(missed_show), len(missed_ep), a.output))


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


def mdb_key():
    key = os.environ.get("MDBLIST_API_KEY")
    if not key:
        raise SystemExit("Set MDBLIST_API_KEY first: export MDBLIST_API_KEY=...")
    return urllib.parse.quote(key)


def attach_tmdb_ids(rows, cache_path):
    """MDBList identifies episodes through the show's TMDB id, which TVMaze does not give.
    Ask MDBList to convert each show's IMDb (or TVDB) id to a TMDB id."""
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    wanted = {}
    for r in rows:
        key = "imdb:" + r["imdb"]
        wanted[key] = ("imdb", r["imdb"])
        r["_key"] = key
    todo = [(k, v) for k, v in wanted.items() if k not in cache]
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        status, resp = http_json("%s/imdb/show?apikey=%s" % (MDBLIST, mdb_key()), {"ids": [v[1] for _, v in chunk]})
        found = {}
        if status == 200 and isinstance(resp, list):
            for info in resp:
                ids = (info or {}).get("ids") or {}
                if ids.get("imdb") and ids.get("tmdb"):
                    found[ids["imdb"]] = ids["tmdb"]
        for k, (_, imdb) in chunk:
            cache[k] = found.get(imdb)
        print("  looked up %d / %d shows" % (min(i + 50, len(todo)), len(todo)), file=sys.stderr)
        time.sleep(1)
    json.dump(cache, open(cache_path, "w"))
    out = []
    for r in rows:
        t = cache.get(r["_key"])
        if t:
            out.append(dict(r, tmdb=t))
    return out


def body_for(fmt, rows):
    """Candidate request bodies for episodes. Rows need a show TMDB id (see attach_tmdb_ids)."""
    if fmt == "D":
        return {"episodes": [{"tmdb": r["tmdb"], "season": r["season"], "episode": r["episode"]} for r in rows]}
    if fmt == "E":
        return {"episodes": [{"show": {"tmdb": r["tmdb"]}, "season": r["season"], "episode": r["episode"]} for r in rows]}
    if fmt == "F":
        return {"episodes": [{"show_id": r["tmdb"], "season_number": r["season"], "episode_number": r["episode"]} for r in rows]}
    if fmt == "shows":
        return {"shows": [{"imdb": i} for i in dict.fromkeys(r["imdb"] for r in rows)]}
    raise SystemExit("unknown format " + fmt)


def send(list_id, body):
    url = "%s/lists/%d/items/add?apikey=%s" % (MDBLIST, list_id, mdb_key())
    return http_json(url, body)


def cmd_probe(a):
    rows = attach_tmdb_ids(read_resolved(a.resolved), a.resolved + ".tmdb.json")
    if not rows:
        raise SystemExit("No usable rows (need season, episode and a show IMDb ID that MDBList knows).")
    # Prefer an episode that is already in the list, so a correct format answers "existing: 1".
    _, cur = http_json("%s/lists/%d/items?apikey=%s" % (MDBLIST, a.list_id, mdb_key()))
    pick = rows[0]
    for e in (cur or {}).get("episodes", []) if isinstance(cur, dict) else []:
        for r in rows:
            if r["season"] == e.get("season_number") and r["episode"] == e.get("episode_number") and norm(r["show"]) == norm(e.get("parent_title") or ""):
                pick = r
                break
    print("Testing with: %s S%02dE%02d (show TMDB id %s)" % (pick["show"], pick["season"], pick["episode"], pick["tmdb"]))
    for fmt in ("D", "E", "F"):
        status, resp = send(a.list_id, body_for(fmt, [pick]))
        print("format %s -> HTTP %s: %s" % (fmt, status, json.dumps(resp)[:300]))
    print("\nUse the format whose answer shows an episode 'added' or 'existing' of 1, then:\n  upload %s --list-id %d --format <D|E|F>" % (a.resolved, a.list_id))


def cmd_upload(a):
    rows = read_resolved(a.resolved)
    fmt = "shows" if a.shows_only else a.format
    if not fmt:
        raise SystemExit("Choose --format D|E|F (run probe first) or --shows-only.")
    if fmt != "shows":
        rows = attach_tmdb_ids(rows, a.resolved + ".tmdb.json")
    if fmt == "shows":
        shows = body_for("shows", rows)["shows"]
        batches = [{"shows": shows[i:i + 100]} for i in range(0, len(shows), 100)]
    else:
        batches = [body_for(fmt, rows[i:i + 100]) for i in range(0, len(rows), 100)]
    totals = {}
    for n, body in enumerate(batches, 1):
        status, resp = send(a.list_id, body)
        print("batch %d/%d -> HTTP %s: %s" % (n, len(batches), status, json.dumps(resp)[:300]))
        if status != 200:
            raise SystemExit("Stopping: MDBList refused this batch.")
        for kind in ("added", "existing", "not_found"):
            for k, v in ((resp or {}).get(kind) or {}).items():
                totals[(kind, k)] = totals.get((kind, k), 0) + v
        time.sleep(1)
    print("Totals: " + ", ".join("%s %s=%d" % (k[0], k[1], v) for k, v in sorted(totals.items()) if v))


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
    u.add_argument("--format", choices=["D", "E", "F"])
    u.add_argument("--shows-only", action="store_true")
    a = p.parse_args()
    {"resolve": cmd_resolve, "probe": cmd_probe, "upload": cmd_upload}[a.cmd](a)


if __name__ == "__main__":
    main()
