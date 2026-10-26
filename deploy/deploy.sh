#!/usr/bin/env bash
# Redeploy on the VM: pull main, install deps, restart, health-check.
set -euo pipefail
cd /opt/cdl
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart cdl
# boot validates config and asserts the Notion schema first, so allow up to 30 s
for _ in $(seq 30); do
  if curl -sf http://127.0.0.1:8000/healthz; then echo " deployed $(git rev-parse --short HEAD)"; exit 0; fi
  sleep 1
done
echo "health check failed after 30 s; see: sudo journalctl -u cdl -n 50" >&2
exit 1
