"""Model drafting, extraction, and substring filtering for CDL.

This module owns forced tool calls (BharatCode by default, Anthropic optional)
and the guard that removes extracted names not present in the sentence. It must never decide SUPPORTED, UNSUPPORTED, or
UNVERIFIABLE; that decision belongs only to ``grounding.check``.
"""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Callable

try:
    import anthropic
except ImportError:  # pragma: no cover - dependency is listed in requirements
    anthropic = None

from .bharatcode_client import BharatCodeClient, BharatCodeError, ToolResult
from .config import Settings, get_settings
from .models import DiffContext, Entities, Note, Sentence
from .store import Store


class LLMError(RuntimeError):
    """Raised when the model returns malformed tool input after one retry."""


MAX_DRAFT_CHARACTERS = 900
RETRY_DRAFT_CHARACTERS = 800


class DraftTooLongError(ValueError):
    """Carry the validated sentences and length for a bounded retry/fallback."""

    def __init__(self, characters: int, sentences: list[Sentence]):
        self.characters = characters
        self.sentences = sentences
        super().__init__(f"draft is {characters} characters; maximum is {MAX_DRAFT_CHARACTERS}")


RULE = "Every file, function, or integration a sentence names must appear in the diff — or the sentence doesn't ship."

DRAFT_TOOL: dict[str, Any] = {
    "name": "draft_sentences",
    "description": "Draft a short build update as typed sentences.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sentences": {
                "type": "array",
                "minItems": 3,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string"},
                        "source": {"type": "string", "enum": ["notes", "commits", "both"]},
                    },
                    "required": ["text", "source"],
                },
            }
        },
        "required": ["sentences"],
    },
}

ENTITIES_TOOL: dict[str, Any] = {
    "name": "extract_entities",
    "description": "Extract concrete files, symbols, and integrations named by a sentence.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "files": {"type": "array", "items": {"type": "string"}},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "integrations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["files", "symbols", "integrations"],
    },
}


def _keeps(sentence_text: str, entity: str, *, file_entity: bool = False) -> bool:
    """Apply the case-insensitive literal substring rule."""

    lowered = sentence_text.lower()
    return entity.lower() in lowered or (
        file_entity and entity.rsplit("/", 1)[-1].lower() in lowered
    )


def filter_entities(
    sentence: Sentence,
    entities: Entities,
    *,
    event_sink: Callable[[str, dict], object] | None = None,
) -> Entities:
    """Drop hallucinated entities and optionally emit one event per drop."""

    kept: dict[str, list[str]] = {"files": [], "symbols": [], "integrations": []}
    for category, values in (
        ("files", entities.files),
        ("symbols", entities.symbols),
        ("integrations", entities.integrations),
    ):
        for value in values:
            if _keeps(sentence.text, value, file_entity=category == "files"):
                kept[category].append(value)
            elif event_sink is not None:
                event_sink(
                    "entity_dropped",
                    {"sentence_idx": sentence.idx, "entity": value, "kind": category[:-1]},
                )
    return Entities(**kept)


def _settings(settings: Settings | None) -> Settings:
    """Resolve settings lazily so importing this module needs no credentials."""

    return settings or get_settings(strict=False)


def _store(settings: Settings, store: Store | None) -> Store:
    """Resolve an event store for calls that need audit records."""

    return store or Store(settings.database_path)


class AnthropicToolCaller:
    """Adapt an Anthropic SDK client to the provider-neutral ``call_tool`` shape."""

    def __init__(self, client: Any, model: str):
        self.client = client
        self.model = model

    def _accepts(self, name: str) -> bool:
        """Return True when the installed SDK's messages.create accepts ``name``."""

        try:
            params = inspect.signature(self.client.messages.create).parameters
        except (TypeError, ValueError):
            return False
        return name in params

    def call_tool(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool: dict[str, Any],
        temperature: float,
        max_tokens: int = 1024,
    ) -> ToolResult:
        """Force one tool_use block and return its raw input."""

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }
        if self._accepts("temperature"):
            kwargs["temperature"] = temperature
        response = self.client.messages.create(**kwargs)
        input_tokens, output_tokens = _usage(response)
        return ToolResult(_response_raw(response), input_tokens, output_tokens)

    def list_models(self) -> list[str]:
        """List model IDs visible to the Anthropic key."""

        response = self.client.models.list()
        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data", [])
        ids = [str(model.get("id") if isinstance(model, dict) else getattr(model, "id", "")) for model in data or []]
        return [model_id for model_id in ids if model_id]


