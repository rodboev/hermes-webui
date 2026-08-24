"""Headless browser contract tests for capability-only dismissal rendering."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is required")


def _function(name):
    source = (ROOT / "static/ui.js").read_text(encoding="utf-8")
    start = source.find(f"function {name}(")
    assert start >= 0
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{": depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(name)


def _run(source):
    result = subprocess.run([NODE], input=source, cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _preamble():
    funcs = "\n".join(_function(name) for name in ("_isProviderErrorCardMessage", "_messageIsRenderable", "_providerErrorDismissalButtonHtml"))
    return funcs + """
function msgContent(m){return String(m&&m.content||'').trim();}
function _isContextCompactionMessage(){return false;}
function _isPreservedCompressionTaskListMessage(){return false;}
function _isRecoveryControlMessage(){return false;}
function _messageHasReasoningPayload(){return false;}
function _assistantMessageHasVisibleContent(m){return !!msgContent(m);}
function _isReadOnlySession(){return false;}
function t(k){return k;}
function esc(v){return String(v||'');}
function li(){return '<svg></svg>';}
"""


def test_only_server_reference_renders_without_content_or_index():
    result = _run(_preamble() + """
global.S={session:{session_id:'s1'},busy:false,activeStreamId:null};
const ref='a'.repeat(64);
const button=_providerErrorDismissalButtonHtml({_provider_error_dismiss_ref:ref},0,false);
console.log(JSON.stringify({button,hasContent:button.includes('provider failed'),hasIndex:button.includes('message-index'),hasRef:button.includes('data-dismiss-ref')}));
""")
    assert result["hasContent"] is False and result["hasIndex"] is False and result["hasRef"] is True


def test_dismissed_provider_marker_hides_only_marked_row_and_ordinary_tombstone_survives():
    result = _run(_preamble() + """
global.S={session:{session_id:'s1'},busy:false,activeStreamId:null};
console.log(JSON.stringify({provider:_messageIsRenderable({role:'assistant',content:'provider failed',_provider_error_dismissed:true}),ordinary:_messageIsRenderable({_dismissed:true,role:'assistant',content:'answer'}),button:_providerErrorDismissalButtonHtml({_provider_error_dismissed:true},0,false)}));
""")
    assert result == {"provider": False, "ordinary": True, "button": ""}
