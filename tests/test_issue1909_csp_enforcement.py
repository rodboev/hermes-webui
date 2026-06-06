"""Regression tests for enforced CSP alignment with report-only policy (#1909)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.helpers import _security_headers
from server import Handler


class _HeaderCapture:
    def __init__(self):
        self.sent_headers = []

    def send_header(self, key, value):
        self.sent_headers.append((key, value))


def _headers_from_security_helper():
    handler = _HeaderCapture()
    _security_headers(handler)
    return dict(handler.sent_headers)


def test_security_helper_sends_enforcing_csp_with_hardening_directives(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_CSP_CONNECT_EXTRA", raising=False)

    headers = _headers_from_security_helper()

    policy = headers["Content-Security-Policy"]
    assert "default-src 'self' https://*.cloudflareaccess.com" in policy
    assert "base-uri 'self'" in policy
    assert "form-action 'self'" in policy
    assert "manifest-src 'self' https://*.cloudflareaccess.com" in policy
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://static.cloudflareinsights.com blob:" in policy
    assert "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com" in policy
    assert "worker-src blob: 'self' https://cdn.jsdelivr.net" in policy
    assert "font-src 'self' data: https://fonts.gstatic.com" in policy
    assert "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:* https://cdn.jsdelivr.net" in policy


def test_enforcing_csp_honors_valid_extra_connect_origins(monkeypatch):
    monkeypatch.setenv(
        "HERMES_WEBUI_CSP_CONNECT_EXTRA",
        "https://metrics.example.com wss://events.example.com:443",
    )

    headers = _headers_from_security_helper()

    policy = headers["Content-Security-Policy"]
    assert (
        "connect-src 'self' http://127.0.0.1:* http://localhost:* "
        "ws://127.0.0.1:* ws://localhost:* https://cdn.jsdelivr.net "
        "https://metrics.example.com wss://events.example.com:443; "
    ) in policy


def test_enforcing_and_report_only_csp_share_validated_connect_extra(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_CSP_CONNECT_EXTRA", "https://metrics.example.com")

    enforced = _headers_from_security_helper()["Content-Security-Policy"]
    report_only = Handler.csp_report_only_policy()

    assert "https://metrics.example.com" in enforced
    assert "https://metrics.example.com" in report_only


def test_report_only_csp_headers_still_point_to_collector(monkeypatch):
    sent_headers = []
    handler = Handler.__new__(Handler)
    handler.send_header = lambda key, value: sent_headers.append((key, value))
    monkeypatch.setattr(BaseHTTPRequestHandler, "end_headers", lambda self: None)

    Handler.end_headers(handler)

    headers = dict(sent_headers)
    assert "Content-Security-Policy-Report-Only" in headers
    assert headers["Report-To"] == (
        '{"group":"csp-endpoint","max_age":10886400,'
        '"endpoints":[{"url":"/api/csp-report"}]}'
    )
    assert "report-uri /api/csp-report" in headers["Content-Security-Policy-Report-Only"]
    assert "report-to csp-endpoint" in headers["Content-Security-Policy-Report-Only"]
