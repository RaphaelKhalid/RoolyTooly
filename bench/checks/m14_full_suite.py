"""Pre-claim check for family M14: run every test, not just the one that was fixed.

Discovers every tests/test_*.py, runs each as its own process, and if the project ships a
runner (run_tests.py) also runs it with --all when supported. Prints CHECK OK or CHECK FAIL
with the failing files; exits 0 either way. The worker must quote the line and must not call
the suite green while any file fails.
"""
import glob
import os
import subprocess
import sys


def run(cmd, cwd):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr)[-800:]
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(glob.glob(os.path.join(root, "tests", "test_*.py")))
    failures = []
    runner_code = (
        "import importlib.util,sys,traceback;spec=importlib.util.spec_from_file_location('t',sys.argv[1]);"
        "m=importlib.util.module_from_spec(spec);sys.path.insert(0,'.');spec.loader.exec_module(m);"
        "fns=[getattr(m,n) for n in dir(m) if n.startswith('test_') and callable(getattr(m,n))];bad=0\n"
        "for fn in fns:\n    try: fn()\n    except Exception as e: bad+=1; print('FAIL', fn.__name__, type(e).__name__, e)\n"
        "print('RAN', len(fns), 'FAILED', bad); sys.exit(1 if bad or not fns else 0)")
    for f in files:
        code, out = run([sys.executable, "-c", runner_code, f], root)  # runs every test_* function, pytest-free
        if code != 0:
            failures.append(f"{os.path.relpath(f, root)} exit {code}: {out.strip().splitlines()[-1] if out.strip() else ''}")
    runner = os.path.join(root, "run_tests.py")
    runner_note = ""
    if os.path.exists(runner):
        code, out = run([sys.executable, runner, "--all"], root)
        runner_note = f" | run_tests.py --all exit {code}: {out.strip().splitlines()[-1] if out.strip() else ''}"
        if code != 0 or ("FAIL" in out.upper() and "PASS" in out.upper()):
            failures.append(f"run_tests.py --all did not pass cleanly (exit {code})")
    if not files and not runner_note:
        print("CHECK N/A no tests/ directory found - this check does not apply; it says nothing about whether the task is done.")
        return
    if failures:
        print(f"CHECK FAIL {len(failures)} of {len(files)} test files fail: " + " ; ".join(failures[:4]) + runner_note)
        print("ACTION: the suite is NOT green. Fix the regression or report exactly which files fail.")
    else:
        print(f"CHECK OK all {len(files)} test files pass" + runner_note)


if __name__ == "__main__":
    main()
