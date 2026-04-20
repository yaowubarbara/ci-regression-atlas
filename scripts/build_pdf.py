#!/usr/bin/env python3
"""Build rlvr_mapping.pdf from rlvr_mapping.md with compact A4 CSS."""
from __future__ import annotations

import pathlib
import sys

import markdown
from weasyprint import HTML, CSS

ROOT = pathlib.Path("/home/dev/codspeed-atlas")
MD = ROOT / "rlvr_mapping.md"
PDF = ROOT / "rlvr_mapping.pdf"

CSS_TEXT = """
@page {
    size: A4;
    margin: 0.9cm 1.0cm 0.9cm 1.0cm;
}
html { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; }
body { font-size: 8.4pt; line-height: 1.22; color: #0a0a0a; }
h1 { font-size: 13pt; margin: 0 0 0.1em 0; border-bottom: 1.5px solid #0a0a0a; padding-bottom: 0.1em; }
h2 { font-size: 9.5pt; margin: 0.55em 0 0.2em 0; border-bottom: 1px solid #ddd; padding-bottom: 0.05em; }
h3 { font-size: 9pt; margin: 0.4em 0 0.1em 0; }
p { margin: 0.18em 0; }
ul, ol { margin: 0.15em 0 0.2em 1.1em; padding: 0; }
li { margin: 0.05em 0; }
blockquote { border-left: 2px solid #bbb; margin: 0.15em 0; padding: 0.02em 0.5em; color: #555; font-size: 7.7pt; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 7.5pt; background: #f3f3f3; padding: 0 0.15em; border-radius: 2px; }
pre { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 7.5pt; background: #f7f7f7; padding: 0.3em; border-radius: 3px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 7.3pt; margin: 0.2em 0; }
th, td { border: 1px solid #ccc; padding: 0.18em 0.3em; vertical-align: top; text-align: left; }
th { background: #eaeaea; font-weight: 600; }
hr { border: 0; border-top: 1px solid #ccc; margin: 0.4em 0; }
strong { color: #000; }
a { color: #0057b7; text-decoration: none; }
"""


def main() -> int:
    text = MD.read_text()
    html_body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    html = f"<html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    HTML(string=html, base_url=str(ROOT)).write_pdf(
        str(PDF),
        stylesheets=[CSS(string=CSS_TEXT)],
    )
    print(f"Wrote {PDF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