def _model_client(settings: Settings, anthropic_client: Any | None, llm_client: Any | None) -> Any:
    """Return an object with ``call_tool``, built only when a real call is requested."""

    if llm_client is not None:
        return llm_client
    if anthropic_client is not None:
        return AnthropicToolCaller(anthropic_client, settings.anthropic_model)
    if settings.llm_provider == "anthropic":
        if anthropic is None:
            raise LLMError("anthropic package is not installed")
        return AnthropicToolCaller(anthropic.Anthropic(api_key=settings.anthropic_api_key), settings.anthropic_model)
    if not settings.bharatcode_api_key:
        raise LLMError("BHARATCODE_API_KEY is not set; get one at https://bharatcode.ai/account")
    return BharatCodeClient(
        settings.bharatcode_api_key,
        model=settings.bharatcode_model,
        base_url=settings.bharatcode_base_url,
        reasoning_effort=settings.bharatcode_reasoning_effort,
    )


def _response_raw(response: Any) -> Any:
    """Read the first forced tool-use block's input from an Anthropic response."""

    content = getattr(response, "content", None)
    if not content:
        raise ValueError("response has no content")
    block = content[0]
    raw = getattr(block, "input", None)
    if raw is None and isinstance(block, dict):
        raw = block.get("input")
    if raw is None:
        raise ValueError("response content has no tool input")
    return raw


def _decode_input(raw: Any) -> dict[str, Any]:
    """Decode tool arguments that may arrive as a JSON string or an object."""

    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("tool input is not an object")
    return raw


def _usage(response: Any) -> tuple[int, int]:
    """Read Anthropic usage from either an SDK object or a test dictionary."""

    usage = getattr(response, "usage", None) or {}
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


def _log_call(
    store: Store,
    *,
    operation: str,
    model: str,
    result: ToolResult | None,
    elapsed_ms: int,
    error: str | None = None,
) -> None:
    """Append one usage/timing event for every attempted model call."""

    data: dict[str, Any] = {
        "operation": operation,
        "model": model,
        "input_tokens": result.input_tokens if result else 0,
        "output_tokens": result.output_tokens if result else 0,
        "ms": elapsed_ms,
    }
    if result is not None and result.request_id:
        data["request_id"] = result.request_id
    if result is not None and result.via != "tool_call":
        data["via"] = result.via
    if error:
        data["error"] = error
    store.append_event("llm.call", data)


def _diff_material(diff: DiffContext) -> str:
    """Format bounded commit and patch context for the drafting prompt."""

    sections = ["Commits:"]
    sections.extend(f"- {commit.sha[:12]}: {commit.message}" for commit in diff.commits)
    sections.append("Changed files:")
    for file in diff.files[:20]:
        patch = file.patch if file.patch is not None else "<patch unavailable>"
        sections.append(f"\n### {file.path} ({file.status})\n{patch[:4000]}")
    return "\n".join(sections)


def _note_parts(note: str | Note, note_body: str | DiffContext, diff: DiffContext | None) -> tuple[str, str, DiffContext]:
    """Support both ``draft(title, body, diff)`` and ``draft(Note, diff)`` APIs."""

    if diff is None:
        if not isinstance(note_body, DiffContext):
            raise TypeError("draft requires a DiffContext")
        if isinstance(note, Note):
            return note.title, note.body, note_body
        raise TypeError("draft(note, diff) requires a Note dataclass")
    if isinstance(note, Note):
        return note.title, note.body, diff
    if not isinstance(note_body, str):
        raise TypeError("note body must be text")
    return str(note), note_body, diff


def _validate_draft(payload: dict[str, Any]) -> list[Sentence]:
    """Validate the tool schema and convert it to typed sentences."""

    raw_sentences = payload.get("sentences")
    if not isinstance(raw_sentences, list) or not 3 <= len(raw_sentences) <= 6:
        raise ValueError("sentences must contain 3 to 6 items")
    sentences: list[Sentence] = []
    total = 0
    for idx, item in enumerate(raw_sentences):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError(f"sentence {idx} has invalid text")
        text = item["text"].strip()
        source = item.get("source")
        if not text or source not in {"notes", "commits", "both"}:
            raise ValueError(f"sentence {idx} has invalid text or source")
        total += len(text)
        sentences.append(Sentence(idx=idx, text=text, source=source))
    if total > MAX_DRAFT_CHARACTERS:
        raise DraftTooLongError(total, sentences)
    return sentences


