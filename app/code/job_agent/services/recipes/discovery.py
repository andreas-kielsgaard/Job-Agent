from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from job_agent.services.recipes.mapping import _selectors
from job_agent.services.recipes.models import (
    ApplicationEntry,
    JobBoardRecipe,
    PaginationLink,
    SelectorValue,
)
from job_agent.services.recipes.soup import is_stable_css_class, parse_markup


@dataclass(frozen=True)
class ListingExpansionDiscovery:
    links: list[PaginationLink]
    selector: str = ""


@dataclass(frozen=True)
class FeedDiscovery:
    links: list[PaginationLink]
    selector: str = ""


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
                url = _pagination_url_from_href(str(href).strip(), base_url)
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
            url = _pagination_url_from_href(href, base_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            links.append(PaginationLink(label=label or url, url=url, is_next=_looks_like_next_link(label, match)))
    return links


def discover_listing_expansion(html: str, base_url: str) -> ListingExpansionDiscovery:
    matches: list[Tag] = []
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
                    _ancestor_class_text(match),
                ]
            ).lower()
            if not _looks_like_listing_expansion(label, href, haystack):
                continue
            url = _pagination_url_from_href(href, base_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            matches.append(match)
            links.append(PaginationLink(label=label or url, url=url, is_next=False))
    return ListingExpansionDiscovery(links=links, selector=_listing_expansion_selector(matches))


def discover_listing_expansion_links(html: str, base_url: str) -> list[PaginationLink]:
    return discover_listing_expansion(html, base_url).links


def discover_feed_links(html: str, base_url: str) -> FeedDiscovery:
    matches: list[Tag] = []
    links: list[PaginationLink] = []
    seen_urls: set[str] = set()
    base_host = urlparse(base_url).netloc.lower()
    for soup in _selectable_soups(html):
        for match in soup.find_all(["a", "link"], href=True):
            href = str(match.get("href") or "").strip()
            label = match.get_text(" ", strip=True) or str(match.get("title") or match.get("aria-label") or "")
            haystack = " ".join(
                [
                    label,
                    href,
                    " ".join(match.get("class", [])),
                    str(match.get("rel") or ""),
                    str(match.get("type") or ""),
                    str(match.get("aria-label") or ""),
                ]
            ).lower()
            if not _looks_like_feed_link(href, haystack):
                continue
            url = urljoin(base_url, href)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if base_host and parsed.netloc.lower() != base_host:
                continue
            url_key = _pagination_url_key(url)
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            matches.append(match)
            links.append(PaginationLink(label=label or url, url=url, is_next=False))
    return FeedDiscovery(links=links, selector=_feed_selector(matches))


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
    soup = parse_markup(html)
    for element in soup.find_all(["script", "style", "noscript", "template"]):
        element.decompose()
    scoped_texts = [
        " ".join(element.get_text(" ", strip=True).split())
        for selector in ("main", "[role='main']")
        for element in soup.select(selector)
    ]
    for text in scoped_texts:
        total = _discover_visible_total_job_count_from_text(text)
        if total:
            return total
    for element in soup.find_all(["footer", "header", "nav", "aside"]):
        element.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return _discover_visible_total_job_count_from_text(text)


def _discover_visible_total_job_count_from_text(text: str) -> int:
    if not text:
        return 0
    count = r"([\d][\d,.]{0,8})"
    result_word = (
        r"(?:jobs?(?!\s*(?:@|seekers?\b|by\s+email\b))|postings?|positions?|projects?|"
        r"(?:search\s+)?results?|vacancies)"
    )
    pattern_groups = [
        [
            rf"\bshowing\s+{count}\s*(?:-|to|\u2013|\u2014)\s*{count}\s+of\s+{count}\s+{result_word}\b",
        ],
        [
            rf"\b{count}\s+(?:open|live|matching|available|listed)?\s*{result_word}\b",
            rf"\b{count}\s+{result_word}\s*(?:found|available|listed|matching|matched)\b",
            rf"\b{result_word}\s*(?:found|available|listed|matching|matched|total)?\s*[:\-]?\s*{count}\b",
        ],
    ]
    for patterns in pattern_groups:
        counts: list[int] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                if not _visible_total_match_allowed(text, match):
                    continue
                value = _parse_visible_count(match.groups()[-1])
                if 0 < value < 10000:
                    counts.append(value)
        if counts:
            return max(counts)
    return 0


def _visible_total_match_allowed(text: str, match: re.Match[str]) -> bool:
    start, end = match.span()
    nearby = text[max(0, start - 48) : min(len(text), end + 48)].lower()
    matched_and_after = (text[start:end] + text[end : min(len(text), end + 24)]).lower()
    if "@" in text[start : min(len(text), end + 32)]:
        return False
    if re.search(r"\bjobs?\s+(?:seekers?|by\s+email)\b", matched_and_after):
        return False
    contact_terms = ("phone", "tel", "telephone", "call", "contact", "address", "email")
    result_terms = ("search result", "results found", "jobs found", "showing")
    return not (any(term in nearby for term in contact_terms) and not any(term in nearby for term in result_terms))


def discover_application_entries(html: str, base_url: str) -> list[ApplicationEntry]:
    soup = parse_markup(html)
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
    soup = parse_markup(html)
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
    match = re.search(r"(?:[?&](?:page|pagenr)=|/page/)(\d+)", url)
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
    soups = [parse_markup(html)]
    for fragment in _embedded_html_fragments(html):
        soups.append(parse_markup(fragment))
    return soups


def _embedded_html_fragments(html: str) -> list[str]:
    fragments: list[str] = []
    soup = parse_markup(html)
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
        return _has_pagination_context(href, haystack)
    if _looks_like_listing_expansion(label, href, haystack):
        return True
    if _looks_like_listing_expansion_link(label, href):
        return True
    return any(token in haystack for token in ["page-numbers", "pagenr=", "page=", "pagination", "paginator", "/page/"])


def _looks_like_listing_expansion_link(label: str, href: str) -> bool:
    normalized = " ".join(label.lower().split())
    if not normalized or href.lower().split("?", 1)[0].endswith(".rss"):
        return False
    return bool(re.search(r"\bview all\s+\d+.+\bjobs?\b", normalized))


def _has_pagination_context(href: str, haystack: str) -> bool:
    value = f"{href} {haystack}".lower()
    return any(token in value for token in ["page-numbers", "pagenr=", "page=", "pagination", "paginator", "/page/"])


def _looks_like_listing_expansion(label: str, href: str, haystack: str) -> bool:
    normalized_label = re.sub(r"\s+", " ", label.lower()).strip()
    normalized_href = href.lower().strip()
    if not normalized_label or not normalized_href:
        return False
    if normalized_href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    if not _contains_listing_term(f"{normalized_label} {normalized_href}"):
        return False
    count_with_term = bool(
        re.search(
            r"\b\d{1,5}\b[^\n\r]{0,80}\b"
            r"(?:jobs?|postings?|positions?|roles?|vacanc(?:y|ies)|projects?|openings?|opportunities)\b",
            normalized_label,
        )
    )
    expansion_verb = bool(
        re.search(r"\b(?:view|see|show|browse|find|explore|load)\s+(?:all|more|\d{1,5})\b", normalized_label)
    )
    all_count_label = bool(re.search(r"\ball\s+\d{1,5}\b", normalized_label))
    category_hint = any(token in haystack for token in ["view-all", "show-all", "all-jobs", "/categor"])
    return bool(
        (count_with_term and (expansion_verb or all_count_label or category_hint)) or (expansion_verb and category_hint)
    )


def _looks_like_feed_link(href: str, haystack: str) -> bool:
    normalized_href = href.lower().strip()
    if not normalized_href or normalized_href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    path = urlparse(normalized_href).path.lower()
    if path.endswith((".rss", ".atom")):
        return True
    if path.endswith(".xml") and any(token in haystack for token in ["rss", "atom", "feed", "jobs", "postings"]):
        return True
    return "application/rss+xml" in haystack or "application/atom+xml" in haystack


def _contains_listing_term(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:jobs?|postings?|positions?|roles?|vacanc(?:y|ies)|projects?|openings?|opportunities)\b",
            value,
        )
    )


