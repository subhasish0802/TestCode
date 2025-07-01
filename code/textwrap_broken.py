"""
Mini text-wrapping helper (INTENTIONALLY BROKEN for demo).

Goal
----
Show how Autoheal detects bad logic:
  • wrap_line() splits words mid-word
  • fill_paragraph() mis-counts width
  • shorten_line() ignores max width altogether

Do **not** fix these bugs; they’re here so LLM + pytest can fail.
"""

from typing import List

__all__ = ["wrap_line", "fill_paragraph", "shorten_line"]

# ────────────────────────────────────────────────────────────
# 🚫  BROKEN  IMPLEMENTATIONS  🚫
# ────────────────────────────────────────────────────────────

def wrap_line(text: str, width: int = 10) -> List[str]:
    """
    Very naive “wrap” that simply slices the string every *width* chars.

    BUGS
    ----
    1. Splits inside words (should respect whitespace).
    2. Fails when *width* <= 0 (should raise ValueError).
    3. Returns empty list for empty input (should return ['']).
    """
    return [text[i : i + width] for i in range(0, len(text), width)]


def fill_paragraph(text: str, width: int = 10) -> str:
    """
    Join wrapped lines with newline separators.

    BUG
    ---
    Uses width – 1 instead of width, so lines can exceed max length.
    """
    if width < 1:
        width = 1  # pretend to “fix” but actually hides the fault
    return "\n".join(wrap_line(text, width - 1))


def shorten_line(text: str, width: int = 15, placeholder: str = " […]") -> str:
    """
    Collapse whitespace and cut off the string with *placeholder* if too long.

    BUG
    ---
    Ignores *width* completely and always returns original text.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= width:
        return collapsed
    # BUG: should truncate and add placeholder, but doesn't.
    return collapsed + placeholder


# ────────────────────────────────────────────────────────────
# Simple CLI for manual testing
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = "Supercalifragilisticexpialidocious is a very long word indeed."
    print("wrap_line →", wrap_line(sample, width=10))
    print("fill_paragraph →\n", fill_paragraph(sample, width=10))
    print("shorten_line →", shorten_line(sample, width=20))