def _truncate_sentences(sentences: list[Sentence], maximum: int = MAX_DRAFT_CHARACTERS) -> list[Sentence]:
    """Keep the leading complete sentences that fit under the output cap."""

    kept: list[Sentence] = []
    total = 0
    for sentence in sentences:
        if total + len(sentence.text) > maximum:
            break
        kept.append(Sentence(idx=len(kept), text=sentence.text, source=sentence.source))
        total += len(sentence.text)
    if not kept and sentences:
        first = sentences[0]
        kept.append(Sentence(idx=0, text=first.text[:maximum].rstrip(), source=first.source))
    return kept


def _validate_entities(payload: dict[str, Any]) -> Entities:
    """Validate an extraction tool object and convert it to ``Entities``."""

    values: dict[str, list[str]] = {}
    for key in ("files", "symbols", "integrations"):
        value = payload.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a string array")
        values[key] = value
    return Entities(**values)


def _create_tool_call(
    client: Any,
    *,
    operation: str,
    system: str,
    messages: list[dict[str, Any]],
    tool: dict[str, Any],
    temperature: float,
    store: Store,
) -> dict[str, Any]:
    """Make one forced-tool call, write its usage event, and decode its input."""

    model = str(getattr(client, "model", ""))
    started = time.perf_counter()
    try:
        result = client.call_tool(system=system, messages=messages, tool=tool, temperature=temperature)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _log_call(store, operation=operation, model=model, result=None, elapsed_ms=elapsed_ms, error=str(exc))
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log_call(store, operation=operation, model=model, result=result, elapsed_ms=elapsed_ms)
    return _decode_input(result.raw_input)


def draft(
    note: str | Note,
    note_body: str | DiffContext,
    diff: DiffContext | None = None,
    *,
    anthropic_client: Any | None = None,
    llm_client: Any | None = None,
    store: Store | None = None,
    settings: Settings | None = None,
) -> list[Sentence]:
    """Draft 3–6 bounded sentences through one forced model tool call."""

    title, body, context = _note_parts(note, note_body, diff)
    active_settings = _settings(settings)
    active_store = _store(active_settings, store)
    system = (
        f"{RULE}\n"
        "Write a build-in-public post, not a changelog. Never put commit SHAs in the text. "
        "Lead with the human moment from the note (what broke, what surprised you, what it cost). "
        "Keep the author's own phrasing from the note wherever the diff supports it; add facts "
        "from the commits only to fill gaps. Name files and functions naturally inside sentences "
        "(for example, in cdl/llm.py I now feature-detect…), never as lists. "
        "Write 3–6 first-person sentences, no more than 900 characters total. "
        "Exactly one sentence may be pure voice with no file, function, or integration; every "
        "other sentence must name something checkable. Tag each sentence with source notes, "
        "commits, or both."
    )
    if active_settings.style_examples:
        system += "\nStyle examples from the author:\n" + active_settings.style_examples
    style_path = Path(active_settings.style_examples_file)
    if style_path.is_file():
        file_examples = style_path.read_text(encoding="utf-8").strip()
        if file_examples:
            system += "\nAuthor's own past posts — match this voice:\n" + file_examples
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Note title: {title}\nNote body:\n{body}\n\nDiff context:\n{_diff_material(context)}",
        }
    ]
    client = _model_client(active_settings, anthropic_client, llm_client)
    last_error = ""
    last_exception: Exception | None = None
    for attempt in range(2):
        if attempt:
            retry_prefix = ""
            if isinstance(last_exception, DraftTooLongError):
                retry_prefix = (
                    f"You returned {last_exception.characters} characters. Return at most 4 sentences "
                    f"and stay under {RETRY_DRAFT_CHARACTERS} characters.\n"
                )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"{retry_prefix}The previous tool input was invalid: {last_error}. "
                        "Return only a valid draft_sentences tool input."
                    ),
                }
            )
        try:
            payload = _create_tool_call(
                client,
                operation="draft",
                system=system,
                messages=messages,
                tool=DRAFT_TOOL,
                temperature=0.3,
                store=active_store,
            )
            sentences = _validate_draft(payload)
            active_store.append_event("draft.generated", {"sentences": len(sentences), "characters": sum(len(item.text) for item in sentences)})
            return sentences
        except DraftTooLongError as exc:
            last_error = str(exc)
            last_exception = exc
            if attempt == 1:
                truncated = _truncate_sentences(exc.sentences)
                active_store.append_event(
                    "draft.truncated",
                    {
                        "original_characters": exc.characters,
                        "characters": sum(len(item.text) for item in truncated),
                        "sentences": len(truncated),
                    },
                )
                active_store.append_event(
                    "draft.generated",
                    {"sentences": len(truncated), "characters": sum(len(item.text) for item in truncated)},
                )
                return truncated
            continue
        except BharatCodeError as exc:
            # Auth, quota and exhausted-budget failures are not fixed by resending the same call.
            raise LLMError(f"BharatCode call failed: {exc}") from exc
        except Exception as exc:
            last_error = str(exc)
            last_exception = exc
            if attempt == 1:
                raise LLMError(f"draft tool input invalid after retry: {last_error}") from exc
    raise LLMError("draft failed")


