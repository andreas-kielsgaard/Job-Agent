from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from job_agent.services.recipes.mapping import _selectors
from job_agent.services.recipes.models import (
    ApplicationEntry,
    JobBoardRecipe,
    PaginationLink,
    SelectorValue,
)


def find_pagination_links(html: str, base_url: str, recipe: JobBoardRecipe) -> list[PaginationLink]:
    selectors = _selectors(recipe.pagination.page_link_selector) + _selectors(recipe.pagination.next_selector)
    if not selectors:
        return []

    soups = _selectable_soups(html)
    next_urls: set[str] = set()
    for soup in soups:
        next_urls.update(_selected_urls(soup, recipe.pagination.next_selector, base_url))
    next_url_keys = {_pagination_url_key(url) for url in next_urls}
    links: list[PaginationLink] = []
    seen_urls: set[str] = set()
    for selector in selectors:
        for soup in soups:
            for match in soup.select(selector):
                href = match.get("href")
                if not href:
                    continue
                url = urljoin(base_url, str(href).strip())
                url_key = _pagination_url_key(url)
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                label = match.get_text(" ", strip=True) or url
                links.append(
                    PaginationLink(
                        label=label,
                        url=url,
                        is_next=url_key in next_url_keys or _looks_like_next_link(label, match),
                    )
                )
    return links


def discover_pagination_links(html: str, base_url: str) -> list[PaginationLink]:
    links: list[PaginationLink] = []
    seen_urls: set[str] = set()
    for soup in _selectable_soups(html):
        for match in soup.find_all("a", href=True):
            label = match.get_text(" ", strip=True)
            href = str(match.get("href", "")).strip()
            haystack = " ".join(
                [
                    label,
                    href,
                    " ".join(match.get("class", [])),
                    str(match.get("rel") or ""),
                    str(match.get("aria-label") or ""),
                ]
            ).lower()
            if not _looks_like_pagination(label, href, haystack):
                continue
            url = urljoin(base_url, href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            links.append(PaginationLink(label=label or url, url=url, is_next=_looks_like_next_link(label, match)))
    return links


def discover_interactive_pagination_controls(html: str) -> list[str]:
    controls: list[str] = []
    seen: set[str] = set()
    for soup in _selectable_soups(html):
        for match in soup.find_all(["a", "button", "input", "div", "span"]):
            if match.name == "input" and str(match.get("type") or "").lower() not in {"button", "submit"}:
                continue
            if match.name in {"div", "span"} and str(match.get("role") or "").lower() != "button":
                continue
            label = match.get_text(" ", strip=True) or str(match.get("value") or match.get("aria-label") or "")
            href = str(match.get("href") or "").strip()
            onclick = str(match.get("onclick") or match.get("onClick") or "")
            data_attrs = " ".join(str(value) for key, value in match.attrs.items() if str(key).startswith("data-"))
            classes = " ".join(match.get("class", []))
            haystack = " ".join([label, href, onclick, data_attrs, classes]).lower()
            if href and not href.startswith("#") and not href.lower().startswith("javascript:"):
                continue
            if not (
                _looks_like_pagination(label, href, haystack)
                or "pagination" in haystack
                or "paginator" in haystack
                or "load more" in haystack
            ):
                continue
            key = f"{match.name}:{label}:{href}:{onclick}:{data_attrs}:{classes}"
            if key in seen:
                continue
            seen.add(key)
            controls.append(label or href or onclick or data_attrs or classes)
    return controls


def discover_visible_total_job_count(html: str) -> int:
    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    if not text:
        return 0
    count = r"([\d][\d,.]{0,8})"
    patterns = [
        rf"\b{count}\s+(?:open\s+)?(?:jobs?|postings?|positions?|projects?|results?|vacancies)\b",
        rf"\b(?:jobs?|postings?|positions?|projects?|results?|vacancies)\s*(?:found|available|listed|matching|match)?\s*[:\-]?\s*{count}\b",
        rf"\bshowing\s+{count}\s*(?:-|to|\u2013|\u2014)\s*{count}\s+of\s+{count}\b",
    ]
    counts: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = _parse_visible_count(match.groups()[-1])
            if 0 < value < 10000:
                counts.append(value)
    return max(counts) if counts else 0


def discover_application_entries(html: str, base_url: str) -> list[ApplicationEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ApplicationEntry] = []
    seen: set[tuple[str, str]] = set()
    for element in soup.find_all(["a", "button", "input"]):
        label = element.get_text(" ", strip=True) or str(element.get("value") or element.get("aria-label") or "")
        href = str(element.get("href") or "").strip()
        onclick = str(element.get("onclick") or element.get("onClick") or "")
        data_attrs = " ".join(str(value) for key, value in element.attrs.items() if str(key).startswith("data-"))
        haystack = " ".join([label, href, onclick, data_attrs, " ".join(element.get("class", []))]).lower()
        if not any(term in haystack for term in ["apply", "application", "contact-button", "send application"]):
            continue
        if element.name == "input" and str(element.get("type") or "").lower() in {"hidden", "checkbox"}:
            continue
        url = urljoin(base_url, href) if href and not href.startswith("javascript:") else ""
        key = (label, url or onclick)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            ApplicationEntry(
                label=label.strip() or "Application entry",
                url=url,
                kind=element.name or "element",
                detail=onclick[:180] if onclick else data_attrs[:180],
            )
        )
    return entries


