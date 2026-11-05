#!/usr/bin/env python3
"""Turn lists/christmas.txt into season/episode numbers and upload it to an MDBList static list.

Needs only Python 3 (standard library). Three steps:

  1. resolve  Looks every show up on TVMaze (free, no key) and writes a list with
              season and episode numbers plus the show's IMDb and TVDB IDs:
                Show | S05E10 | Episode title | tt0108778 | 79168
              This file is useful on its own for any media server tool.
  2. probe    Tests ways of adding ONE known episode to an EMPTY list, prints what really landed
              in the list, and removes it again. Use the format whose result is correct.
  3. upload   Sends everything in the resolved file to an EMPTY MDBList list, after checking
              with one episode that the right one lands. Format G also needs a free TMDB key:
                export TMDB_API_KEY=...

Your MDBList API key goes in an environment variable, never on the command line:

  export MDBLIST_API_KEY=...        (from https://mdblist.com/preferences/)

  4. check    Compares the resolved file with the list and explains every missing row.

Examples:
  python3 christmas_to_mdblist_0.5.0.py check christmas_resolved.txt --list-id 12345
  python3 christmas_to_mdblist_0.5.0.py resolve lists/christmas.txt -o christmas_resolved.txt
  python3 christmas_to_mdblist_0.5.0.py probe christmas_resolved.txt --list-id 12345
  python3 christmas_to_mdblist_0.5.0.py upload christmas_resolved.txt --list-id 12345 --format G
  python3 christmas_to_mdblist_0.5.0.py upload christmas_resolved.txt --list-id 12345 --format shows
"""
import argparse, difflib, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

VERSION = "0.5.0"
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
    todo = [(k, v) for k, v in wanted.items() if not cache.get(k)]  # retry earlier misses
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        status, resp = http_json("%s/imdb/show?apikey=%s" % (MDBLIST, mdb_key()), {"ids": [v[1] for _, v in chunk]})
        found = {}
        if status == 200 and isinstance(resp, list):
            for info in resp:
                ids = (info or {}).get("ids") or {}
                if ids.get("imdb") and ids.get("tmdb"):
                    found[ids["imdb"]] = ids["tmdb"]
        if status != 200:
            print("  warning: MDBList show lookup failed (HTTP %s); these will be retried next run" % status, flush=True)
            continue
        for k, (_, imdb) in chunk:
            cache[k] = found.get(imdb)
        print("  MDBList show lookup: %d / %d" % (min(i + 50, len(todo)), len(todo)), flush=True)
        time.sleep(1)
    json.dump(cache, open(cache_path, "w"))
    unknown = sorted({r["show"] for r in rows if not cache.get(r["_key"])})
    if unknown:
        print("MDBList could not give a TMDB id for %d shows: %s" % (len(unknown), ", ".join(unknown[:15]) + (" ..." if len(unknown) > 15 else "")), flush=True)
    out = []
    for r in rows:
        t = cache.get(r["_key"])
        if t:
            out.append(dict(r, tmdb=t))
    return out


def send(list_id, body, action="add"):
    return http_json("%s/lists/%d/items/%s?apikey=%s" % (MDBLIST, list_id, action, mdb_key()), body)


def list_items(list_id, offset=0):
    status, data = http_json("%s/lists/%d/items?apikey=%s&limit=1000&offset=%d" % (MDBLIST, list_id, mdb_key(), offset))
    if status != 200 or not isinstance(data, dict):
        raise SystemExit("Could not read list %d: HTTP %s %s" % (list_id, status, str(data)[:200]))
    return data


def list_all(list_id):
    """Every item in the list, following MDBList's paging (1000 per page)."""
    out = {"episodes": [], "shows": [], "movies": [], "seasons": []}
    offset = 0
    while True:
        data = list_items(list_id, offset)
        for k in out:
            out[k] += data.get(k) or []
        pg = data.get("pagination") or {}
        if not pg.get("has_more"):
            return out
        offset += int(pg.get("limit") or 1000)
        print("  read %d items so far..." % sum(len(v) for v in out.values()), flush=True)


def describe(data):
    out = ["%s S%02dE%02d \"%s\" (show TMDB %s, episode id %s)" % (e.get("parent_title"), e.get("season_number") or 0, e.get("episode_number") or 0, e.get("title"), e.get("show_id"), e.get("id")) for e in data.get("episodes", [])]
    out += ["WHOLE SHOW: %s" % sh.get("title") for sh in data.get("shows", [])]
    out += ["MOVIE: %s" % m.get("title") for m in data.get("movies", [])]
    return out


def is_empty(data):
    return not (data.get("episodes") or data.get("shows") or data.get("movies") or data.get("seasons"))


TMDB_EPISODES = {}
TMDB_WARNED = set()


