.PHONY: serve test eval secrets deploy

serve:
	.venv/bin/uvicorn cdl.app:app --host 127.0.0.1 --port 8000 --reload

test:
	.venv/bin/pytest -q

eval:
	.venv/bin/python -m cdl eval

# abort a commit if a secret is staged
secrets:
	@git diff --cached | grep -iE 'xoxb-|ntn_|secret_[a-z0-9]|sk-ant-|ghp_|github_pat_' && echo "SECRET STAGED — abort" && exit 1 || echo "no secrets staged"

deploy:
	ssh cdl-vm 'bash -s' < deploy/deploy.sh
