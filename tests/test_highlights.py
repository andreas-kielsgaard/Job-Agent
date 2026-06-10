from __future__ import annotations

from job_agent.highlights import build_match_highlights
from job_agent.models import Job, MatchResult


def test_strong_match_produces_highlight() -> None:
    reasons = build_match_highlights(
        Job(title="SAP ABAP Consultant"),
        MatchResult(total_score=70, category="strong"),
        _profile(),
    )

    assert "strong match category" in reasons


def test_score_above_configured_threshold_produces_highlight() -> None:
    reasons = build_match_highlights(
        Job(title="SAP Consultant"),
        MatchResult(total_score=82, category="exploratory"),
        _profile(highlight_score=80),
    )

    assert "score 82% meets highlight threshold" in reasons


def test_remote_preferred_location_and_part_time_produce_highlights() -> None:
    reasons = build_match_highlights(
        Job(title="SAP Consultant", location="Denmark", remote="Fully remote", workload="Part-time 60%"),
        MatchResult(total_score=65, category="exploratory"),
        _profile(),
    )

    assert "fully remote" in reasons
    assert "reduced workload or part-time" in reasons
    assert "matches preferred location: Denmark" in reasons


def test_configured_interest_high_rate_and_core_keywords_produce_highlights() -> None:
    reasons = build_match_highlights(
        Job(
            title="Project Coordination Lead",
            description="ABAP RAP CDS OData Gateway project coordination role",
            rate="EUR 850/day",
        ),
        MatchResult(total_score=72, category="exploratory", matched_keywords=["ABAP", "RAP", "CDS"]),
        _profile(),
    )

    assert "matches configured role interests" in reasons
    assert "visible high compensation" in reasons
    assert "strong core keyword overlap" in reasons


def test_weak_low_score_job_produces_no_highlight() -> None:
    reasons = build_match_highlights(
        Job(title="SAP Functional Consultant", description="Functional support"),
        MatchResult(total_score=30, category="weak"),
        _profile(),
    )

    assert reasons == []


def _profile(highlight_score: int = 75) -> dict:
    return {
        "thresholds": {"highlight_score": highlight_score},
        "location_policy": {"preferred_regions": ["Denmark", "Remote"]},
        "role_preferences": {"interests": ["project coordination", "technical lead"]},
        "highlighting": {"core_match_groups": ["abap", "rap", "cds"]},
    }
