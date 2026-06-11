from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from job_agent.models import Job
from job_agent.services.extraction_quality import title_quality
from job_agent.services.recipes.mapping import _selectors
from job_agent.services.recipes.models import (
    ApiFieldMapping,
    JobBoardRecipe,
    ListingExtractionStats,
    PatternsRecipe,
    SelectorValue,
)


def extract_jobs_with_recipe_with_stats(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
    use_recipe_card_limit: bool = True,
) -> tuple[list[Job], ListingExtractionStats]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    cards = soup.select(recipe.listing.card_selector)
    card_limit = recipe.limits.max_cards if use_recipe_card_limit else 0
    stats = ListingExtractionStats(page_url=base_url, observed_cards=len(cards), limit=card_limit)

    for index, card in enumerate(cards):
        title = select_text(card, recipe.listing.title_selector)
        link = select_href(card, recipe.listing.link_selector)
        url = urljoin(base_url, link) if link else ""
        description = (
            select_text(card, recipe.listing.description_selector) if recipe.listing.description_selector else ""
        )
        raw_text = card.get_text("\n", strip=True)
        if not description:
            description = raw_text

        pattern_values = extract_pattern_values(raw_text, recipe.patterns)
        title = pattern_values.get("title") or title

        if not url:
            stats.missing_url_count += 1
            continue
        if should_reject(title, url, description, recipe):
            stats.rejected_count += 1
            continue
        if url in seen_urls:
            stats.duplicate_count += 1
            continue
        seen_urls.add(url)

        posted_date = select_text(card, recipe.listing.posted_date_selector)
        start_date = select_text(card, recipe.listing.start_date_selector) or pattern_values.get("start_date")
        job = Job(
            title=title,
            company=select_text(card, recipe.listing.company_selector) or "Unknown",
            source=source_name or recipe.source_name,
            url=url,
            application_url=url,
            location=select_text(card, recipe.listing.location_selector)
            or pattern_values.get("location")
            or "Not listed",
            remote=select_text(card, recipe.listing.remote_selector) or pattern_values.get("remote") or "Not listed",
            rate=select_text(card, recipe.listing.rate_selector) or pattern_values.get("rate") or "Not listed",
            start_date=start_date or "Not listed",
            workload=(
                select_text(card, recipe.listing.workload_selector)
                or pattern_values.get("workload")
                or pattern_values.get("work_type")
                or "Not listed"
            ),
            posted_date=posted_date or pattern_values.get("posted_date") or "Not listed",
            languages=[pattern_values["language"]] if pattern_values.get("language") else [],
            description=description[:3000],
            raw_text=raw_text[:5000],
            source_confidence="recipe",
            freshness_confidence="recipe" if posted_date or pattern_values.get("posted_date") else "unknown",
            extraction_notes=extraction_notes(pattern_values),
        )
        jobs.append(job)
        if card_limit and len(jobs) >= card_limit:
            stats.limit_skipped_count += max(0, len(cards) - index - 1)
            break
    stats.extracted_jobs = len(jobs)
    return jobs, stats


def extract_jobs_from_api_payload_with_stats(
    payload: Any,
    base_url: str,
    recipe: JobBoardRecipe,
    *,
    source_name: str = "",
    use_recipe_card_limit: bool = True,
) -> tuple[list[Job], ListingExtractionStats, int]:
    records = json_path(payload, recipe.listing_api.results_path)
    if not isinstance(records, list):
        records = []
    total = json_path(payload, recipe.listing_api.total_path) if recipe.listing_api.total_path else 0
    total_count = _int_value(total)
    stats = ListingExtractionStats(
        page_url=base_url,
        observed_cards=len(records),
        limit=recipe.limits.max_cards if use_recipe_card_limit else 0,
    )
    jobs = jobs_from_api_records(
        records,
        base_url,
        recipe,
        source_name=source_name,
        stats=stats,
        use_recipe_card_limit=use_recipe_card_limit,
    )
    stats.extracted_jobs = len(jobs)
    return jobs, stats, total_count


