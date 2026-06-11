from __future__ import annotations


def listing_count_explanations(
    *,
    observed: int,
    retained: int,
    missing_url: int = 0,
    rejected: int = 0,
    duplicates: int = 0,
    limited: int = 0,
) -> list[str]:
    if not observed:
        return []
    if observed == retained and not any([missing_url, rejected, duplicates, limited]):
        return [f"Observed {observed} listing card(s) and retained all {retained} as jobs."]

    reasons = []
    if missing_url:
        reasons.append(f"{missing_url} card(s) had no recipe-readable job URL")
    if rejected:
        reasons.append(f"{rejected} card(s) were rejected by recipe filters")
    if duplicates:
        reasons.append(f"{duplicates} duplicate URL(s) were ignored")
    if limited:
        reasons.append(f"{limited} card(s) were outside the configured run limit")
    reason_text = "; ".join(reasons) if reasons else "some cards did not produce retained jobs"
    return [f"Observed {observed} listing card(s) and retained {retained} job(s): {reason_text}."]


def seen_state_explanation(*, new: int, changed: int, previously_seen: int, job_count: int) -> str:
    if not job_count:
        return ""
    return (
        "Seen-state check: "
        f"{new} new, {changed} changed, {previously_seen} already seen in previous runs. "
        "This source test does not skip or mark seen jobs; a normal run with Include seen off only processes new/changed jobs."
    )
