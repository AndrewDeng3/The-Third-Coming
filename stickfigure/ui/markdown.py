"""A small Markdown -> Qt rich-text HTML converter for chat messages.

Covers what chat replies use: fenced code blocks, inline code, **bold**, *italic*, ~~strike~~,
headings, bullet/numbered lists, block quotes, links, and horizontal rules. Everything is escaped
first, so model output can never inject markup. Unfinished fences (mid-stream) render as code too.
"""

from __future__ import annotations

import html
import re

CODE_BG = "#15161a"
CODE_FG = "#e6e1cf"
INLINE_BG = "#2a2b32"
QUOTE_FG = "#b8b8c4"
LINK = "#7fb2ff"

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_AUTOLINK = re.compile(r"(?<![\"'>=])(https?://[^\s<]+[^\s<.,;:!?)\]])")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)\s*$")


def inline(text: str) -> str:
    """Escape, then apply inline formatting (code spans are protected from the other rules)."""
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(keep, text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}" style="color:{LINK}">{m.group(1)}</a>', text)
    text = _AUTOLINK.sub(lambda m: f'<a href="{m.group(1)}" style="color:{LINK}">{m.group(1)}</a>', text)
    text = _BOLD.sub(r"<b>\2</b>", text)
    text = _ITALIC.sub(r"<i>\2</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    return re.sub(
        "\x00(\\d+)\x00",
        lambda m: (f'<code style="background:{INLINE_BG}; font-family:Consolas,monospace;">'
                   f"&nbsp;{html.escape(codes[int(m.group(1))], quote=False)}&nbsp;</code>"),
        text,
    )


def _code_block(lines: list[str], lang: str) -> str:
    body = html.escape("\n".join(lines), quote=False) or "&nbsp;"
    label = (f'<div style="color:#8a8a96; font-size:8pt;">{html.escape(lang)}</div>' if lang else "")
    return (f'<table width="100%" cellpadding="8" cellspacing="0" style="background:{CODE_BG}; margin:4px 0;">'
            f'<tr><td>{label}<pre style="white-space:pre-wrap; font-family:Consolas,monospace; font-size:9.5pt; color:{CODE_FG}; '
            f'margin:0;">{body}</pre></td></tr></table>')


def to_html(text: str) -> str:
    out: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    para: list[str] = []
    list_kind: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<p style='margin:2px 0;'>" + "<br>".join(inline(p) for p in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush_para()
            close_list()
            marker, lang = fence.group(1), fence.group(2)
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                code.append(lines[i])
                i += 1
            out.append(_code_block(code, lang))
            i += 1  # skip the closing fence (or run off the end mid-stream)
            continue
        if not line.strip():
            flush_para()
            close_list()
            i += 1
            continue
        if _RULE.match(line):
            flush_para()
            close_list()
            out.append("<hr>")
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            flush_para()
            close_list()
            size = {1: "13pt", 2: "12pt", 3: "11pt"}.get(len(m.group(1)), "10.5pt")
            out.append(f"<p style='margin:6px 0 2px 0; font-size:{size}; font-weight:700;'>{inline(m.group(2))}</p>")
            i += 1
            continue
        b, n = _BULLET.match(line), _NUMBERED.match(line)
        if b or n:
            flush_para()
            kind = "ul" if b else "ol"
            if list_kind != kind:
                close_list()
                start = f' start="{n.group(2)}"' if n else ""
                out.append(f"<{kind}{start} style='margin:2px 0 2px -18px;'>")
                list_kind = kind
            out.append(f"<li>{inline(b.group(2) if b else n.group(3))}</li>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush_para()
            close_list()
            out.append(f"<p style='margin:2px 0; color:{QUOTE_FG};'>▎ {inline(line.lstrip()[1:].strip())}</p>")
            i += 1
            continue
        close_list()
        para.append(line)
        i += 1
    flush_para()
    close_list()
    return "".join(out)


def plain(text: str) -> str:
    """Markdown flattened for a speech bubble / TTS: no symbols, code blocks summarized."""
    text = re.sub(r"(```|~~~)[\s\S]*?(\1|$)", " (code's in the chat) ", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = re.sub(r"(\*\*|__|~~)", "", text)
    text = re.sub(r"(?<![\w*])[*_](?=\S)(.+?)(?<=\S)[*_](?![\w*])", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "• ", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", text)).strip()
