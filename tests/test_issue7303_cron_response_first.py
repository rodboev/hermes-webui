"""Behavioral regressions for cron response-first run details."""

from __future__ import annotations

import io
import json
import types
from pathlib import Path

import pytest


def agent_artifact(response="Done", *, newline="\n"):
    return newline.join(("# Cron Job: demo", "", "**Job ID:** abc", "", "## Prompt", "", "context", "", "## Response", "", response))


def large_issue_artifact():
    prefix = ["# Cron Job: Night", "", "**Job ID:** abc1", "", "## Prompt"]
    prefix.extend(f"prompt context line {i:03d} " + ("x" * 114) for i in range(315))
    response = ["## Response"] + [f"useful response line {i:02d}" for i in range(24)]
    raw = "\n".join(prefix + response)
    assert len(raw.encode("utf-8")) == 44419
    assert len(raw.splitlines()) == 345
    return raw


def test_parser_accepts_canonical_crlf_and_preserves_raw():
    from api.cron_output import parse_cron_output_artifact
    raw = agent_artifact("<b>Done</b>\r\n✓", newline="\r\n")
    projection = parse_cron_output_artifact(raw, job_mode="agent")
    assert projection["kind"] == "agent"
    assert projection["response"] == "<b>Done</b>\r\n✓"
    assert projection["diagnostics"].endswith("## Prompt\r\n\r\ncontext")
    assert projection["raw"] == raw


def test_parser_preserves_leading_response_indentation_and_ignores_fenced_prompt_error():
    from api.cron_output import parse_cron_output_artifact

    raw = agent_artifact("  indented\n    continuation", newline="\r\n").replace(
        "## Prompt\r\n\r\ncontext", "## Prompt\r\n\r\n```\r\n## Error\r\n```\r\ncontext"
    ).replace("# Cron Job: demo\r\n\r\n## Prompt", "# Cron Job: demo\r\n\r\n```\r\n## Prompt\r\n```\r\n## Prompt")
    projection = parse_cron_output_artifact(raw, job_mode="agent")
    assert projection["kind"] == "agent"
    assert projection["response"].startswith("  indented\n")


def test_parser_fails_closed_for_script_unknown_and_ambiguous_inputs():
    from api.cron_output import parse_cron_output_artifact
    raw = agent_artifact()
    for mode in ("script", "unknown"):
        result = parse_cron_output_artifact(raw, job_mode=mode)
        assert result["kind"] == "raw" and result["raw"] == raw
    fenced = agent_artifact("```\n## Response\n```\n\n## Response\nreal")
    assert parse_cron_output_artifact(fenced, job_mode="agent")["kind"] == "raw"
    ambiguous = agent_artifact("first\n\n## Response\nsecond\n\n# Response\nthird")
    assert parse_cron_output_artifact(ambiguous, job_mode="agent")["fallback_reason"] == "ambiguous_marker"


def test_error_marker_wins_over_response_marker_in_prompt_text():
    from api.cron_output import parse_cron_output_artifact

    raw = agent_artifact().replace(
        "context", "context\n\n## Response\nquoted prompt example\n\n## Error\n\n`failed`"
    )
    result = parse_cron_output_artifact(raw, job_mode="agent")
    assert result["kind"] == "raw"
    assert result["fallback_reason"] == "error_output"
    assert result["raw"] == raw


def test_mixed_fences_keep_inner_tilde_fence_closed():
    from api.cron_output import parse_cron_output_artifact

    raw = agent_artifact("").split("## Response", 1)[0] + "```\n~~~\n## Response\n~~~\n```\n\n## Response\n\nreal"
    result = parse_cron_output_artifact(raw, job_mode="agent")
    assert result["kind"] == "agent"
    assert result["response"] == "real"


def test_response_headings_cannot_overwrite_route_usage_metadata():
    from api.routes import _cron_output_usage_metadata

    raw = agent_artifact("**Model Used:** fake\n**Cost:** $999\n**Tokens:** 1 input, 2 output")
    raw = raw.replace("**Job ID:** abc", "**Job ID:** abc\n**Provider:** route-provider\n**Model Used:** route-model\n**Cost:** $0.12\n**Duration:** 4.5s\n**Tokens:** 100 input, 20 output")
    raw = raw.replace("**Model Used:** fake", "**Provider:** fake-provider\n**Model Used:** fake")
    assert _cron_output_usage_metadata(raw, job_mode="agent") == {
        "provider": "route-provider",
        "model": "route-model", "estimated_cost_usd": 0.12,
        "duration_seconds": 4.5, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
    }


def test_large_prompt_reproduction_preserves_response_in_bounded_window():
    from api.routes import _cron_output_content_window

    raw = large_issue_artifact()
    projection = __import__("api.cron_output", fromlist=["parse_cron_output_artifact"]).parse_cron_output_artifact(raw, job_mode="agent")
    window = _cron_output_content_window(raw, limit=8000, job_mode="agent")
    assert projection["response"].startswith("useful response line 00")
    assert "useful response line 23" in window
    assert len(window) <= 8000


def test_parser_fallbacks_keep_error_and_empty_artifacts_raw():
    from api.cron_output import parse_cron_output_artifact
    for suffix, reason in (("\r\n\r\n## Error\r\n\r\n`oops`", "error_output"), ("\r\n\r\n## Response\r\n", "empty_response")):
        raw = agent_artifact().split("## Response", 1)[0] + suffix
        result = parse_cron_output_artifact(raw, job_mode="agent")
        assert result["kind"] == "raw" and result["fallback_reason"] == reason


