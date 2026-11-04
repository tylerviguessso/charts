#!/usr/bin/env python3
"""Read charts/usernames.txt, find each Suno user's public playlists and albums,
and append any new ones to charts/albums/albums.txt and charts/playlists.txt.

Uses only the Python standard library. Suno has no official public API, so the
endpoint below is the one Suno's own website uses and it may change.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Paths are relative to the repo root. If your site is the repo named "charts",
# the repo root IS .io/charts/, so these match .io/charts/albums/albums.txt etc.
USERNAMES_CANDIDATES = ["usernames.txt", "charts/usernames.txt"]  # first one that exists is used
ALBUMS = "../albums/albums.txt"
PLAYLISTS = "../playlists.txt"
HOSTS = ["https://studio-api-prod.suno.com", "https://studio-api.prod.suno.com"]
MAX_PAGES = 30
DELAY = 1.5  # seconds between requests, to be polite
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://suno.com",
    "Referer": "https://suno.com/",
}

_host = [0]  # index of the host that last worked
_printed_keys = []


def get_json(path):
    """GET path from the API. Returns parsed JSON, or None if the profile doesn't exist."""
    last = None
    for h in range(len(HOSTS)):
        host = HOSTS[(_host[0] + h) % len(HOSTS)]
        for attempt in range(4):
            try:
                req = urllib.request.Request(host + path, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
                try:
                    data = json.loads(body)
                except ValueError:
                    raise RuntimeError("reply was not JSON (blocked?): " + body[:80].replace("\n", " "))
                _host[0] = (_host[0] + h) % len(HOSTS)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                last = "HTTP %s" % e.code
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(10 * (attempt + 1))
                    continue
                break  # 401/403 etc: try the other host
            except Exception as e:  # network error, bad JSON
                last = str(e)
                time.sleep(3)
        # fall through to the next host
    raise RuntimeError(last or "request failed")


def find_playlists(node):
    """Find the list stored under a 'playlists' key anywhere in the reply."""
    if isinstance(node, dict):
        v = node.get("playlists")
        if isinstance(v, list):
            return v
        for child in node.values():
            found = find_playlists(child)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = find_playlists(child)
            if found is not None:
                return found
    return None


def is_album(p):
    for key in ("is_album", "album"):
        if p.get(key) is True:
            return True
    for key in ("type", "playlist_type", "kind", "category"):
        if str(p.get(key, "")).lower() == "album":
            return True
    return False


def collect(handle):
    """Return a list of (id, is_album, name) for a handle's public playlists/albums."""
    seen, out = set(), []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(
            "/api/profiles/%s?playlists_sort_by=created_at&clips_sort_by=created_at&page=%d" % (handle, page)
        )
        if data is None:
            raise RuntimeError("profile not found")
        items = find_playlists(data) or []
        new = 0
        for item in items:
            p = item.get("playlist", item) if isinstance(item, dict) else None
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id", "")).lower()
            if not UUID.fullmatch(pid) or pid in seen:
                continue
            seen.add(pid)
            new += 1
            if not _printed_keys:
                _printed_keys.append(1)
                print("(playlist fields from Suno: %s)" % ", ".join(sorted(p.keys())))
            out.append((pid, is_album(p), str(p.get("name") or p.get("title") or "")))
        if new == 0:
            break
        time.sleep(DELAY)
    return out


def read_handles():
    path = next((p for p in USERNAMES_CANDIDATES if os.path.exists(p)), None)
    if not path:
        return []
    print("Reading usernames from %s" % path)
    handles = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip().lstrip("@")
        if line and line not in handles:
            handles.append(line)
    return handles


def ids_in(path):
    """Suno IDs already listed (not in # comments) in one file."""
    ids = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if not line.lstrip().startswith("#"):
                ids.update(m.lower() for m in UUID.findall(line))
    return ids


def append_lines(path, lines):
    if not lines:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    prefix = ""
    if os.path.exists(path):
        text = open(path, encoding="utf-8").read()
        if text and not text.endswith("\n"):
            prefix = "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + "\n".join(lines) + "\n")


def main():
    handles = read_handles()
    if not handles:
        print("No usernames found. Nothing to do.")
        return 0
    in_albums, in_playlists = ids_in(ALBUMS), ids_in(PLAYLISTS)
    print("Already listed: %d in %s, %d in %s" % (len(in_albums), ALBUMS, len(in_playlists), PLAYLISTS))
    add_albums, add_playlists, report, ok = [], [], [], 0
    for i, h in enumerate(handles):
        try:
            found = collect(h)
        except Exception as e:
            print("@%s: FAILED (%s)" % (h, e))
            report.append("- @%s: failed (%s)" % (h, e))
            continue
        ok += 1
        added = 0
        print("@%s: %d public playlist(s)/album(s) found" % (h, len(found)))
        for pid, album, name in found:
            url = "https://suno.com/%s/%s" % ("album" if album else "playlist", pid)
            need_a, need_p = pid not in in_albums, pid not in in_playlists
            if need_a:
                add_albums.append(url)
                in_albums.add(pid)
            if need_p:
                add_playlists.append(url)
                in_playlists.add(pid)
            if need_a or need_p:
                added += 1
            where = "NEW" if need_a and need_p else ("NEW in %s only" % (ALBUMS if need_a else PLAYLISTS)) if (need_a or need_p) else "already in both files"
            print("   %s  %s  %s" % (where, url, name))
        print("@%s: %d link(s) added" % (h, added))
        report.append("- @%s: %d found, %d added" % (h, len(found), added))
        if i < len(handles) - 1:
            time.sleep(DELAY)
    append_lines(ALBUMS, add_albums)
    append_lines(PLAYLISTS, add_playlists)
    print("Wrote %d line(s) to %s and %d line(s) to %s" % (len(add_albums), ALBUMS, len(add_playlists), PLAYLISTS))
    summary = "\n".join(["### Suno sync", "", "Added %d line(s) to %s and %d to %s." % (len(add_albums), ALBUMS, len(add_playlists), PLAYLISTS), ""] + report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary + "\n")
    # Fail the run only when every username failed, so a broken fetch shows as red.
    return 1 if ok == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