def jobs_from_api_records(
    records: list[Any],
    base_url: str,
    recipe: JobBoardRecipe,
    *,
    source_name: str = "",
    stats: ListingExtractionStats | None = None,
    use_recipe_card_limit: bool = True,
) -> list[Job]:
    stats = stats or ListingExtractionStats(page_url=base_url, observed_cards=len(records))
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    card_limit = recipe.limits.max_cards if use_recipe_card_limit else 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            stats.rejected_count += 1
            continue
        job = job_from_api_record(record, base_url, recipe, source_name=source_name)
        if not job.url:
            stats.missing_url_count += 1
            continue
        if should_reject(job.title, job.url, job.description, recipe):
            stats.rejected_count += 1
            continue
        if job.url in seen_urls:
            stats.duplicate_count += 1
            continue
        seen_urls.add(job.url)
        jobs.append(job)
        if card_limit and len(jobs) >= card_limit:
            stats.limit_skipped_count += max(0, len(records) - index - 1)
            break
    return jobs


def job_from_api_record(
    record: dict[str, Any],
    base_url: str,
    recipe: JobBoardRecipe,
    *,
    source_name: str = "",
) -> Job:
    fields = recipe.listing_api.fields
    title = _api_text(record, fields.title)
    url = _api_url(record, fields, base_url)
    description = _api_description(record, fields)
    raw_text = _api_text(record, fields.raw_text) or description or json.dumps(record, ensure_ascii=False)
    job_id = _api_text(record, fields.job_id)
    notes = ["Recipe API extraction; verify details manually."]
    if job_id:
        notes.append(f"Recipe extracted job ID: {job_id}")
    return Job(
        title=title,
        company=_api_text(record, fields.company) or "Unknown",
        recruiter=_api_text(record, fields.recruiter),
        end_client=_api_text(record, fields.end_client),
        source=source_name or recipe.source_name,
        url=url,
        application_url=_api_text(record, fields.application_url) or url,
        location=_api_text(record, fields.location) or "Not listed",
        remote=_api_text(record, fields.remote) or "Not listed",
        rate=_api_text(record, fields.rate) or "Not listed",
        contract_duration=_api_text(record, fields.contract_duration) or "Not listed",
        start_date=_api_text(record, fields.start_date) or "Not listed",
        posted_date=_api_text(record, fields.posted_date) or "Not listed",
        deadline=_api_text(record, fields.deadline) or "Not listed",
        workload=_api_text(record, fields.workload) or "Not listed",
        languages=_api_list(record, fields.languages),
        description=html_to_text(description)[:3000],
        raw_text=html_to_text(raw_text)[:5000],
        source_confidence="recipe-api",
        freshness_confidence="recipe" if _api_text(record, fields.posted_date) else "unknown",
        extraction_notes=notes,
    )


def apply_detail_api_record(job: Job, record: Any, recipe: JobBoardRecipe) -> dict[str, str]:
    if not isinstance(record, dict):
        return {}
    fields = recipe.detail_api.fields
    found_values = {
        "title": _api_text(record, fields.title),
        "description": _api_description(record, fields),
        "location": _api_text(record, fields.location),
        "remote": _api_text(record, fields.remote),
        "rate": _api_text(record, fields.rate),
        "workload": _api_text(record, fields.workload),
        "posted_date": _api_text(record, fields.posted_date),
        "start_date": _api_text(record, fields.start_date),
        "languages": _api_text(record, fields.languages),
    }
    found_values = {field_name: value for field_name, value in found_values.items() if value}
    if found_values.get("title"):
        job.title = found_values["title"]
    if found_values.get("description"):
        cleaned_description = html_to_text(found_values["description"])
        job.description = cleaned_description[:3000]
        job.raw_text = cleaned_description[:5000]
    for field_name in ["location", "remote", "rate", "workload", "posted_date", "start_date"]:
        if found_values.get(field_name) and getattr(job, field_name) == "Not listed":
            setattr(job, field_name, found_values[field_name])
    if found_values.get("languages") and not job.languages:
        job.languages = [found_values["languages"]]
    if found_values and "Detail API fetched by recipe; verify details manually." not in job.extraction_notes:
        job.extraction_notes.append("Detail API fetched by recipe; verify details manually.")
    return found_values