def _login_gate_detected(html: str) -> bool:
    lowered = html.lower()
    soup = BeautifulSoup(html, "html.parser")
    visible_text = soup.get_text(" ", strip=True).lower()
    gate_phrases = [
        "sign up free to see more results",
        "sign up to see more results",
        "create your account to see more results",
        "log in to see more",
        "login to see more",
        "sign in to see more",
        "register to see more",
        "quota limit reached",
    ]
    if any(phrase in visible_text for phrase in gate_phrases):
        return True
    if "modal-backdrop show" in lowered and any(token in lowered for token in ["registration", "login", "sign up"]):
        return True
    return '"user":null' in lowered and "create your account to see more results" in lowered


def _pagination_urls_to_fetch(links: list[PaginationLink], max_pages: int | None) -> list[str]:
    additional_page_count = len(links) if max_pages is None else max(0, max_pages - 1)
    ordered = sorted(links, key=_pagination_sort_key)
    urls: list[str] = []
    seen: set[str] = set()
    for link in ordered:
        if _page_number_from_url_or_label(link.url, link.label) == 1:
            continue
        url_key = _pagination_url_key(link.url)
        if url_key in seen:
            continue
        seen.add(url_key)
        urls.append(link.url)
        if len(urls) >= additional_page_count:
            break
    return urls


def _pagination_url_key(url: str) -> str:
    def normalized_query_items() -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            value = value.strip()
            if value in {"", "0", "false", "False"}:
                continue
            items.append((key, value))
        return sorted(items)

    parsed = urlparse(urldefrag(url).url)
    query = urlencode(normalized_query_items(), doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", query, ""))


def _pagination_sort_key(link: PaginationLink) -> tuple[bool, int, str]:
    page_number = _page_number_from_url_or_label(link.url, link.label)
    return (not link.is_next, page_number or 1_000_000, link.url)


def _page_number_from_url_or_label(url: str, label: str) -> int:
    match = re.search(r"(?:[?&]pagenr=|/page/)(\d+)", url)
    if match:
        return int(match.group(1))
    stripped = label.strip()
    return int(stripped) if stripped.isdigit() else 0


def _parse_visible_count(value: str) -> int:
    try:
        return int(value.replace(",", "").replace(".", ""))
    except ValueError:
        return 0


def _selectable_soups(html: str) -> list[BeautifulSoup]:
    soups = [BeautifulSoup(html, "html.parser")]
    for fragment in _embedded_html_fragments(html):
        soups.append(BeautifulSoup(fragment, "html.parser"))
    return soups


def _embedded_html_fragments(html: str) -> list[str]:
    fragments: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type=lambda value: value and "json" in value):
        raw = script.string or script.get_text("", strip=True)
        if not raw or "<a" not in raw:
            continue
        fragments.extend(_strings_containing_links(raw))
    return fragments


def _strings_containing_links(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw] if "<a" in raw else []
    fragments: list[str] = []
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str) and "<a" in value:
            fragments.append(value)
    return fragments


def _looks_like_pagination(label: str, href: str, haystack: str) -> bool:
    if re.fullmatch(r"\d+", label.strip()):
        return True
    return any(token in haystack for token in ["page-numbers", "pagenr=", "pagination", "paginator", "/page/"])


def _looks_like_next_link(label: str, match: Tag) -> bool:
    classes = {str(item).lower() for item in match.get("class", [])}
    rel = match.get("rel") or []
    rel_values = {str(item).lower() for item in rel} if isinstance(rel, list) else {str(rel).lower()}
    haystack = " ".join([label, str(match.get("aria-label") or ""), str(match.get("href") or "")]).lower()
    return "next" in classes or "next" in rel_values or "next" in haystack


def _selected_urls(soup: BeautifulSoup, selector: SelectorValue, base_url: str) -> set[str]:
    urls: set[str] = set()
    for css_selector in _selectors(selector):
        for match in soup.select(css_selector):
            href = match.get("href")
            if href:
                urls.add(urljoin(base_url, str(href).strip()))
    return urls
