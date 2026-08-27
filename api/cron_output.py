"""Shared parsing and presentation projection for persisted cron output."""

from __future__ import annotations

import re


_PREAMBLE = re.compile(r"^# Cron Job:[^\r\n]*\r?$", re.MULTILINE)
_PROMPT = re.compile(r"^## Prompt[ \t]*\r?$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,2} Response[ \t]*\r?$", re.MULTILINE)
_ERROR = re.compile(r"^#{1,2} Error[ \t]*\r?$", re.MULTILINE)


def _outside_fence_and_quote(text: str, index: int) -> bool:
    fence_char = None
    fence_length = 0
    for line in text[:index].splitlines():
        stripped = line.strip()
        match = re.match(r"(`{3,}|~{3,})(?:[^`~]*)$", stripped)
        if not match:
            continue
        delimiter = match.group(1)
        if fence_char is None:
            fence_char, fence_length = delimiter[0], len(delimiter)
        elif delimiter[0] == fence_char and len(delimiter) >= fence_length:
            fence_char = None
            fence_length = 0
    line_start = text.rfind("\n", 0, index) + 1
    return fence_char is None and not text[line_start:index].lstrip().startswith(">")


def parse_cron_output_artifact(text: str, *, job_mode: str = "unknown") -> dict:
    """Return one fail-closed, raw-preserving projection for a cron artifact."""
    raw = text if isinstance(text, str) else str(text or "")
    base = {"kind": "raw", "response": None, "diagnostics": raw,
            "raw": raw, "fallback_reason": None}
    if job_mode == "script":
        base["fallback_reason"] = "script_mode"
        return base
    if job_mode != "agent":
        base["fallback_reason"] = "unknown_mode"
        return base
    preamble = _PREAMBLE.match(raw)
    prompt_candidates = [m for m in _PROMPT.finditer(raw, preamble.end() if preamble else 0)
                         if _outside_fence_and_quote(raw, m.start())]
    prompt = prompt_candidates[0] if len(prompt_candidates) == 1 else None
    if not preamble or not prompt or prompt.start() <= preamble.end():
        base["fallback_reason"] = "malformed_preamble"
        return base
    errors = [m for m in _ERROR.finditer(raw)
              if _outside_fence_and_quote(raw, m.start())]
    if errors:
        base["fallback_reason"] = "error_output"
        return base
    candidates = [m for m in _HEADING.finditer(raw, prompt.end())
                  if _outside_fence_and_quote(raw, m.start())]
    if len(candidates) != 1:
        base["fallback_reason"] = "missing_marker" if not candidates else "ambiguous_marker"
        return base
    marker = candidates[0]
    response = raw[marker.end():].lstrip("\r\n")
    if not response:
        base["fallback_reason"] = "empty_response"
        return base
    return {"kind": "agent", "response": response,
            "diagnostics": raw[:marker.start()].rstrip(), "raw": raw,
            "fallback_reason": None}


def bounded_cron_projection(projection: dict, limit: int) -> dict:
    """Bound display fields while retaining the exact raw artifact."""
    result = dict(projection)
    for field in ("response", "diagnostics"):
        value = result.get(field)
        if isinstance(value, str) and limit >= 0 and len(value) > limit:
            result[field] = value[:limit]
            result[f"{field}_truncated"] = True
        else:
            result[f"{field}_truncated"] = False
    return result
