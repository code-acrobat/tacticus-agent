#!/usr/bin/env python3
"""Refresh tacticus-player.json from the Tacticus API.

Reads the key from .tacticus_api_key, fetches GET /api/v1/player, and
atomically replaces the local cache (write to a .tmp file, then rename, so an
interrupted run never leaves a half-written file behind).

Usage:
    python3 update_player.py                 # refresh the cache
    python3 update_player.py --pretty        # indent the JSON for reading
    python3 update_player.py -o /tmp/x.json  # write elsewhere
    python3 update_player.py --quiet         # no summary output
"""
import argparse
import datetime
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
KEY_FILE = BASE / ".tacticus_api_key"
DEFAULT_OUT = BASE / "tacticus-player.json"
DEFAULT_URL = "https://api.tacticusgame.com/api/v1/player"

HINTS = {
    403: "Forbidden - key missing, invalid, or lacking the scope this endpoint requires.",
    404: "Not found.",
    500: "Server error - retryable, try again shortly.",
}


def fail(message, code=1):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def fetch(url, key):
    request = urllib.request.Request(
        url, headers={"X-API-KEY": key, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace").strip()
        hint = HINTS.get(error.code, "")
        fail(f"HTTP {error.code} from {url}. {hint}\nresponse: {body}")
    except urllib.error.URLError as error:
        fail(f"network error contacting {url}: {error.reason}")
    except TimeoutError:
        fail(f"timeout after 30s contacting {url}")


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def summarise(document, path, size):
    player = document["player"]
    meta = document["metaData"]
    details = player["details"]
    updated = meta.get("lastUpdatedOn") or 0
    when = (
        datetime.datetime.fromtimestamp(updated, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if updated
        else "never"
    )
    units = player["units"]
    top = max(units, key=lambda u: (u["rank"], u["xpLevel"]))
    print(f"wrote {path} ({size:,} bytes)")
    print(f"  player      {details['name']} - power level {details['powerLevel']}")
    print(f"  units       {len(units)}")
    print(f"  top ranked  {top['name']} ({top.get('faction', '?')}) rank {top['rank']}")
    print(f"  server sync {when}")
    print(f"  scopes      {', '.join(meta.get('scopes', []))}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", type=pathlib.Path, default=DEFAULT_OUT,
                        help=f"output file (default: {DEFAULT_OUT.name})")
    parser.add_argument("--url", default=DEFAULT_URL, help="API endpoint to query")
    parser.add_argument("--pretty", action="store_true",
                        help="indent the JSON instead of storing it as served")
    parser.add_argument("--quiet", "-q", action="store_true", help="suppress the summary")
    args = parser.parse_args()

    if not KEY_FILE.exists():
        fail(f"{KEY_FILE} not found - create it with the API key only, no newline")
    key = KEY_FILE.read_text().strip()
    if not key:
        fail(f"{KEY_FILE} is empty")

    raw = fetch(args.url, key)

    if args.pretty:
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            fail(f"response was not valid JSON: {error}")
        raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()

    write_atomic(args.out, raw)

    if not args.quiet:
        if args.pretty:
            document = json.loads(raw)
        else:
            try:
                document = json.loads(raw)
            except json.JSONDecodeError:
                document = None
        if document and "player" in document and "metaData" in document:
            summarise(document, args.out, len(raw))
        else:
            print(f"wrote {args.out} ({len(raw):,} bytes) - unexpected shape, no summary")


if __name__ == "__main__":
    main()