def json_path(value: Any, path: str) -> Any:
    path = str(path or "").strip()
    if not path or path == "$":
        return value
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:].lstrip(".")
    current = value
    for token in [part for part in path.split(".") if part]:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def render_template_from_record(template: str, record: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = json_path(record, match.group(1))
        return "" if value is None else str(value)

    return re.sub(r"\{([^{}]+)\}", replace, template)


def render_template_value(value: Any, context: dict[str, Any] | None) -> Any:
    if context is None:
        return value
    if isinstance(value, str):
        return render_template_from_record(value, context)
    if isinstance(value, list):
        return [render_template_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_template_value(item, context) for key, item in value.items()}
    return value


def _api_url(record: dict[str, Any], fields: ApiFieldMapping, base_url: str) -> str:
    if fields.url_template:
        return urljoin(base_url, render_template_from_record(fields.url_template, record))
    url = _api_text(record, fields.url)
    return urljoin(base_url, url) if url else ""


def _api_description(record: dict[str, Any], fields: ApiFieldMapping) -> str:
    return _api_text(record, fields.description_html) or _api_text(record, fields.description)


def _api_text(record: dict[str, Any], path: str) -> str:
    if not path:
        return ""
    value = json_path(record, path)
    return _string_value(value)


def _api_list(record: dict[str, Any], path: str) -> list[str]:
    value = json_path(record, path) if path else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _string_value(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;/]", text) if item.strip()]


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_string_value(item) for item in value if _string_value(item))
    if isinstance(value, dict):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def apply_detail_html(job: Job, html: str, recipe: JobBoardRecipe) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    schema_values = extract_jobposting_json_ld(soup) if recipe.detail.use_json_ld else {}
    detail_root = detail_root_for(soup)
    pattern_values = extract_pattern_values(detail_root.get_text(" ", strip=True), recipe.patterns)
    found_values = {
        "title": select_detail_text(detail_root, soup, recipe.detail.title_selector)
        or schema_values.get("title", "")
        or pattern_values.get("title", ""),
        "description": select_detail_text(detail_root, soup, recipe.detail.description_selector)
        or schema_values.get("description", ""),
        "location": select_detail_text(detail_root, soup, recipe.detail.location_selector)
        or schema_values.get("location", "")
        or pattern_values.get("location", ""),
        "remote": select_detail_text(detail_root, soup, recipe.detail.remote_selector)
        or schema_values.get("remote", "")
        or pattern_values.get("remote", ""),
        "rate": select_detail_text(detail_root, soup, recipe.detail.rate_selector)
        or schema_values.get("rate", "")
        or pattern_values.get("rate", ""),
        "workload": select_detail_text(detail_root, soup, recipe.detail.workload_selector)
        or schema_values.get("workload", "")
        or pattern_values.get("workload", "")
        or pattern_values.get("work_type", ""),
        "posted_date": select_detail_text(detail_root, soup, recipe.detail.posted_date_selector)
        or schema_values.get("posted_date", "")
        or pattern_values.get("posted_date", ""),
        "start_date": select_detail_text(detail_root, soup, recipe.detail.start_date_selector)
        or schema_values.get("start_date", "")
        or pattern_values.get("start_date", ""),
        "languages": select_detail_text(detail_root, soup, recipe.detail.language_selector)
        or schema_values.get("language", "")
        or pattern_values.get("language", ""),
    }
    found_values = {field_name: value for field_name, value in found_values.items() if value}

    if found_values.get("title"):
        job.title = found_values["title"]
    if found_values.get("description"):
        cleaned_description = html_to_text(found_values["description"])
        job.description = cleaned_description[:3000]
        job.raw_text = cleaned_description[:5000]
    if found_values.get("location") and job.location == "Not listed":
        job.location = found_values["location"]
    if found_values.get("remote") and job.remote == "Not listed":
        job.remote = found_values["remote"]
    if found_values.get("rate") and job.rate == "Not listed":
        job.rate = found_values["rate"]
    if found_values.get("workload") and job.workload == "Not listed":
        job.workload = found_values["workload"]
    if found_values.get("posted_date") and job.posted_date == "Not listed":
        job.posted_date = found_values["posted_date"]
        job.freshness_confidence = "recipe"
    if found_values.get("start_date") and job.start_date == "Not listed":
        job.start_date = found_values["start_date"]
    if found_values.get("languages") and not job.languages:
        job.languages = [found_values["languages"]]
    if found_values and "Detail page fetched by recipe; verify details manually." not in job.extraction_notes:
        job.extraction_notes.append("Detail page fetched by recipe; verify details manually.")
    return found_values