def extract_entities(
    sentence: Sentence | str,
    changed_paths: list[str] | None = None,
    *,
    anthropic_client: Any | None = None,
    llm_client: Any | None = None,
    store: Store | None = None,
    settings: Settings | None = None,
) -> Entities:
    """Extract concrete entity categories for one sentence through tool use."""

    active_settings = _settings(settings)
    active_store = _store(active_settings, store)
    current = sentence if isinstance(sentence, Sentence) else Sentence(0, sentence, "both")
    paths = changed_paths or []
    system = (
        f"{RULE}\nExtract only concrete files, symbols, and integrations named literally in the sentence. "
        "Symbols must be code identifiers: they contain an underscore, a dot, parentheses, or CamelCase, "
        "or exactly equal a path segment from the changed paths. Plain lowercase English words such as "
        "github, webhook, or server are never symbols; return them as integrations only when they name "
        "one of the allowed products, otherwise omit them. Integrations must be single product names: "
        "GitHub, Slack, Notion, BharatCode, Anthropic, SQLite, FastAPI, or Caddy. "
        "The changed paths are hints for spelling only; do not add an entity that is not in the sentence."
    )
    messages = [
        {
            "role": "user",
            "content": f"Sentence: {current.text}\nChanged paths for spelling hints: {json.dumps(paths)}",
        }
    ]
    client = _model_client(active_settings, anthropic_client, llm_client)
    last_error = ""
    for attempt in range(2):
        if attempt:
            messages.append(
                {
                    "role": "user",
                    "content": f"The previous tool input was invalid: {last_error}. Return only valid extract_entities tool input.",
                }
            )
        try:
            payload = _create_tool_call(
                client,
                operation="extract_entities",
                system=system,
                messages=messages,
                tool=ENTITIES_TOOL,
                temperature=0.0,
                store=active_store,
            )
            entities = _validate_entities(payload)
            active_store.append_event(
                "entities.extracted",
                {"sentence_idx": current.idx, "files": len(entities.files), "symbols": len(entities.symbols), "integrations": len(entities.integrations)},
            )
            return entities
        except BharatCodeError as exc:
            raise LLMError(f"BharatCode call failed: {exc}") from exc
        except Exception as exc:
            last_error = str(exc)
            if attempt == 1:
                raise LLMError(f"entity tool input invalid after retry: {last_error}") from exc
    raise LLMError("entity extraction failed")


def list_models(
    *,
    anthropic_client: Any | None = None,
    llm_client: Any | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """List model IDs for the configured provider."""

    active_settings = _settings(settings)
    if llm_client is None and anthropic_client is None and active_settings.llm_provider == "bharatcode":
        client = BharatCodeClient(active_settings.bharatcode_api_key, base_url=active_settings.bharatcode_base_url)
        try:
            return client.list_models()
        finally:
            client.close()
    return _model_client(active_settings, anthropic_client, llm_client).list_models()
