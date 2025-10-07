"""
Mini text-wrapping helper with subtle bugsies.

Functions
---------
- wrap_line: naive word-wrapping that fails on long words.
- fill_paragraph: joins wrapped lines but doesn’t respect width correctly.
- shorten_line: truncates without adding the placeholder.
"""

from typing import List

__all__ = ["wrap_line", "fill_paragraph", "shorten_line"]

def wrap_line(text: str, width: int = 10) -> List[str]:
    """
    Split text into lines, trying not to exceed 'width'.
    BUG: Words longer than 'width' are never broken and end up on their own line,
         possibly exceeding the limit.
    """
    lines: List[str] = []
    current = ""
    for word in text.split(" "):
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def fill_paragraph(text: str, width: int = 10) -> str:
    """
    Wraps text then joins with newline.
    BUG: Uses wrap_line exactly, but wrap_line’s logic may produce lines
         longer than 'width' for long words.
    """
    return "\n".join(wrap_line(text, width))

def shorten_line(text: str, width: int = 15, placeholder: str = "...") -> str:
    """
    Collapse whitespace and truncate to 'width', appending 'placeholder'.
    BUG: Returns raw slice instead of adding 'placeholder'.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= width:
        return collapsed
    # should be: return collapsed[: width - len(placeholder)] + placeholder
    return collapsed[:width]