def tmdb_episode_id(show_tmdb, season, episode):
    """TMDB's own episode id, needed by MDBList. Needs a free TMDB API key (v3) in TMDB_API_KEY."""
    key = os.environ.get("TMDB_API_KEY")
    if not key:
        raise SystemExit("Set TMDB_API_KEY first (free, from themoviedb.org > Settings > API): export TMDB_API_KEY=...")
    if (show_tmdb, season) not in TMDB_EPISODES:
        for attempt in range(4):
            status, data = http_json("https://api.themoviedb.org/3/tv/%s/season/%d?api_key=%s" % (show_tmdb, season, urllib.parse.quote(key)))
            time.sleep(0.1)
            if status in (200, 404):
                break
            time.sleep(2 * (attempt + 1))
        if status == 401:
            raise SystemExit("TMDB refused the API key (HTTP 401). Check TMDB_API_KEY is the v3 'API Key', not the read access token.")
        if status not in (200, 404) and status not in TMDB_WARNED:
            TMDB_WARNED.add(status)
            print("  warning: TMDB answered HTTP %s for some lookups" % status, flush=True)
        n = len(TMDB_EPISODES) + 1
        if n % 25 == 0:
            print("  TMDB lookups: %d shows/seasons done" % n, flush=True)
        if status not in (200, 404):
            return "error"  # not cached, so a later call can try again
        TMDB_EPISODES[(show_tmdb, season)] = {e["episode_number"]: e["id"] for e in data.get("episodes", [])} if status == 200 and isinstance(data, dict) else {}
    return TMDB_EPISODES[(show_tmdb, season)].get(episode)


SKIPPED = {"no_tmdb_episode": [], "tmdb_error": []}


def body_for(fmt, rows):
    """fmt G: episodes by TMDB episode id (needs TMDB_API_KEY).  fmt H: show + season + episode."""
    if fmt == "G":
        ids = []
        for r in rows:
            i = tmdb_episode_id(r["tmdb"], r["season"], r["episode"])
            if i == "error":
                SKIPPED["tmdb_error"].append(r)
            elif not i:
                SKIPPED["no_tmdb_episode"].append(r)
            else:
                ids.append(i)
        return {"episodes": [{"tmdb": i} for i in ids]}
    if fmt == "H":
        shows = {}
        for r in rows:
            shows.setdefault(r["tmdb"], {}).setdefault(r["season"], []).append({"episode": r["episode"]})
        return {"shows": [{"tmdb": t, "seasons": [{"season": s, "episodes": e} for s, e in sorted(ss.items())]} for t, ss in shows.items()]}
    if fmt == "shows":
        return {"shows": [{"imdb": i} for i in dict.fromkeys(r["imdb"] for r in rows)]}
    raise SystemExit("unknown format " + fmt)


def cmd_probe(a):
    """Tests layouts with ONE known episode (Bob's Burgers S08E06) on an EMPTY list, shows what
    actually landed in the list, then removes it again."""
    if not is_empty(list_items(a.list_id)):
        raise SystemExit("Use an EMPTY list for probe (it removes what it adds). List %d has items." % a.list_id)
    tests = {
        "G (episode by TMDB id)": {"episodes": [{"tmdb": 1404187}]},
        "H (show + season + episode)": {"shows": [{"tmdb": 32726, "seasons": [{"season": 8, "episodes": [{"episode": 6}]}]}]},
        "J (episode with show_tmdb)": {"episodes": [{"show_tmdb": 32726, "season": 8, "episode": 6}]},
    }
    print("Expected result: Bob's Burgers S08E06 \"The Bleakening (1)\" and nothing else\n")
    for name, body in tests.items():
        status, resp = send(a.list_id, body)
        data = list_items(a.list_id)
        print("format %s -> HTTP %s: %s" % (name, status, json.dumps(resp)[:200]))
        print("   list now contains: %s" % (describe(data) or ["nothing"]))
        send(a.list_id, body, "remove")
        left = list_items(a.list_id)
        if not is_empty(left):
            print("   (could not remove it automatically; please clear the list on mdblist.com before the next test)")
            break
    print("\nA format is right only if its 'list now contains' line is exactly the Bob's Burgers episode.")


