from __future__ import annotations

from job_agent.web.formatting import markdown_to_html


def test_markdown_to_html_renders_basic_blocks() -> None:
    html = markdown_to_html("# Title\n## Section\n### Detail\n- Item\nParagraph")

    assert "<h1>Title</h1>" in html
    assert "<h2>Section</h2>" in html
    assert "<h3>Detail</h3>" in html
    assert '<p class="bullet">- Item</p>' in html
    assert "<p>Paragraph</p>" in html


def test_markdown_to_html_escapes_user_content() -> None:
    html = markdown_to_html("# <script>\nPlain <b>text</b>")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;text&lt;/b&gt;" in html
