# 07 — Deploy & ops runbook (human-executed unless marked agent)

Target: Oracle Cloud `VM.Standard.E2.1.Micro`, Ubuntu 24.04, 1 vCPU, **1 GB RAM**, x86,
public IP `161.118.188.75`, region ap-mumbai-1. Domain `commitsdontlie.akashparashar.dev`.
No Docker (memory). systemd + venv + Caddy.

## 1. DNS (do first — propagation)
At your DNS provider for `akashparashar.dev`: **A** record, name `commitsdontlie`, value
`161.118.188.75`, TTL 300. Check: `dig +short commitsdontlie.akashparashar.dev` → the IP.

## 2. Oracle network (both layers, or it will time out)
**Console:** Networking → VCN → the subnet's Security List → Add Ingress Rules:
source `0.0.0.0/0`, protocol TCP, destination ports `80` and `443` (two rules).
**On the VM** (Oracle Ubuntu images block everything but 22 in iptables):
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. VM prep (once)
```bash
# swap — pip and uvicorn on 1 GB will OOM without it
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo apt update && sudo apt install -y python3.12-venv git caddy sqlite3
sudo mkdir -p /opt/cdl && sudo chown $USER:$USER /opt/cdl
git clone https://github.com/adot-7/commits-dont-lie.git /opt/cdl
cd /opt/cdl && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # fill secrets; never commit
mkdir -p data
```

## 4. Services
`deploy/cdl.service` → `/etc/systemd/system/cdl.service`; `deploy/Caddyfile` → `/etc/caddy/Caddyfile`.
```bash
sudo cp deploy/cdl.service /etc/systemd/system/cdl.service
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload && sudo systemctl enable --now cdl && sudo systemctl reload caddy
curl -s https://commitsdontlie.akashparashar.dev/healthz     # {"ok":true}
journalctl -u cdl -f                                          # live logs
```
Caddy obtains the Let's Encrypt cert automatically once DNS resolves and 80/443 are open.
If it fails: `journalctl -u caddy -n 50` — 99% it is §2.

**Redeploy after every push (agent may run this over SSH if given access; otherwise human):**
```bash
cd /opt/cdl && git pull --ff-only && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart cdl && sleep 1 && curl -sf localhost:8000/healthz
```
(`deploy/deploy.sh` wraps this.)

## 5. GitHub webhook (after §4 returns 200)
Repo → Settings → Webhooks → Add webhook. Payload URL
`https://commitsdontlie.akashparashar.dev/webhook/github`, Content type `application/json`,
Secret = `GITHUB_WEBHOOK_SECRET` from `.env` (`openssl rand -hex 32`), SSL verification on,
**Just the push event**. Recent Deliveries tab shows 202s; **Redeliver** button = free replay.

**PAT:** Settings → Developer settings → Fine-grained tokens → this repo only →
Repository permissions: Contents **Read-only**, Metadata **Read-only**. → `GITHUB_TOKEN`.

## 6. Notion (~10 min)
1. notion.so → Settings → Connections → Develop or manage integrations → **New integration**,
   internal, workspace = yours, capabilities: read content, update content, insert content.
   Copy the secret → `NOTION_TOKEN`.
2. Create a page **Commits Don't Lie**. Inside it, `/database` → **Database – Full page** twice:
   - **Notes** with properties exactly: `Name` (title), `Status` (select: `Writing`, `Ready`,
     `Drafted`), `Created` (created time).
   - **Posts** with properties exactly (`docs/02 §7`): `Name` (title), `Status` (select: `Blocked`,
     `Draft`, `Sent`, `Rejected`, `Stale`, `Correction`, `Errored`), `Post Text` (text),
     `Evidence` (text), `Commit Range` (text), `Head SHA` (text), `Slack TS` (text),
     `Superseded By` (relation → Posts, no two-way), `Note` (relation → Notes), `Dashboard` (URL).
3. On the parent page: `···` → Connections → add your integration (children inherit).
4. Database IDs: open each database as full page; URL is
   `notion.so/<workspace>/<32-hex-id>?v=…` → the 32 hex = `NOTION_*_DB_ID`.
5. Agent: `python -m cdl resolve-notion-ids` → paste `NOTION_*_DS_ID` into `.env`.

## 7. Slack (~10 min)
1. api.slack.com/apps → **Create New App** → From scratch → name `Commits Don't Lie`, your workspace.
2. OAuth & Permissions → Bot Token Scopes: `chat:write`, `chat:write.public`. **Install to
   Workspace** → copy `xoxb-…` → `SLACK_BOT_TOKEN`.
3. Basic Information → App Credentials → **Signing Secret** → `SLACK_SIGNING_SECRET`.
4. Create channel `#build-log`; `/invite @Commits Don't Lie`. Channel details → copy Channel ID
   (`C…`) → `SLACK_CHANNEL_ID`.
5. Interactivity & Shortcuts → toggle **On** → Request URL
   `https://commitsdontlie.akashparashar.dev/slack/interactions` → Save. (After §4 is live.)

## 8. Anthropic
platform.claude.com → API keys → create → `ANTHROPIC_API_KEY`. Buy $5 credits. Verify model id
with `python -m cdl models`.

## 9. Sanity sequence once everything is set
```bash
python -m cdl resolve-notion-ids      # prints DS ids
python -m cdl models                  # confirms ANTHROPIC_MODEL exists
curl -X POST https://…/draft-now -H "Authorization: Bearer $ADMIN_TOKEN"   # after M3
```
Then: write a note in Notion, set `Ready`, `git commit --allow-empty -m "test: webhook" && git push`,
watch `journalctl -u cdl -f`.
