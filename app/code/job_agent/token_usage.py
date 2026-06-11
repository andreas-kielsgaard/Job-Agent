from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ROOT
from .paths import output_dir
from .run_store import utc_now


@dataclass
class TokenUsageRecord:
    run_id: str
    purpose: str
    model: str
    associated_job_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost: float | None = None
    timestamp: str = ""


class TokenUsageStore:
    def __init__(self, root: Path = ROOT) -> None:
        self.path = output_dir(root) / "runs" / "token_usage.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: TokenUsageRecord) -> None:
        if not record.timestamp:
            record.timestamp = utc_now()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def list_for_run(self, run_id: str) -> list[TokenUsageRecord]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("run_id") == run_id:
                records.append(TokenUsageRecord(**item))
        return records

    def summarize(self, run_id: str) -> dict[str, int | float | None]:
        records = self.list_for_run(run_id)
        cost_values = [record.estimated_cost for record in records if record.estimated_cost is not None]
        return {
            "call_count": len(records),
            "input_tokens": sum(record.input_tokens for record in records),
            "output_tokens": sum(record.output_tokens for record in records),
            "cache_creation_input_tokens": sum(record.cache_creation_input_tokens for record in records),
            "cache_read_input_tokens": sum(record.cache_read_input_tokens for record in records),
            "estimated_cost": round(sum(cost_values), 6) if cost_values else None,
        }


def token_record_from_anthropic_response(
    *,
    run_id: str,
    purpose: str,
    model: str,
    associated_job_id: str,
    response: object,
) -> TokenUsageRecord:
    usage = getattr(response, "usage", None)
    return TokenUsageRecord(
        run_id=run_id,
        purpose=purpose,
        model=model,
        associated_job_id=associated_job_id,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )
