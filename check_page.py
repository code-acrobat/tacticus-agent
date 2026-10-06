#!/usr/bin/env python3
"""Static checks for web/index.html and worker/worker.js - no browser, no network.

Run: python3 check_page.py   (needs node on PATH; ~0.1 s)
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def node_check(text, suffix):
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
        f.write(text)
        tmp = f.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    pathlib.Path(tmp).unlink()
    if r.returncode:
        fails.append("node --check (%s): %s" % (suffix, r.stderr.strip()))


page = (ROOT / "web" / "index.html").read_text()

# 1. the one inline script parses
scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
check(len(scripts) == 1, "expected 1 inline <script>, found %d" % len(scripts))
script = scripts[0] if scripts else ""
if script:
    node_check(script, ".js")

# 2. every id the script dereferences exists in the markup
ids = set(re.findall(r"\$\('([\w-]+)'\)", script))
ids |= set(re.findall(r"getElementById\('([\w-]+)'\)", script))
missing = sorted(i for i in ids if 'id="%s"' % i not in page)
check(not missing, "markup missing ids: " + ", ".join(missing))

# 3. markers the shipped page must keep
for marker in (
    'id="tools"',          # what-this-page-ships table
    'id="hunt"',           # Hunt nodes button
    "home_screen_event",  # its CLI reference
    'id="lres"',           # Uthar event button
    "lres_report",         # its CLI reference
    'id="shards"',         # Shard sources button
    'id="shardq"',         # its character input
    "shard_source",        # its CLI reference
    'id="theme"',          # light/dark toggle
    "water.css@2/out/dark.min.css",
    "What this page ships",
    "https://api.tacticusgame.com/",
):
    check(marker in page, "page lost marker: " + marker)

# 4. worker is still a valid ES module
node_check((ROOT / "worker" / "worker.js").read_text(), ".mjs")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("check_page OK (%d ids)" % len(ids))
