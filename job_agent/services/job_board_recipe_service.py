from __future__ import annotations

from dataclasses import MISSING, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup, Tag

from job_agent.models import Job
from job_agent.services.extraction_quality import ExtractionQuality, candidate_quality


@dataclass
class ListingRecipe:
    card_selector: str
    title_selector: str
    link_selector: str
    company_selector: str = ""
    location_selector: str = ""
    remote_selector: str = ""
    rate_selector: str = ""
    workload_selector: str = ""
    posted_date_selector: str = ""
    description_selector: str = ""


@dataclass
class DetailRecipe:
    follow: bool = False
    description_selector: str = ""
    title_selector: str = ""
    location_selector: str = ""
    rate_selector: str = ""
    posted_date_selector: str = ""
    max_detail_pages: int = 5


@dataclass
class RejectRecipe:
    title_exact: list[str] = field(default_factory=list)
    title_contains: list[str] = field(default_factory=list)
    url_contains: list[str] = field(default_factory=list)


@dataclass
class LimitRecipe:
    max_cards: int = 25
    min_title_length: int = 8
    min_description_length: int = 0


@dataclass
class JobBoardRecipe:
    source_name: str
    listing: ListingRecipe
    start_url: str = ""
    detail: DetailRecipe = field(default_factory=DetailRecipe)
    reject: RejectRecipe = field(default_factory=RejectRecipe)
    limits: LimitRecipe = field(default_factory=LimitRecipe)


def load_job_board_recipe(path: Path) -> JobBoardRecipe:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Recipe {path} must be a YAML mapping.")
    return job_board_recipe_from_mapping(data, label=str(path))


def job_board_recipe_from_mapping(data: dict[str, Any], label: str = "recipe") -> JobBoardRecipe:
    listing_data = data.get("listing") or {}
    if not isinstance(listing_data, dict):
        raise ValueError(f"{label}: listing must be a mapping.")
    missing = [
        key
        for key in ["card_selector", "title_selector", "link_selector"]
        if not str(listing_data.get(key, "")).strip()
    ]
    if missing:
        raise ValueError(f"{label}: missing required listing selector(s): {', '.join(missing)}.")

    return JobBoardRecipe(
        source_name=str(data.get("source_name") or "Recipe source").strip(),
        start_url=str(data.get("start_url") or "").strip(),
        listing=ListingRecipe(**_selector_fields(listing_data, ListingRecipe)),
        detail=DetailRecipe(**_selector_fields(_mapping_section(data, "detail"), DetailRecipe)),
        reject=RejectRecipe(**_list_fields(_mapping_section(data, "reject"), RejectRecipe)),
        limits=LimitRecipe(**_int_fields(_mapping_section(data, "limits"), LimitRecipe)),
    )


def extract_jobs_with_recipe(
    html: str,
    base_url: str,
    recipe: JobBoardRecipe,
    source_name: str = "",
) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    cards = soup.select(recipe.listing.card_selector)[: recipe.limits.max_cards]

    for card in cards:
        title = _select_text(card, recipe.listing.title_selector)
        link = _select_href(card, recipe.listing.link_selector)
        url = urljoin(base_url, link) if link else ""
        description = (
            _select_text(card, recipe.listing.description_selector) if recipe.listing.description_selector else ""
        )
        raw_text = card.get_text("\n", strip=True)
        if not description:
            description = raw_text

        if not url or _should_reject(title, url, description, recipe):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        posted_date = _select_text(card, recipe.listing.posted_date_selector)
        job = Job(
            title=title,
            company=_select_text(card, recipe.listing.company_selector) or "Unknown",
            source=source_name or recipe.source_name,
            url=url,
            application_url=url,
            location=_select_text(card, recipe.listing.location_selector) or "Not listed",
            remote=_select_text(card, recipe.listing.remote_selector) or "Not listed",
            rate=_select_text(card, recipe.listing.rate_selector) or "Not listed",
            workload=_select_text(card, recipe.listing.workload_selector) or "Not listed",
            posted_date=posted_date or "Not listed",
            description=description[:3000],
            raw_text=raw_text[:5000],
            source_confidence="recipe",
            freshness_confidence="recipe" if posted_date else "unknown",
            extraction_notes=["Recipe-based extraction; verify details manually."],
        )
        jobs.append(job)
    return jobs


def check_recipe_against_html(html: str, base_url: str, recipe: JobBoardRecipe) -> ExtractionQuality:
    jobs = extract_jobs_with_recipe(html, base_url=base_url, recipe=recipe)
    quality = ExtractionQuality(label=f"Recipe: {recipe.source_name}")
    quality.candidates = [candidate_quality(job) for job in jobs]
    if not jobs:
        quality.warnings.append("Recipe extraction found no matching job cards.")
    return quality


def _selector_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    fields = cls.__dataclass_fields__
    values: dict[str, Any] = {}
    for key, field_info in fields.items():
        if key in data:
            if field_info.default is not MISSING and isinstance(field_info.default, bool):
                values[key] = bool(data[key])
            elif field_info.default is not MISSING and isinstance(field_info.default, int):
                values[key] = int(data[key])
            else:
                values[key] = str(data[key] or "").strip()
    return values


def _mapping_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    value = data.get(section) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a mapping.")
    return value


def _list_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            value = data.get(key) or []
            if not isinstance(value, list):
                raise ValueError(f"reject.{key} must be a list.")
            values[key] = [str(item).strip() for item in value if str(item).strip()]
    return values


def _int_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            try:
                values[key] = int(data[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"limits.{key} must be an integer.") from exc
    return values


def _select_text(root: Tag, selector: str) -> str:
    if not selector:
        return ""
    match = root.select_one(selector)
    return match.get_text(" ", strip=True) if match else ""


def _select_href(root: Tag, selector: str) -> str:
    if not selector:
        return ""
    match = root.select_one(selector)
    if not match:
        return ""
    href = match.get("href")
    if href:
        return str(href).strip()
    nested = match.select_one("[href]")
    return str(nested.get("href", "")).strip() if nested else ""


def _should_reject(title: str, url: str, description: str, recipe: JobBoardRecipe) -> bool:
    normalized_title = " ".join(title.lower().split())
    if len(normalized_title) < recipe.limits.min_title_length:
        return True
    if len(description.strip()) < recipe.limits.min_description_length:
        return True
    title_exact = {" ".join(item.lower().split()) for item in recipe.reject.title_exact}
    if normalized_title in title_exact:
        return True
    if any(fragment.lower() in normalized_title for fragment in recipe.reject.title_contains):
        return True
    lowered_url = url.lower()
    return any(fragment.lower() in lowered_url for fragment in recipe.reject.url_contains)
