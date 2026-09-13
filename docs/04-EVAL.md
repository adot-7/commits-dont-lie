# 04 — Evaluation ("show how you know it works")

Two layers. Both are cheap because the verdict function is deterministic.

## 1. Unit tests (agent writes, M2) — `tests/test_check.py`
Fixture diffs in `tests/fixtures/*.json` (hand-made `DiffContext` dumps, or captured from the
real compare API via `python -m cdl dump-compare <base> <head> > tests/fixtures/x.json`).
Cover at least:
- file match by full path; by basename; removed file does not count
- symbol match on added line; symbol only in removed line → not supported
- integration alias match in path; in added line
- no entities → UNVERIFIABLE
- one of two entities missing → UNSUPPORTED, `missing` populated, reason names it
- `patch=None` file → symbol miss carries "(some patches unavailable)"
- staleness: file removed; file renamed; symbol net-removed; symbol moved (removed + added
  elsewhere) → **not** stale; body edit keeping name → not stale
- substring filter drops hallucinated entity

`make test` = `pytest -q`. Must be green before every push after M2.

## 2. Hand-labelled eval set (human writes ~02:45, agent runs) — `eval/cases.jsonl`
One JSON object per line:
```json
{"id":"c01","base":"<sha>","head":"<sha>","sentence":"Added HMAC verification for the GitHub webhook in github_client.py","expected":"SUPPORTED","note":"verify_signature added"}
{"id":"c02","base":"<sha>","head":"<sha>","sentence":"Wired the Notion Posts database","expected":"UNSUPPORTED","note":"no notion changes in this range"}
{"id":"c03","base":"<sha>","head":"<sha>","sentence":"Cleaned things up and made the pipeline more robust","expected":"UNVERIFIABLE","note":"names nothing"}
```
**How the human writes it (15 lines, ~20 min):** open five real commits from tonight on
GitHub. For each, write three sentences: one true claim naming the file/function, one false
claim naming something the commit did not touch, one vague claim naming nothing. The author
knows every answer because he watched the code get written.

`python -m cdl eval` (`make eval`):
1. For each case, `github.compare(base, head)` (cached to `eval/cache/`), then run the **full
   extraction path** — `llm.extract_entities(sentence)` → substring filter → `claim_vs_diff`.
   This tests the extractor and the matcher together, which is what production runs.
2. Also run a **matcher-only** pass using entities hand-written in an optional `entities` field
   when present, to separate extractor errors from matcher errors.
3. Write `eval/report.json` and `eval/report.md`: 3×3 confusion matrix, per-class precision and
   recall, list of misses with `expected`, `got`, `reason`. Exit non-zero if accuracy < 0.8.
4. Dashboard `/eval` renders `report.json` — the judges can open it from the production link.

## 3. Production self-report (free)
`/` shows totals since boot: pushes received, posts drafted / blocked / sent / stale,
sentences by verdict, `entity_dropped` count, LLM tokens and estimated cost. These numbers,
plus the eval report and the failure-handling table from `docs/01`, **are** the reliability
brief (`docs/05 §2`).

## 4. Known limitations to state, not hide
- The check is lexical. A sentence can name the right file and still mischaracterise what
  changed inside it ("rewrote the matcher" when one line moved). We verify *presence*, not
  *semantics*. Say this in the brief; it is what keeps the claim honest and one sentence long.
- Extraction is an LLM step and can miss an entity (→ UNVERIFIABLE when it should be
  SUPPORTED). The eval's matcher-only pass isolates this; the substring filter bounds it from
  the other direction (it cannot add entities).
- Staleness is conservative for integrations and does not track semantic drift.
