from __future__ import annotations

from typing import Any

from .models import MatchResult


def match_score_fields(match: MatchResult, existing_index: dict[str, Any] | None = None) -> dict[str, int | None]:
    deterministic_score = int(match.total_score)
    ai_score = _ai_match_score(existing_index or {})
    display_score = round((deterministic_score + ai_score) / 2) if ai_score is not None else deterministic_score
    return {
        "match_score": display_score,
        "deterministic_match_score": deterministic_score,
        "ai_match_score": ai_score,
    }


def match_index_fields(match: MatchResult, existing_index: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        **match_score_fields(match, existing_index),
        "match_category": match.category,
        "recommended_angle": match.recommended_angle,
        "concerns": match.concerns,
        "condition_exclusions": match.condition_exclusions,
        "condition_preferences": match.condition_preferences,
        "condition_values": match.condition_values,
    }


def _ai_match_score(values: dict[str, Any]) -> int | None:
    value = values.get("ai_match_score")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