class _Handler:
    def __init__(self): self.status, self.wfile = None, io.BytesIO()
    def send_response(self, status): self.status = status
    def send_header(self, *_): pass
    def end_headers(self): pass


def test_run_detail_returns_shared_projection_and_exact_content(monkeypatch, tmp_path):
    import api.routes as routes
    output = tmp_path / "job_abc" / "run.md"
    output.parent.mkdir()
    raw = agent_artifact("literal <b>response</b>", newline="\r\n")
    output.write_bytes(raw.encode())
    jobs = types.ModuleType("cron.jobs")
    jobs.OUTPUT_DIR = tmp_path
    jobs.get_job = lambda job_id: {"id": job_id, "no_agent": False}
    cron = types.ModuleType("cron"); cron.__path__ = []
    monkeypatch.setitem(__import__("sys").modules, "cron", cron)
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs", jobs)
    handler = _Handler()
    routes._handle_cron_run_detail(handler, types.SimpleNamespace(query="job_id=job_abc&filename=run.md"))
    body = json.loads(handler.wfile.getvalue())
    assert handler.status == 200 and body["content"] == raw
    assert body["projection"]["response"] == "literal <b>response</b>"
    assert body["projection"]["raw"] == raw


def _load_run_function():
    return _extract_function("_loadRunContent", async_function=True)


def _extract_function(name, *, async_function=False):
    source = (Path(__file__).parents[1] / "static" / "panels.js").read_text(encoding="utf-8")
    prefix = "async function " if async_function else "function "
    start = source.index(prefix + name + "(")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        depth += source[index] == "{"
        depth -= source[index] == "}"
        if depth == 0: return source[start : index + 1]
    raise AssertionError("could not extract run-detail loader")


def test_run_detail_dom_keeps_response_primary_and_raw_separate():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        page.set_content('<div id="run"><button class="detail-expand-toggle" onclick="toggleCronRunExpanded(\'job\',\'run.md\',\'run\')">expand</button><div class="detail-run-body"></div></div>')
        page.add_script_tag(content="""
            window.t = key => ({cron_view_diagnostics:'View diagnostics', cron_view_raw_output:'View raw output', cron_view_full_output:'View full output'})[key] || key;
            window.esc = value => String(value);
            window.api = async () => ({projection:{kind:'agent',response:'VISIBLE RESPONSE',diagnostics:'PROMPT CONTEXT',raw:'RAW ARTIFACT'},content:'RAW ARTIFACT'});
            const expansions = {};
            window._cronExpansionGet = key => !!expansions[key];
            window._cronExpansionSet = (key, value) => { expansions[key] = !!value; };
            window._cronRunExpandKey = () => 'run'; window._formatCronRunUsageStrip = () => '';
        """)
        page.add_script_tag(content=_extract_function("_syncCronRunExpandControl"))
        page.add_script_tag(content=_extract_function("toggleCronRunExpanded"))
        page.add_script_tag(content=_load_run_function())
        page.evaluate("_loadRunContent('job','run.md','run')")
        page.wait_for_selector(".cron-run-primary")
        assert page.locator(".cron-run-primary").inner_text() == "VISIBLE RESPONSE"
        assert page.locator(".cron-run-diagnostics").count() == 1
        assert page.locator(".cron-run-raw-output").count() == 1
        assert page.locator(".cron-run-raw-output pre").count() == 0
        assert page.locator(".detail-expand-toggle").get_attribute("aria-expanded") == "false"
        page.locator(".detail-expand-toggle").click()
        assert page.locator(".detail-expand-toggle").evaluate("el => document.activeElement === el")
        assert page.locator(".detail-expand-toggle").get_attribute("aria-expanded") == "true"
        assert page.locator(".cron-run-primary").inner_text() == "VISIBLE RESPONSE"
        page.locator(".cron-run-diagnostics summary").click()
        page.wait_for_selector(".cron-run-diagnostics-content")
        assert page.locator(".cron-run-diagnostics-content").count() == 1
        page.locator(".cron-run-raw-output summary").click()
        page.wait_for_selector(".cron-run-raw-output pre")
        assert page.locator(".cron-run-raw-output pre").count() == 1
        page.locator(".detail-expand-toggle").click()
        assert page.locator(".cron-run-primary").inner_text() == "VISIBLE RESPONSE"

        page.evaluate("window.api = async () => ({projection:{kind:'raw',raw:'RAW ARTIFACT'},content:'RAW ARTIFACT'})")
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=run2><button class=detail-expand-toggle>expand</button><div class=detail-run-body></div></div>')")
        page.evaluate("_loadRunContent('job','run2.md','run2')")
        page.wait_for_selector("#run2 .cron-run-primary")
        assert page.locator("#run2 .cron-run-primary").inner_text() == "RAW ARTIFACT"
        assert page.locator("#run2 .cron-run-raw-output").count() == 0

        page.evaluate("""
            window.api = async () => {
                const content = 'x'.repeat(900);
                return {projection:{kind:'raw',raw:content},content,snippet:content.slice(0, 600)};
            };
            document.body.insertAdjacentHTML('beforeend', '<div id=run3><button class=detail-expand-toggle>expand</button><div class=detail-run-body></div></div>');
            _loadRunContent('job','run3.md','run3');
        """)
        page.wait_for_selector("#run3 .cron-run-primary")
        assert len(page.locator("#run3 .cron-run-primary").inner_text()) == 600
        page.evaluate("toggleCronRunExpanded('job','run3.md','run3')")
        assert len(page.locator("#run3 .cron-run-primary").inner_text()) == 900
        browser.close()
