"""Shared parsing and presentation projection for persisted cron output."""

from __future__ import annotations

import re


_PREAMBLE = re.compile(r"^# Cron Job:[^\r\n]*\r?$", re.MULTILINE)
_PROMPT = re.compile(r"^## Prompt[ \t]*\r?$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,2} Response[ \t]*\r?$", re.MULTILINE)
_ERROR = re.compile(r"^#{1,2} Error[ \t]*\r?$", re.MULTILINE)
_CRON_JOB_LINE = re.compile(r"^# Cron Job:[^\r\n]*\r?$")
_JOB_ID_LINE = re.compile(r"^\*\*Job ID:\*\*[ \t]*[^\r\n]+\r?$")
_RUN_TIME_LINE = re.compile(r"^\*\*Run Time:\*\*[ \t]*[^\r\n]+\r?$")
_SCHEDULE_LINE = re.compile(r"^\*\*Schedule:\*\*[ \t]*[^\r\n]+\r?$")
_MODE_SCRIPT_LINE = re.compile(r"^\*\*Mode:\*\*[ \t]*no_agent \(script\)[ \t]*\r?$")


def _opening_lines(text: str):
    """Yield only top-level metadata lines before artifact content begins."""
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped == "---" or stripped in ("## Prompt", "## Response", "## Error"):
            if stripped == "## Prompt":
                yield stripped
            break
        yield stripped


def resolve_cron_artifact_mode(text: str, *, legacy_job_mode: str = "unknown") -> str:
    """Resolve one artifact's mode, using the current job only for legacy files."""
    raw = text if isinstance(text, str) else str(text or "")
    lines = list(_opening_lines(raw))
    if not lines or not _CRON_JOB_LINE.fullmatch(lines[0]):
        return legacy_job_mode if legacy_job_mode in ("agent", "script") else "unknown"

    positions = {}
    counts = {"job_id": 0, "run_time": 0, "schedule": 0, "mode": 0}
    for index, line in enumerate(lines[1:], 1):
        for name, pattern in (("job_id", _JOB_ID_LINE), ("run_time", _RUN_TIME_LINE),
                              ("schedule", _SCHEDULE_LINE), ("mode", _MODE_SCRIPT_LINE)):
            if pattern.fullmatch(line):
                counts[name] += 1
                positions.setdefault(name, index)
                break

    if all(name in positions for name in ("job_id", "run_time", "mode")) and not any(counts[name] > 1 for name in positions):
        if positions["job_id"] < positions["run_time"] < positions["mode"]:
            return "script"
    if all(name in positions for name in ("job_id", "run_time", "schedule")) and not any(counts[name] > 1 for name in positions):
        prompt = next((index for index, line in enumerate(lines[1:], 1) if _PROMPT.fullmatch(line)), None)
        if (positions["job_id"] < positions["run_time"] < positions["schedule"] and
                prompt is not None and positions["schedule"] < prompt):
            return "agent"
    return "unknown"


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
