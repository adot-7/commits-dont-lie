# Commits Don't Lie evaluation

Accuracy: **28/30 (93.3%)**

Extraction model: `bharatcode` / `qwen-3.8-27b`. The verdict itself is always the deterministic matcher.

## Confusion matrix

| expected \ got | SUPPORTED | UNSUPPORTED | UNVERIFIABLE |
|---|---|---|---|
| SUPPORTED | 9 | 0 | 0 |
| UNSUPPORTED | 1 | 11 | 0 |
| UNVERIFIABLE | 1 | 0 | 8 |

## Per-class precision / recall

| class | precision | recall |
|---|---:|---:|
| SUPPORTED | 0.818 | 1.000 |
| UNSUPPORTED | 1.000 | 0.917 |
| UNVERIFIABLE | 1.000 | 0.889 |

## Misses

- `ba052d0-UNV` expected `UNVERIFIABLE`, got `SUPPORTED` — Every named file, function, and integration appears in the diff.
- `38dee1b-ADV-decoy-integration` expected `UNSUPPORTED`, got `SUPPORTED` — Every named file, function, and integration appears in the diff.

## Staleness cases

Accuracy: **5/5 (100.0%)**

| id | expected stale | got stale | note |
|---|---:|---:|---|
| `renamed-symbol-stale` | true | true | The live pipeline fix removed the obsolete active_github call-site symbol. |
| `body-edit-keeps-symbol` | false | false | Slack update_message changed its body and signature but kept the symbol. |
| `unrelated-commit` | false | false | The Slack preservation commit does not remove the grounding checker. |
| `symbol-moved-file` | false | false | The webhook work references handle_push from the pipeline while the app receipt remains non-removed. |
| `integration-receipt-kept` | false | false | The Slack-only receipt changes while cdl/slack_client.py remains present. |
