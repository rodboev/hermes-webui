"""Regression coverage for workspace CSV preview parity (#4025)."""

from pathlib import Path


WORKSPACE_JS = Path("static/workspace.js").read_text(encoding="utf-8")
UI_JS = Path("static/ui.js").read_text(encoding="utf-8")


def _open_file_block() -> str:
    start = WORKSPACE_JS.index("async function openFile(path, opts={}){")
    end = WORKSPACE_JS.index("\nfunction downloadFile", start)
    return WORKSPACE_JS[start:end]


def test_workspace_csv_branch_precedes_generic_code_fallback():
    block = _open_file_block()
    csv_pos = block.find("} else if(ext==='.csv'){")
    generic_pos = block.find("} else {\n    // Plain code / text -- but fall back to download if server signals binary")

    assert csv_pos != -1, "openFile() should handle .csv before the generic code branch"
    assert generic_pos != -1, "generic code branch missing from openFile()"
    assert csv_pos < generic_pos


def test_workspace_csv_branch_reuses_shared_preview_helper():
    block = _open_file_block()
    csv_pos = block.find("} else if(ext==='.csv'){")
    generic_pos = block.find("} else {\n    // Plain code / text -- but fall back to download if server signals binary")
    branch = block[csv_pos:generic_pos]

    assert "if(renderCsvPreviewContent(path, data.content)) return;" in branch
    assert "renderCodePreviewContent(path, data.content);" in branch
    assert "showPreview('csv');" in WORKSPACE_JS
    assert "$('previewMd').innerHTML=preview.html;" in WORKSPACE_JS
    assert "function buildCsvTablePreview(path, text){" in UI_JS


def test_workspace_csv_error_keys_render_read_only_feedback():
    helper_start = WORKSPACE_JS.index("function renderCsvPreviewContent(path, content){")
    helper_end = WORKSPACE_JS.index("\nfunction forceRenderMarkdownPreview", helper_start)
    helper_body = WORKSPACE_JS[helper_start:helper_end]

    assert "if(preview.errorKey&&typeof _csvPreviewErrorHtml==='function'){" in helper_body
    assert "$('previewMd').innerHTML=_csvPreviewErrorHtml(path, preview.errorKey);" in helper_body
    assert "renderCodePreviewContent(path, data.content);" in _open_file_block()


def test_csv_preview_mode_is_read_only():
    show_start = WORKSPACE_JS.index("function showPreview(mode){")
    show_end = WORKSPACE_JS.index("\nfunction updateEditBtn", show_start)
    show_body = WORKSPACE_JS[show_start:show_end]

    assert "(mode==='md'||mode==='csv')" in show_body
    assert "mode==='csv'?'csv'" in show_body
    assert "const editable = _previewCurrentMode==='code'||_previewCurrentMode==='md';" in WORKSPACE_JS
