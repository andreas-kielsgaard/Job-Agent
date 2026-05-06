from __future__ import annotations

import argparse
from datetime import date

from .config import ROOT, load_profile
from .digest import write_daily_digest, write_job_package
from .generator import generate_materials
from .scoring import score_job
from .sources import load_jobs_from_sources
from .store import JobStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SAP job preparation agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("run-daily", help="Find new roles and generate today's digest.")
    daily.add_argument("--use-llm", action="store_true", help="Use the configured LLM for application drafting.")
    daily.add_argument("--include-seen", action="store_true", help="Process jobs even if already seen.")
    daily.add_argument("--mark-seen", action="store_true", help="Mark processed jobs as seen after the run.")

    args = parser.parse_args()
    if args.command == "run-daily":
        run_daily(use_llm=args.use_llm, include_seen=args.include_seen, mark_seen=args.mark_seen)


def run_daily(use_llm: bool = False, include_seen: bool = False, mark_seen: bool = False) -> None:
    profile = load_profile(ROOT)
    jobs = load_jobs_from_sources(ROOT)
    store = JobStore(ROOT)
    candidate_jobs = jobs if include_seen else store.filter_new(jobs)
    threshold = int(profile.get("thresholds", {}).get("minimum_digest_score", 45))
    run_date = date.today()
    digest_items = []
    processed_jobs = []

    for job in candidate_jobs:
        match = score_job(job, profile)
        if match.score < threshold:
            continue
        package = generate_materials(job, match, profile, use_llm=use_llm, root=ROOT)
        paths = write_job_package(job, match, package, run_date, root=ROOT)
        digest_items.append({"job": job, "match": match, "paths": paths})
        processed_jobs.append(job)

    digest_path = write_daily_digest(digest_items, run_date, root=ROOT)
    if mark_seen:
        store.mark_seen(processed_jobs)

    print(f"Loaded jobs: {len(jobs)}")
    print(f"New candidate jobs: {len(candidate_jobs)}")
    print(f"Jobs in digest: {len(digest_items)}")
    print(f"Digest: {digest_path}")
    if not mark_seen:
        print("Seen-job state was not updated. Re-run with --mark-seen after reviewing output.")


if __name__ == "__main__":
    main()
