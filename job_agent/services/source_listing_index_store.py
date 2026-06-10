from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json
from job_agent.models import Job
from job_agent.run_store import utc_now
from job_agent.store import JobStore


@dataclass
class SourceListingIndexRecord:
    listing_key: str
    title: str
    url: str
    source: str
    source_id: str
    first_indexed_at: str
    last_indexed_at: str
    posting_status: str = "active"


@dataclass
class SourceListingIndexSummary:
    source_id: str
    source_name: str = ""
    indexed_count: int = 0
    last_indexed_at: str = ""
    listings: list[SourceListingIndexRecord] = field(default_factory=list)
    no_longer_posted_count: int = 0

    @property
    def is_indexed(self) -> bool:
        return self.indexed_count > 0

    @property
    def active_listings(self) -> list[SourceListingIndexRecord]:
        return [listing for listing in self.listings if listing.posting_status != "no_longer_posted"]

    @property
    def status_label(self) -> str:
        return "Indexed" if self.is_indexed else "Not indexed"


class SourceListingIndexStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = root / "jobs" / "source_listing_index.json"

    def summary_for_source(self, source_id: str, source_name: str = "") -> SourceListingIndexSummary:
        source_data = self._sources().get(source_id, {})
        return self._summary_from_source_data(source_id, source_data, source_name=source_name)

    def summaries_by_source(self) -> dict[str, SourceListingIndexSummary]:
        sources = self._sources()
        return {
            str(source_id): self._summary_from_source_data(str(source_id), data)
            for source_id, data in sources.items()
            if isinstance(data, dict)
        }

    def _summary_from_source_data(
        self,
        source_id: str,
        source_data: dict[str, Any],
        *,
        source_name: str = "",
    ) -> SourceListingIndexSummary:
        listings = [
            SourceListingIndexRecord(**item)
            for item in source_data.get("listings", [])
            if isinstance(item, dict) and item.get("listing_key")
        ]
        return SourceListingIndexSummary(
            source_id=source_id,
            source_name=str(source_data.get("source_name") or source_name or source_id),
            indexed_count=sum(1 for listing in listings if listing.posting_status != "no_longer_posted"),
            last_indexed_at=str(source_data.get("last_indexed_at") or ""),
            listings=listings,
            no_longer_posted_count=sum(1 for listing in listings if listing.posting_status == "no_longer_posted"),
        )

    def record_index(self, *, source_id: str, source_name: str, jobs: list[Job]) -> SourceListingIndexSummary:
        data = self._data()
        sources = data.setdefault("sources", {})
        if not isinstance(sources, dict):
            sources = {}
            data["sources"] = sources
        now = utc_now()
        existing = sources.get(source_id, {})
        existing_by_key = {
            str(item.get("listing_key")): item
            for item in existing.get("listings", [])
            if isinstance(item, dict) and item.get("listing_key")
        }
        current_keys: set[str] = set()
        listings: dict[str, SourceListingIndexRecord] = {}
        for job in jobs:
            listing_key = JobStore.listing_key(job)
            current_keys.add(listing_key)
            previous = existing_by_key.get(listing_key, {})
            listings[listing_key] = SourceListingIndexRecord(
                listing_key=listing_key,
                title=job.title,
                url=job.url or job.application_url,
                source=job.source,
                source_id=job.source_id or source_id,
                first_indexed_at=str(previous.get("first_indexed_at") or now),
                last_indexed_at=now,
                posting_status="active",
            )
        for listing_key, previous in existing_by_key.items():
            if listing_key in current_keys:
                continue
            listings[listing_key] = SourceListingIndexRecord(
                listing_key=listing_key,
                title=str(previous.get("title") or "Unknown posting"),
                url=str(previous.get("url") or ""),
                source=str(previous.get("source") or source_name or source_id),
                source_id=str(previous.get("source_id") or source_id),
                first_indexed_at=str(previous.get("first_indexed_at") or now),
                last_indexed_at=str(previous.get("last_indexed_at") or now),
                posting_status="no_longer_posted",
            )
        sources[source_id] = {
            "source_id": source_id,
            "source_name": source_name or source_id,
            "last_indexed_at": now,
            "indexed_count": len(current_keys),
            "listings": [asdict(item) for item in sorted(listings.values(), key=lambda item: item.title.lower())],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, data)
        return self.summary_for_source(source_id, source_name)

    def _data(self) -> dict[str, Any]:
        data = read_json(self.path, {"sources": {}}, strict=True)
        return data if isinstance(data, dict) else {"sources": {}}

    def _sources(self) -> dict[str, Any]:
        sources = self._data().get("sources", {})
        return sources if isinstance(sources, dict) else {}

    def list_all(self) -> list[SourceListingIndexSummary]:
        return [
            self._summary_from_source_data(str(source_id), data, source_name=str(data.get("source_name") or source_id))
            for source_id, data in self._sources().items()
            if isinstance(data, dict)
        ]
