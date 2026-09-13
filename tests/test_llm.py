"""Offline tests for forced Anthropic tool calls and extraction guards."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cdl.config import Settings
from cdl.llm import LLMError, draft, extract_entities, filter_entities, list_models
from cdl.models import DiffContext, Entities, FileChange, Note, Sentence
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return deterministic settings for fake-model tests."""

    return Settings(
        "http://test", "admin", "github", "webhook", "adot-7/commits-dont-lie", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack", "signing", "C1", "anthropic", "test-model", str(tmp_path / "db.sqlite")
    )


def response(tool_input, input_tokens=11, output_tokens=7):
    """Build an Anthropic-shaped tool response."""

    return SimpleNamespace(
        content=[SimpleNamespace(input=tool_input)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeMessages:
    """Queue-backed messages API double."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    """Anthropic client double exposing messages and models."""

    def __init__(self, responses):
        self.messages = FakeMessages(responses)
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="sonnet-new")]))


def diff():
    """Build a small diff containing all names used by the fake draft."""

    return DiffContext(
        "adot-7/commits-dont-lie", "a" * 40, "b" * 40,
        files=[FileChange("cdl/app.py", "modified", added=[(1, "def handle_push(): pass")])],
    )


def test_draft_uses_forced_tool_schema_and_logs_usage(tmp_path):
    """Drafting returns typed sentences and sends the normative request shape."""

    fake = FakeClient([
        response({"sentences": [
            {"text": "I added cdl/app.py.", "source": "commits"},
            {"text": "I wired handle_push.", "source": "both"},
            {"text": "I kept the update grounded.", "source": "notes"},
        ]})
    ])
    store = Store(tmp_path / "db.sqlite")
    sentences = draft("Tonight", "I worked on the webhook.", diff(), anthropic_client=fake, store=store, settings=settings(tmp_path))
    assert [item.idx for item in sentences] == [0, 1, 2]
    call = fake.messages.calls[0]
    assert call["model"] == "test-model"
    assert call["tool_choice"] == {"type": "tool", "name": "draft_sentences"}
    assert call["tools"][0]["input_schema"]["properties"]["sentences"]["maxItems"] == 6
    rows = store._connect().execute("SELECT data_json FROM events WHERE kind='llm.call'").fetchall()
    assert len(rows) == 1
    assert '"input_tokens": 11' in rows[0][0]


def test_draft_appends_file_voice_examples(tmp_path):
    """Drafting includes an optional author-post file under the voice label."""

    examples = tmp_path / "examples.md"
    examples.write_text("I kept poking at the weird failure until it gave up.", encoding="utf-8")
    fake = FakeClient([response({"sentences": [
        {"text": "I changed cdl/app.py.", "source": "commits"},
        {"text": "I fixed the weird failure.", "source": "both"},
        {"text": "I kept the receipts.", "source": "notes"},
    ]})])
    draft(
        "Tonight",
        "Something broke and it cost me an hour.",
        diff(),
        anthropic_client=fake,
        store=Store(tmp_path / "db.sqlite"),
        settings=replace(settings(tmp_path), style_examples_file=str(examples)),
    )
    system = fake.messages.calls[0]["system"]
    assert "Author's own past posts — match this voice:" in system
    assert "I kept poking at the weird failure until it gave up." in system


def test_extract_then_filter_drops_entity_not_in_sentence(tmp_path):
    """Extraction is tool-driven; the substring filter bounds its output."""

    fake = FakeClient([response({"files": ["cdl/app.py", "cdl/ghost.py"], "symbols": ["handle_push"], "integrations": []})])
    store = Store(tmp_path / "db.sqlite")
    sentence = Sentence(0, "I changed cdl/app.py and handle_push", "both")
    entities = extract_entities(sentence, ["cdl/app.py"], anthropic_client=fake, store=store, settings=settings(tmp_path))
    dropped = []
    filtered = filter_entities(sentence, entities, event_sink=lambda kind, data: dropped.append((kind, data)))
    assert filtered.files == ["cdl/app.py"]
    assert filtered.symbols == ["handle_push"]
    assert dropped[0][1]["entity"] == "cdl/ghost.py"


def test_malformed_tool_input_gets_one_retry_with_error(tmp_path):
    """Invalid output is retried once with the parse error in the prompt."""

    fake = FakeClient([
        response({"sentences": []}),
        response({"sentences": [
            {"text": "I changed cdl/app.py.", "source": "commits"},
            {"text": "I updated the webhook.", "source": "both"},
            {"text": "I kept receipts.", "source": "notes"},
        ]}),
    ])
    sentences = draft("Retry", "note", diff(), anthropic_client=fake, store=Store(tmp_path / "db.sqlite"), settings=settings(tmp_path))
    assert len(sentences) == 3
    assert len(fake.messages.calls) == 2
    assert "sentences must contain" in fake.messages.calls[1]["messages"][-1]["content"]


def test_overlong_retry_is_guided_and_truncated_to_leading_sentences(tmp_path):
    """An overlong second draft becomes a logged, bounded shorter post."""

    first = [
        {"text": "a" * 250, "source": "commits"},
        {"text": "b" * 250, "source": "commits"},
        {"text": "c" * 250, "source": "commits"},
        {"text": "d" * 250, "source": "commits"},
    ]
    second = [
        {"text": "e" * 300, "source": "commits"},
        {"text": "f" * 300, "source": "commits"},
        {"text": "g" * 300, "source": "commits"},
        {"text": "h" * 300, "source": "commits"},
    ]
    fake = FakeClient([response({"sentences": first}), response({"sentences": second})])
    store = Store(tmp_path / "db.sqlite")
    sentences = draft("Retry", "note", diff(), anthropic_client=fake, store=store, settings=settings(tmp_path))
    assert [sentence.text[0] for sentence in sentences] == ["e", "f", "g"]
    assert sum(len(sentence.text) for sentence in sentences) == 900
    assert fake.messages.calls[1]["messages"][-1]["content"].startswith(
        "You returned 1000 characters. Return at most 4 sentences and stay under 800 characters."
    )
    rows = store._connect().execute("SELECT kind, data_json FROM events WHERE kind='draft.truncated'").fetchall()
    assert len(rows) == 1
    assert '"original_characters": 1200' in rows[0][1]


def test_malformed_tool_input_after_retry_raises(tmp_path):
    """Two invalid responses become a typed LLM error."""

    fake = FakeClient([response({"sentences": []}), response({"sentences": []})])
    with pytest.raises(LLMError, match="after retry"):
        draft("Retry", "note", diff(), anthropic_client=fake, store=Store(tmp_path / "db.sqlite"), settings=settings(tmp_path))


def test_note_dataclass_api_and_model_listing(tmp_path):
    """The alternate Note contract and models CLI helper both work offline."""

    fake = FakeClient([response({"sentences": [
        {"text": "I changed cdl/app.py.", "source": "commits"},
        {"text": "I updated handle_push.", "source": "both"},
        {"text": "I wrote receipts.", "source": "notes"},
    ]})])
    result = draft(Note("n1", "Title", "Body"), diff(), anthropic_client=fake, store=Store(tmp_path / "db.sqlite"), settings=settings(tmp_path))
    assert result[0].text == "I changed cdl/app.py."
    assert list_models(anthropic_client=fake, settings=settings(tmp_path)) == ["sonnet-new"]