def _ancestor_class_text(match: Tag) -> str:
    parts: list[str] = []
    current = match.parent
    depth = 0
    while isinstance(current, Tag) and depth < 3:
        parts.extend(str(item) for item in current.get("class", []))
        if current.get("id"):
            parts.append(str(current.get("id")))
        current = current.parent
        depth += 1
    return " ".join(parts)


def _listing_expansion_selector(matches: list[Tag]) -> str:
    if not matches:
        return ""
    parent_selector = _common_parent_link_selector(matches)
    href_selector = _common_href_selector(matches)
    if parent_selector and href_selector:
        return f"{parent_selector} {href_selector}"
    if parent_selector:
        return f"{parent_selector} a[href]"
    return href_selector or "a[href]"


def _feed_selector(matches: list[Tag]) -> str:
    if not matches:
        return ""
    names = {str(match.name or "") for match in matches}
    hrefs = [str(match.get("href") or "").lower().strip() for match in matches]
    suffixes = [
        suffix
        for suffix in [".rss", ".atom", ".xml"]
        if hrefs and all(urlparse(href).path.endswith(suffix) for href in hrefs)
    ]
    suffix_selector = f'[href$="{suffixes[0]}"]' if suffixes else "[href]"
    if names == {"a"}:
        return f"a{suffix_selector}"
    if names == {"link"}:
        if all("rss" in str(match.get("type") or "").lower() for match in matches):
            return f'link[type*="rss"]{suffix_selector}'
        if all("atom" in str(match.get("type") or "").lower() for match in matches):
            return f'link[type*="atom"]{suffix_selector}'
        return f"link{suffix_selector}"
    if "a" in names:
        return f"a{suffix_selector}"
    return f"{sorted(names)[0]}{suffix_selector}"


