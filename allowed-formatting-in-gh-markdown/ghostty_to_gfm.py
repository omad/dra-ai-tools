#!/usr/bin/env python3
"""
Convert Ghostty HTML clipboard output into GitHub-safe Markdown.

GitHub strips inline styles from issue/comment HTML, so the safest transform is
to recover the text content and emit a fenced code block.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from html.parser import HTMLParser


class GhosttyHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.parts.append(html.unescape(f"&#{name};"))

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text


def code_fence(text: str, language: str) -> str:
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest_run + 1)
    info = language.strip()
    header = f"{fence}{info}" if info else fence
    if not text.endswith("\n"):
        text += "\n"
    return f"{header}\n{text}{fence}\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Ghostty clipboard HTML into GitHub-safe Markdown."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="HTML file to read, or '-' for stdin.",
    )
    parser.add_argument(
        "-l",
        "--language",
        default="text",
        help="Fence language to use in the output, for example: text, diff, console.",
    )
    return parser.parse_args()


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    args = parse_args()
    source = read_input(args.input)

    parser = GhosttyHTMLTextExtractor()
    parser.feed(source)
    parser.close()

    sys.stdout.write(code_fence(parser.get_text(), args.language))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
