from __future__ import annotations

import argparse
from datetime import date

from .config import ROOT, load_profile
from .digest import write_daily_digest, write_excluded_summary, write_job_package
from .generator import generate_materials
from .scoring import score_job
from .sources import load_jobs_from_sources
from .store import JobStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SAP job preparation agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("run-daily", help="Find new roles and generate today's digest.")
    daily.add_argument("--use-llm", action="store_true", help="Use Claude for application drafting.")
    daily.add_argument("--include-seen", action="store_true", help="Process jobs even if already seen.")
    daily.add_argument("--mark-seen", action="store_true", help="Mark processed jobs as seen after the run.")
    daily.add_argument("--include-weak", action="store_true", help="Generate packages for weak matches too.")

    args = parser.parse_args()
    if args.command == "run-daily":
        run_daily(use_llm=args.use_llm, include_seen=args.include_seen, mark_seen=args.mark_seen, include_weak=args.include_weak)


def run_daily(use_llm: bool = False, include_seen: bool = False, mark_seen: bool = False, include_weak: bool = False) -> None:
    profile = load_profile(ROOT)
    source_result = load_jobs_from_sources(ROOT)
    store = JobStore(ROOT)
    states = store.classify(source_result.jobs)
    candidate_states = states if include_seen else [state for state in states if state.status in {"new", "changed"}]
    threshold = int(profile.get("thresholds", {}).get("minimum_digest_score", 45))
    run_date = date.today()
    digest_items = []
    excluded_items = []
    processed_states = []

    for state in candidate_states:
        job = state.job
        match = score_job(job, profile)
        item = {"job": job, "match": match, "state": state}

        should_include = (
            match.category in {"strong", "exploratory"}
            and match.total_score >= threshold
            and match.category != "excluded"
        ) or include_weak

        if should_include and match.category != "excluded":
            package = generate_materials(job, match, profile, use_llm=use_llm, root=ROOT)
            paths = write_job_package(job, match, package, run_date, root=ROOT)
            item["paths"] = paths
            digest_items.append(item)
            processed_states.append(state)
        else:
            excluded_items.append(item)
            processed_states.append(state)

    summary = {
        "total_loaded": len(source_result.jobs),
        "new_roles": sum(1 for state in states if state.status == "new"),
        "changed_roles": sum(1 for state in states if state.status == "changed"),
        "strong_matches": sum(1 for item in digest_items if item["match"].category == "strong"),
        "exploratory_matches": sum(1 for item in digest_items if item["match"].category == "exploratory"),
        "weak_matches": sum(1 for item in excluded_items if item["match"].category == "weak"),
        "excluded_roles": sum(1 for item in excluded_items if item["match"].category == "excluded"),
        "source_warnings": len(source_result.warnings),
    }

    digest_path = write_daily_digest(summary, digest_items, source_result.warnings, run_date, root=ROOT)
    excluded_path = write_excluded_summary(excluded_items, source_result.warnings, run_date, root=ROOT)
    if mark_seen:
        store.mark_seen(processed_states)

    print(f"Loaded jobs: {len(source_result.jobs)}")
    print(f"New jobs: {summary['new_roles']}")
    print(f"Changed jobs: {summary['changed_roles']}")
    print(f"Jobs in digest: {len(digest_items)}")
    print(f"Filtered/excluded jobs: {len(excluded_items)}")
    print(f"Source warnings: {len(source_result.warnings)}")
    print(f"Digest: {digest_path}")
    print(f"Excluded summary: {excluded_path}")
    if not mark_seen:
        print("Seen-job state was not updated. Re-run with --mark-seen after reviewing output.")


if __name__ == "__main__":
    main()
