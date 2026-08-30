"""Pre-claim check for family M09: is a derived artifact older than its source.

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
    """A timestamp written inside the artifact (JSON key or ISO date in text), if any."""
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


def ts_before(ts, src_mtime):
    """True when an embedded timestamp is older than the newest source, by date.

    A timestamp that cannot be parsed, or that is on/after the source date, is not
    evidence of staleness."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(ts))
    if not m:
        return False
    try:
        y, mo, d = (int(x) for x in m.groups())
        art_day = time.mktime((y, mo, d, 0, 0, 0, 0, 0, -1))
    except (ValueError, OverflowError):
        return False
    lt = time.localtime(src_mtime)
    src_day = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return art_day < src_day


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    sources = [os.path.join(root, d) for d in SOURCE_DIRS if os.path.isdir(os.path.join(root, d))]
    derived = [os.path.join(root, d) for d in DERIVED_DIRS if os.path.isdir(os.path.join(root, d))]
    if not sources or not derived:
        print("CHECK N/A no derived/source pair found - this check does not apply to this task; ignore it and report the task result plainly.")
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
                if ts and ts_before(ts, src_newest):
                    problems.append(f"{os.path.relpath(p, root)} says it was generated {ts}, before the newest source "
                                    f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(src_newest))}): regenerate or report the source value")
    if problems:
        print("CHECK FAIL stale-derived-artifact: " + " | ".join(problems[:4]))
        print("ACTION: regenerate the derived artifact (e.g. run the build/report script with its refresh flag) or report the SOURCE value and say the report is stale.")
    else:
        print("CHECK OK derived artifacts are newer than their sources")


if __name__ == "__main__":
    main()
