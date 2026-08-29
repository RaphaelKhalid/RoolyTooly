"""Delete all Daytona sandboxes in the org (frees the 30 GiB disk quota).

Usage:  python scripts/purge_sandboxes.py [--dry-run]
Reads DAYTONA_API_KEY from the environment or from a gitignored `.env` file at the repo root.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("DAYTONA_API_URL", "https://app.daytona.io/api")


def load_key() -> str:
    key = os.environ.get("DAYTONA_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DAYTONA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("DAYTONA_API_KEY not set (env or .env)")
    return key


H = {"Authorization": f"Bearer {load_key()}", "Content-Type": "application/json"}


def req(method: str, path: str):
    r = urllib.request.Request(f"{API}{path}", method=method, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "body": e.read().decode(errors="replace")[:300]}


def main() -> None:
    dry = "--dry-run" in sys.argv
    sb = req("GET", "/sandbox")
    if isinstance(sb, dict) and "_error" in sb:
        sys.exit(f"list failed: {sb}")
    items = sb if isinstance(sb, list) else (sb.get("items") or sb.get("data") or [])
    print(f"{len(items)} sandboxes")
    for s in items:
        sid, state, disk = s.get("id"), s.get("state"), s.get("disk")
        print(f"  {sid} state={state} disk={disk}GiB", end=" ")
        if dry:
            print()
            continue
        r = req("DELETE", f"/sandbox/{sid}?force=true")
        print("-> deleted" if not (isinstance(r, dict) and "_error" in r) else f"-> {r}")


if __name__ == "__main__":
    main()