def _common_parent_link_selector(matches: list[Tag]) -> str:
    parents = [match.parent for match in matches if isinstance(match.parent, Tag)]
    if not parents:
        return ""
    first_name = str(parents[0].name or "")
    if not first_name or any(str(parent.name or "") != first_name for parent in parents):
        return ""
    common_classes = set(_stable_classes(parents[0]))
    for parent in parents[1:]:
        common_classes.intersection_update(_stable_classes(parent))
    if common_classes:
        return f"{first_name}.{sorted(common_classes)[0]}"
    return first_name if len(matches) >= 2 and first_name in {"li", "tr", "article"} else ""


def _common_href_selector(matches: list[Tag]) -> str:
    hrefs = [str(match.get("href") or "").strip() for match in matches]
    path_segments = [_first_stable_path_segment(href) for href in hrefs]
    common_segments = {segment for segment in path_segments if segment}
    if len(common_segments) == 1:
        return f'a[href*="/{common_segments.pop()}/"]'
    common_classes = set(_stable_classes(matches[0]))
    for match in matches[1:]:
        common_classes.intersection_update(_stable_classes(match))
    if common_classes:
        return f"a.{sorted(common_classes)[0]}[href]"
    return "a[href]"


def _first_stable_path_segment(href: str) -> str:
    path = urlparse(href).path.lower()
    for segment in [item for item in path.split("/") if item]:
        if segment not in {"en", "gb", "uk", "us", "de", "fr", "nl", "remote"}:
            return segment
    return ""


def _stable_classes(tag: Tag) -> list[str]:
    return [str(item).strip() for item in tag.get("class", []) if is_stable_css_class(str(item))]


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
                urls.add(_pagination_url_from_href(str(href).strip(), base_url))
    return urls


def _pagination_url_from_href(href: str, base_url: str) -> str:
    url = urljoin(base_url, href)
    parsed = urlparse(url)
    if not parsed.path.lower().rstrip("/").endswith("/undefined") and not href.lower().startswith("undefined?"):
        return url
    page_number = _query_page_number(parsed.query)
    if page_number <= 0:
        return url
    return _base_url_with_page(base_url, page_number)


def _base_url_with_page(base_url: str, page_number: int) -> str:
    parsed = urlparse(base_url)
    items = parse_qsl(parsed.query, keep_blank_values=True)
    updated: list[tuple[str, str]] = []
    replaced = False
    for key, value in items:
        if key.lower() in {"page", "pagenr"} and not replaced:
            updated.append((key, str(page_number)))
            replaced = True
        else:
            updated.append((key, value))
    if not replaced:
        updated.append(("page", str(page_number)))
    return urlunparse(parsed._replace(query=urlencode(updated, doseq=True)))


def _query_page_number(query: str) -> int:
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.lower() in {"page", "pagenr"} and value.isdigit():
            return int(value)
    return 0
