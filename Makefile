.PHONY: serve test eval secrets deploy

PYTHON ?= python3

serve:
	$(PYTHON) -m uvicorn cdl.app:app --host 127.0.0.1 --port 8000 --reload

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m cdl eval

# abort a commit if a secret is staged
secrets:
	@git diff --cached | grep -iE 'xoxb-|ntn_|secret_[a-z0-9]|sk-ant-|ghp_|github_pat_' && echo "SECRET STAGED — abort" && exit 1 || echo "no secrets staged"

deploy:
	ssh cdl-vm 'bash -s' < deploy/deploy.sh
