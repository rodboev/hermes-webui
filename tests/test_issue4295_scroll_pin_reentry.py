"""
Tests for issue #4295 — viewport jumps while reading streamed reply.

When reading earlier parts of a long streaming reply, the viewport should not
jump to the bottom due to content growth. The scroll handler guards re-pinning
with a _messageUserUnpinned check and requires a 30px minimum scroll-up distance
to avoid touch jitter unpinning.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent


def _ui_js() -> str:
    return (ROOT / "static" / "ui.js").read_text(encoding="utf-8")


class TestScrollPinReentry:
    def test_near_bottom_repin_guarded_by_user_unpinned(self):
        """The _nearBottomCount>=2 re-pin block must check !_messageUserUnpinned before setting _scrollPinned=true."""
        src = _ui_js()
        # Find the near-bottom detection block with _nearBottomCount>=2
        pattern_idx = src.find("if(_nearBottomCount>=2)")
        assert pattern_idx != -1, (
            "_nearBottomCount>=2 block not found in ui.js"
        )
        # Extract a reasonable window around this block to verify the guard
        block_window = src[pattern_idx:pattern_idx + 500]
        # The block should contain an inner guard: if(!_messageUserUnpinned)
        # before _scrollPinned=true
        assert "if(!_messageUserUnpinned)" in block_window, (
            "The _nearBottomCount>=2 block must guard _scrollPinned=true with "
            "if(!_messageUserUnpinned) to prevent re-pinning after user scroll-up"
        )
        # Also verify _scrollPinned=true is present
        assert "_scrollPinned=true" in block_window, (
            "_scrollPinned=true should be present in the guarded block"
        )

    def test_scroll_up_threshold_prevents_jitter_unpin(self):
        """The scroll-up condition must require at least 30px displacement to avoid touch jitter."""
        src = _ui_js()
        # Find the _recordNonMessageScrollIntent function
        func_idx = src.find("function _recordNonMessageScrollIntent")
        assert func_idx != -1, (
            "_recordNonMessageScrollIntent function not found in ui.js"
        )
        # Extract the function body (reasonable window)
        func_window = src[func_idx:func_idx + 800]
        # The function should check e.deltaY < -30, not just e.deltaY < 0
        # to filter out small jitter movements
        assert "e.deltaY< -30" in func_window or "e.deltaY < -30" in func_window, (
            "The scroll-up condition must require e.deltaY < -30 "
            "(at least 30px upward) to avoid touch jitter false unpins"
        )
