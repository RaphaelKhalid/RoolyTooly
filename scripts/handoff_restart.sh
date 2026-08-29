#!/usr/bin/env bash
# Wait until no eval-runner job is running, then restart the eval server (fresh code) and the
# self-improvement loop. Run from the repo root in Git Bash on Windows.
set -u
cd "$(dirname "$0")/.."
echo "$(date +%H:%M:%S) waiting for running jobs to finish"
for i in $(seq 1 60); do
  running=$(python -c "import json;j=json.load(open('results/jobs.json'));print(sum(1 for v in j.values() if v.get('status')=='running'))" 2>/dev/null || echo 1)
  [ "$running" = "0" ] && break
  sleep 20
done
echo "$(date +%H:%M:%S) running=$running; stopping loop and eval server"
pkill -f "harness.self_improve" 2>/dev/null || true
wsl -d Ubuntu -e bash -lc 'for p in $(pgrep -f eval_server.py); do kill -9 $p; done; sleep 2; pgrep -f eval_server.py || echo none'
powershell.exe -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath wsl.exe -ArgumentList '-d','Ubuntu','-e','bash','-lc','\"cd /mnt/c/Users/rapha/OneDrive/Desktop/Claude/Roolytooly && exec ~/.venvs/rooly/bin/python mcp_servers/eval_server.py > ~/eval.log 2>&1\"'"
sleep 8
wsl -d Ubuntu -e bash -lc 'ps -eo pid,lstart,args | grep eval_server | grep -v grep | cut -c1-70; ss -ltnp | grep -c 8902'
echo "$(date +%H:%M:%S) relaunching self-improvement loop"
nohup python -m harness.self_improve --hours 2.0 --auto-approve --skip-first-timeline > results/self_improve_stdout2.log 2>&1 &
echo "$(date +%H:%M:%S) done"
