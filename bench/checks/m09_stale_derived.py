"""Symbolic pre-claim check for family M09 (stale state): is any derived artifact older than its source?

Run from the project root before reporting a value that comes from a derived artifact
(report, bundle, summary). Prints CHECK OK or CHECK FAIL <reason> and exits 0 either way; the
worker must quote the line and, on FAIL, regenerate the artifact (or say it is stale) instead of
reporting the derived value.
"""
import json
import os
import re
import sys
import time

DERIVED_DIRS = ("report", "reports", "dist", "build", "out", "output")
SOURCE_DIRS = ("data", "src", "lib", "inputs", "metrics")
TS_KEYS = ("computed_at", "generated_at", "generated", "timestamp", "updated_at", "created_at")


def newest(path):
    best = 0.0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                best = max(best, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return best


def embedded_ts(path):
    """A timestamp written INSIDE the artifact (JSON key or an ISO date in text), if any."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read(20000)
    except OSError:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k in TS_KEYS:
                if k in obj:
                    return str(obj[k])
    except json.JSONDecodeError:
        pass
    m = re.search(r"(?i)(generated|computed|updated|as of)[^\n]{0,30}?(\d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2})", text)
    return m.group(2) if m else None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    sources = [os.path.join(root, d) for d in SOURCE_DIRS if os.path.isdir(os.path.join(root, d))]
    derived = [os.path.join(root, d) for d in DERIVED_DIRS if os.path.isdir(os.path.join(root, d))]
    if not sources or not derived:
        print("CHECK OK (no derived/source pair found; nothing to compare)")
        return
    src_newest = max(newest(s) for s in sources)
    problems = []
    for d in derived:
        for r, _, files in os.walk(d):
            for f in files:
                p = os.path.join(r, f)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if m + 1 < src_newest:
                    problems.append(f"{os.path.relpath(p, root)} is older than the newest source "
                                    f"(derived {time.strftime('%H:%M:%S', time.localtime(m))} < source "
                                    f"{time.strftime('%H:%M:%S', time.localtime(src_newest))})")
                ts = embedded_ts(p)
                if ts:
                    problems.append(f"{os.path.relpath(p, root)} carries its own timestamp '{ts}': confirm it matches the source before quoting it")
    if problems:
        print("CHECK FAIL stale-derived-artifact: " + " | ".join(problems[:4]))
        print("ACTION: regenerate the derived artifact (e.g. run the build/report script with its refresh flag) or report the SOURCE value and say the report is stale.")
    else:
        print("CHECK OK derived artifacts are newer than their sources")


if __name__ == "__main__":
    main()
