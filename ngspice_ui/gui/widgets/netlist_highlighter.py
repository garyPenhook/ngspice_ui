from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class SpiceHighlighter(QSyntaxHighlighter):
    """SPICE netlist syntax highlighter."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self._comment_fmt = _fmt("#6a9955", italic=True)
        self._rules = [
            # Continuation line marker
            (re.compile(r"^\+"), _fmt("#569cd6", bold=True)),
            # Dot command keyword (first token on the line)
            (re.compile(r"^\.[a-zA-Z]+"), _fmt("#569cd6", bold=True)),
            # Device-model type keyword after .model
            (
                re.compile(
                    r"\b(?:NPN|PNP|NMOS|PMOS|NJF|PJF|VDMOS|CORE|SW|CSW|LTRA|TRA|URC)\b",
                    re.IGNORECASE,
                ),
                _fmt("#4ec9b0"),
            ),
            # Element reference — first token on a non-comment, non-dot, non-continuation line
            (re.compile(r"^[rclvidbefghjktqmuwxyz]\w*", re.IGNORECASE), _fmt("#ce9178", bold=True)),
            # Engineering numbers (MEG must be tried before bare M)
            (
                re.compile(
                    r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:MEG|[kKmMuUnNpPfFgGtT])?\b",
                    re.IGNORECASE,
                ),
                _fmt("#b5cea8"),
            ),
            # Quoted strings
            (re.compile(r'"[^"]*"'), _fmt("#ce9178")),
        ]

    def highlightBlock(self, text: str) -> None:
        if text.lstrip().startswith("*"):
            self.setFormat(0, len(text), self._comment_fmt)
            return
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        # Inline ; comment overwrites everything after the semicolon
        m = re.search(r";.*", text)
        if m:
            self.setFormat(m.start(), len(text) - m.start(), self._comment_fmt)