def has_detail_selectors(recipe: JobBoardRecipe) -> bool:
    if recipe.detail.use_json_ld:
        return True
    if any(
        pattern
        for pattern in [
            recipe.patterns.title_regex,
            recipe.patterns.location_regex,
            recipe.patterns.remote_regex,
            recipe.patterns.rate_regex,
            recipe.patterns.workload_regex,
            recipe.patterns.posted_date_regex,
            recipe.patterns.start_date_regex,
            recipe.patterns.language_regex,
            recipe.patterns.work_type_regex,
        ]
    ):
        return True
    return any(
        _selectors(selector)
        for selector in [
            recipe.detail.description_selector,
            recipe.detail.title_selector,
            recipe.detail.location_selector,
            recipe.detail.remote_selector,
            recipe.detail.rate_selector,
            recipe.detail.workload_selector,
            recipe.detail.posted_date_selector,
            recipe.detail.start_date_selector,
            recipe.detail.language_selector,
        ]
    )


def canonical_url(soup: BeautifulSoup, base_url: str) -> str:
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get("href"):
        return urljoin(base_url, str(canonical.get("href", "")).strip())
    return base_url


def detail_root_for(soup: BeautifulSoup) -> Tag:
    for selector in [".job-single", ".project-show-single-page"]:
        match = soup.select_one(selector)
        if match:
            return match
    detail_body = soup.select_one(".project-body")
    if detail_body:
        modal = detail_body.find_parent(class_="modal")
        if isinstance(modal, Tag):
            return modal
    return soup.body or soup


def select_detail_text(detail_root: Tag, soup: BeautifulSoup, selector: SelectorValue) -> str:
    return select_text(detail_root, selector) or select_text(soup, selector)


def select_text(root: Tag, selector: SelectorValue) -> str:
    for css_selector in _selectors(selector):
        match = root if matches_selector(root, css_selector) else root.select_one(css_selector)
        text = match.get_text(" ", strip=True) if match else ""
        if text:
            return text
    return ""


def select_href(root: Tag, selector: SelectorValue) -> str:
    for css_selector in _selectors(selector):
        match = root if matches_selector(root, css_selector) else root.select_one(css_selector)
        if not match:
            continue
        href = match.get("href")
        if href:
            return str(href).strip()
        nested = match.select_one("[href]")
        if nested and nested.get("href"):
            return str(nested.get("href", "")).strip()
    return ""


def matches_selector(tag: Tag, selector: str) -> bool:
    selector = selector.strip()
    if selector == tag.name:
        return True
    if selector.startswith("."):
        return selector[1:] in tag.get("class", [])
    if selector.startswith("#"):
        return str(tag.get("id", "")) == selector[1:]
    if "." in selector and " " not in selector and ">" not in selector:
        tag_name, class_name = selector.split(".", 1)
        return tag.name == tag_name and class_name in tag.get("class", [])
    return False


def should_reject(title: str, url: str, description: str, recipe: JobBoardRecipe) -> bool:
    normalized_title = " ".join(title.lower().split())
    if len(normalized_title) < recipe.limits.min_title_length:
        return True
    if title_quality(title) == "generic":
        return True
    if len(description.strip()) < recipe.limits.min_description_length:
        return True
    lowered_url = url.lower()
    if recipe.accept.url_contains and not any(
        fragment.lower() in lowered_url for fragment in recipe.accept.url_contains
    ):
        return True
    if recipe.accept.title_contains and not any(
        fragment.lower() in normalized_title for fragment in recipe.accept.title_contains
    ):
        return True
    title_exact = {" ".join(item.lower().split()) for item in recipe.reject.title_exact}
    if normalized_title in title_exact:
        return True
    if any(fragment.lower() in normalized_title for fragment in recipe.reject.title_contains):
        return True
    return any(fragment.lower() in lowered_url for fragment in recipe.reject.url_contains)


def extract_pattern_values(text: str, patterns: PatternsRecipe) -> dict[str, str]:
    values: dict[str, str] = {}
    pattern_map = {
        "title": patterns.title_regex,
        "job_id": patterns.job_id_regex,
        "location": patterns.location_regex,
        "remote": patterns.remote_regex,
        "rate": patterns.rate_regex,
        "workload": patterns.workload_regex,
        "posted_date": patterns.posted_date_regex,
        "start_date": patterns.start_date_regex,
        "language": patterns.language_regex,
        "work_type": patterns.work_type_regex,
    }
    for field_name, pattern in pattern_map.items():
        if not pattern:
            continue
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            continue
        value = regex_value(match, field_name)
        if value:
            values[field_name] = value
    return values


