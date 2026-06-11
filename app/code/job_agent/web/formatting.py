from __future__ import annotations

import html


def markdown_to_html(text: str) -> str:
    html_lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            html_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            html_lines.append(f'<p class="bullet">{html.escape(line)}</p>')
        elif line.strip():
            html_lines.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(html_lines)