def cmd_upload(a):
    rows = read_resolved(a.resolved)
    if a.format != "shows":
        rows = attach_tmdb_ids(rows, a.resolved + ".tmdb.json")
    if not is_empty(list_items(a.list_id)) and not a.allow_nonempty:
        raise SystemExit("List %d is not empty. Use a new empty list (or add --allow-nonempty)." % a.list_id)
    if a.format != "shows":
        # Safety check: add one episode and confirm the right one landed before continuing.
        r = rows[0]
        body = body_for(a.format, [r])
        if not body.get("episodes") and not body.get("shows"):
            raise SystemExit("Could not build the first request (no TMDB episode id).")
        send(a.list_id, body)
        data = list_items(a.list_id)
        ok = any(e.get("show_id") == r["tmdb"] and e.get("season_number") == r["season"] and e.get("episode_number") == r["episode"] for e in data.get("episodes", []))
        if not ok or data.get("shows"):
            send(a.list_id, body, "remove")
            raise SystemExit("Safety check failed: after adding %s S%02dE%02d the list contained %s. Nothing else was sent; the test item was removed." % (r["show"], r["season"], r["episode"], describe(data)))
        print("Safety check passed: %s" % describe(data)[0])
    if a.format == "shows":
        shows = body_for("shows", rows)["shows"]
        batches = [{"shows": shows[i:i + 100]} for i in range(0, len(shows), 100)]
    else:
        batches = []
        print("Looking up TMDB episode ids for %d episodes (this is the slow part)..." % len(rows), flush=True)
        for i in range(0, len(rows), 100):
            b = body_for(a.format, rows[i:i + 100])
            print("  prepared batch %d/%d (%d episodes found on TMDB)" % (i // 100 + 1, (len(rows) + 99) // 100, len(b.get("episodes", []))), flush=True)
            if b.get("episodes") or b.get("shows"):
                batches.append(b)
    totals = {}
    for n, body in enumerate(batches, 1):
        status, resp = send(a.list_id, body)
        print("batch %d/%d -> HTTP %s" % (n, len(batches), status))
        if status != 200:
            raise SystemExit("Stopping: MDBList refused this batch: %s" % str(resp)[:300])
        for kind in ("added", "existing", "not_found"):
            for k, v in ((resp or {}).get(kind) or {}).items():
                totals[(kind, k)] = totals.get((kind, k), 0) + v
        time.sleep(1)
    print("Totals: " + ", ".join("%s %s=%d" % (k[0], k[1], v) for k, v in sorted(totals.items()) if v))
    for key, label in (("no_tmdb_episode", "TMDB has no such season/episode"), ("tmdb_error", "TMDB lookup failed (re-run with --allow-nonempty to retry)")):
        if SKIPPED[key]:
            print("Skipped %d rows: %s. Shows: %s" % (len(SKIPPED[key]), label, ", ".join(sorted({r["show"] for r in SKIPPED[key]})[:15])))
    print("Reading the whole list back to count it...", flush=True)
    data = list_all(a.list_id)
    print("List now has %d episodes and %d whole shows. First few:" % (len(data.get("episodes", [])), len(data.get("shows", []))))
    for line in describe(data)[:8]:
        print("  " + line)


def cmd_check(a):
    """Compare the resolved file with the list and explain every row that is missing."""
    rows = read_resolved(a.resolved)
    print("Reading list %d..." % a.list_id, flush=True)
    data = list_all(a.list_id)
    have = {(e.get("show_id"), e.get("season_number"), e.get("episode_number")) for e in data["episodes"]}
    print("List holds %d episodes. Checking %d rows (cached lookups are used)..." % (len(data["episodes"]), len(rows)), flush=True)
    cache_path = a.resolved + ".tmdb.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    reasons = {}
    for r in rows:
        tm = cache.get("imdb:" + r["imdb"]) if r["imdb"] else None
        if not r["imdb"]:
            why = "show has no IMDb id in TVMaze"
        elif not tm:
            why = "MDBList gave no TMDB id for the show (or lookup never ran)"
        elif (tm, r["season"], r["episode"]) in have:
            continue
        else:
            eid = tmdb_episode_id(tm, r["season"], r["episode"]) if os.environ.get("TMDB_API_KEY") else None
            why = ("TMDB has no such season/episode" if eid is None and os.environ.get("TMDB_API_KEY") else
                   "TMDB lookup failed" if eid == "error" else
                   "TMDB has it but it is not in the list" if eid else "not checked (set TMDB_API_KEY to learn why)")
        reasons.setdefault(why, {}).setdefault(r["show"], []).append("S%02dE%02d" % (r["season"], r["episode"]))
    total = sum(len(v) for d in reasons.values() for v in d.values())
    print("\n%d of %d rows are not in the list." % (total, len(rows)))
    for why, shows in sorted(reasons.items(), key=lambda kv: -sum(len(v) for v in kv[1].values())):
        print("\n%s: %d rows" % (why, sum(len(v) for v in shows.values())))
        for show, eps in sorted(shows.items(), key=lambda kv: -len(kv[1]))[:12]:
            print("   %s (%s)" % (show, ", ".join(eps[:6]) + (" ..." if len(eps) > 6 else "")))


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
    c = sub.add_parser("check")
    c.add_argument("resolved")
    c.add_argument("--list-id", type=int, required=True)
    u = sub.add_parser("upload")
    u.add_argument("resolved")
    u.add_argument("--list-id", type=int, required=True)
    u.add_argument("--format", choices=["G", "H", "shows"], required=True)
    u.add_argument("--allow-nonempty", action="store_true")
    a = p.parse_args()
    {"resolve": cmd_resolve, "probe": cmd_probe, "upload": cmd_upload, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
