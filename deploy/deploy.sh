#!/usr/bin/env bash
# Redeploy on the VM: pull main, install deps, restart, health-check.
set -euo pipefail
cd /opt/cdl
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart cdl
sleep 1
curl -sf http://127.0.0.1:8000/healthz && echo " deployed $(git rev-parse --short HEAD)"
