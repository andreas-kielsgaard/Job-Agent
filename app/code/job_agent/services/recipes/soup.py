from __future__ import annotations

import re

from bs4 import BeautifulSoup, FeatureNotFound


def parse_markup(value: str) -> BeautifulSoup:
    parser = "xml" if _looks_like_xml_feed(value) else "html.parser"
    try:
        return BeautifulSoup(value, parser)
    except FeatureNotFound:
        return BeautifulSoup(value, "html.parser")


def _looks_like_xml_feed(value: str) -> bool:
    prefix = str(value or "").lstrip()[:500].lower()
    return prefix.startswith("<?xml") or "<rss" in prefix or "<feed" in prefix


def is_stable_css_class(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"^[0-9]|reactrenderer|^css-|^js-|^ng-|^hydrated$", text, re.I):
        return False
    if re.fullmatch(r"sc-[A-Za-z0-9]+", text):
        return False
    return not (re.fullmatch(r"[A-Za-z]{4,10}", text) and re.search(r"[A-Z]", text) and re.search(r"[a-z]", text))


def selector_mentions_unstable_css_class(selector: str) -> bool:
    for match in re.finditer(r"\.([_A-Za-z][-_A-Za-z0-9]*)", str(selector or "")):
        if not is_stable_css_class(match.group(1)):
            return True
    return False
