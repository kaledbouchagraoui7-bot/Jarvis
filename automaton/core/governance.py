"""Governance Layer — PydanticOutputParser with a silent corrective retry loop.

Local models (Qwen, Llama) frequently emit malformed JSON. Nothing unvalidated
ever reaches a database or a tool: every structured response is parsed against
a Pydantic schema; on failure, a corrective prompt is fed back to the model in
a bounded retry loop.
"""
from __future__ import annotations

import json
import re
from typing import Any, Generic, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .types import AuditEvent

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """Best-effort extraction of the first JSON object/array in model output."""
    text = text.strip()
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    # locate first balanced {...}
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    raise ValueError("unterminated JSON object in model output")


class OutputContractError(Exception):
    """Raised when the model cannot produce schema-valid output after retries."""

    def __init__(self, schema_name: str, attempts: int, last_error: str):
        self.schema_name, self.attempts, self.last_error = schema_name, attempts, last_error
        super().__init__(
            f"{schema_name}: invalid output after {attempts} attempt(s). Last error: {last_error}"
        )


class PydanticOutputParser(Generic[T]):
    """Parse raw LLM text into a validated Pydantic model, with correction loop.

    ``llm(prompt) -> str`` is any callable model handle. The parser never
    returns unconstrained data: success yields ``T``, failure raises
    ``OutputContractError`` after ``max_retries`` corrective prompts.
    """

    def __init__(
        self,
        schema: Type[T],
        max_retries: int = 3,
        audit_log: Optional[list[AuditEvent]] = None,
    ):
        self.schema = schema
        self.max_retries = max_retries
        self.audit_log = audit_log if audit_log is not None else []

    # -- schema hint embedded into corrective prompts ---------------------- #
    def _schema_hint(self) -> str:
        model = self.schema.model_json_schema()
        return json.dumps(model, separators=(",", ":"), ensure_ascii=False)

    def _corrective_prompt(self, original: str, bad_output: str, error: str) -> str:
        return (
            "Your previous reply was NOT valid for the required contract.\n"
            f"PARSE ERROR: {error}\n\n"
            "Return ONLY a single JSON object matching this JSON Schema, with no "
            "commentary, no markdown fences, and no trailing text:\n"
            f"SCHEMA: {self._schema_hint()}\n\n"
            f"ORIGINAL TASK CONTEXT (abridged):\n{original[:2000]}\n\n"
            f"YOUR INVALID REPLY WAS:\n{bad_output[:2000]}\n\n"
            "Now output the corrected JSON only:"
        )

    def parse(self, raw: str) -> T:
        """Validate a single raw completion (no retry)."""
        payload = extract_json(raw)
        return self.schema.model_validate_json(payload)

    def parse_with_retry(self, llm, prompt: str) -> T:
        """Generate + validate; on failure feed a corrective prompt silently."""
        current = prompt
        last_err = ""
        for attempt in range(1, self.max_retries + 1):
            raw = llm(current)
            try:
                obj = self.parse(raw)
                self.audit_log.append(
                    AuditEvent(
                        actor="governance.pydantic_parser",
                        event="output_validated",
                        detail={"schema": self.schema.__name__, "attempt": attempt},
                    )
                )
                return obj
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:  # noqa: BLE001
                last_err = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                self.audit_log.append(
                    AuditEvent(
                        actor="governance.pydantic_parser",
                        event="output_rejected",
                        detail={
                            "schema": self.schema.__name__,
                            "attempt": attempt,
                            "error": last_err,
                        },
                    )
                )
                current = self._corrective_prompt(prompt, raw, last_err)
        raise OutputContractError(self.schema.__name__, self.max_retries, last_err)


def redact_secrets(text: str) -> str:
    """Defense-in-depth: strip credential-shaped tokens before anything is logged.

    Enforces the constitutional ban on sending credentials into prompts/memory.
    """
    patterns = [
        (re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED_GH_TOKEN]"),
        (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[REDACTED_API_KEY]"),
        (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
        (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
        (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED_JWT]"),
    ]
    out = text
    for rx, repl in patterns:
        out = rx.sub(repl, out)
    return out
