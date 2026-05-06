from __future__ import annotations

import html


def markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("\n### ", "\n<h3>").replace("\n## ", "\n<h2>").replace("\n# ", "\n<h1>")
    lines = escaped.splitlines()
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            html_lines.append(f'<p class="bullet">{line}</p>')
        elif line.strip():
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)