def regex_value(match: re.Match[str], field_name: str) -> str:
    groups = match.groupdict()
    if groups.get(field_name):
        return clean_pattern_value(groups[field_name])
    for value in groups.values():
        if value:
            return clean_pattern_value(value)
    for value in match.groups():
        if value:
            return clean_pattern_value(value)
    return clean_pattern_value(match.group(0))


def clean_pattern_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" :-\n\t")
    arrangement_labels = {
        "remote": "Remote",
        "hybrid": "Hybrid",
        "hybrid-remote": "Hybrid",
        "office based": "Office based",
    }
    return arrangement_labels.get(cleaned.lower(), cleaned)


def extraction_notes(pattern_values: dict[str, str]) -> list[str]:
    notes = ["Recipe-based extraction; verify details manually."]
    if pattern_values.get("job_id"):
        notes.append(f"Recipe extracted job ID: {pattern_values['job_id']}")
    if pattern_values.get("work_type"):
        notes.append(f"Recipe extracted work type: {pattern_values['work_type']}")
    return notes


def extract_jobposting_json_ld(soup: BeautifulSoup) -> dict[str, str]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text("", strip=True)
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            fallback = extract_loose_jobposting_json(raw)
            if fallback:
                return fallback
            continue
        posting = find_jobposting(data)
        if not posting:
            continue
        return {
            "title": json_text(posting.get("title")),
            "description": html_to_text(json_text(posting.get("description"))),
            "posted_date": json_text(posting.get("datePosted")),
            "workload": employment_type(posting.get("employmentType")),
            "location": job_location(posting.get("jobLocation")),
            "rate": base_salary(posting.get("baseSalary")),
        }
    return {}


def extract_loose_jobposting_json(raw: str) -> dict[str, str]:
    if "JobPosting" not in raw:
        return {}
    return {
        "title": loose_json_string(raw, "title"),
        "description": html_to_text(loose_json_string(raw, "description")),
        "posted_date": loose_json_string(raw, "datePosted"),
        "workload": employment_type(loose_json_string(raw, "employmentType")),
        "location": loose_json_string(raw, "addressCountry"),
        "rate": loose_base_salary(raw),
    }


def find_jobposting(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        item_type = value.get("@type")
        item_types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(item).lower() == "jobposting" for item in item_types):
            return value
        graph_match = find_jobposting(value.get("@graph"))
        if graph_match:
            return graph_match
        for child in value.values():
            child_match = find_jobposting(child)
            if child_match:
                return child_match
    if isinstance(value, list):
        for item in value:
            item_match = find_jobposting(item)
            if item_match:
                return item_match
    return None


def json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(text for item in value if (text := json_text(item)))
    if isinstance(value, dict):
        return ""
    return str(value).strip()


def loose_json_string(raw: str, key: str) -> str:
    pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"])*)"'
    match = re.search(pattern, raw, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group(1).replace("\\/", "/").replace('\\"', '"')
    return re.sub(r"\s+", " ", value).strip()


def html_to_text(value: str) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def employment_type(value: Any) -> str:
    text = json_text(value)
    if text:
        return text.replace("_", " ").title()
    return ""


def job_location(value: Any) -> str:
    locations = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, dict):
            location_parts = [
                json_text(address.get("addressLocality")),
                json_text(address.get("addressRegion")),
                json_text(address.get("addressCountry")),
            ]
            text = ", ".join(part for part in location_parts if part)
            if text:
                parts.append(text)
        elif address:
            parts.append(json_text(address))
    return "; ".join(parts)


def base_salary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    currency = json_text(value.get("currency"))
    salary_value = value.get("value")
    amount = ""
    unit = ""
    if isinstance(salary_value, dict):
        amount = json_text(salary_value.get("value"))
        unit = json_text(salary_value.get("unitText"))
        min_value = json_text(salary_value.get("minValue"))
        max_value = json_text(salary_value.get("maxValue"))
        if not amount and (min_value or max_value):
            amount = f"{min_value}-{max_value}".strip("-")
    else:
        amount = json_text(salary_value)
    if not amount:
        return ""
    return " ".join(part for part in [currency, amount, unit] if part)


def loose_base_salary(raw: str) -> str:
    currency = loose_json_string(raw, "currency")
    unit = loose_json_string(raw, "unitText")
    value = loose_json_string(raw, "value")
    if not value:
        return ""
    return " ".join(part for part in [currency, value, unit] if part)
