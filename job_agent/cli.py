from __future__ import annotations

import argparse

from .run_service import run_daily_agent
from .run_store import RunOptions


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SAP job preparation agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily = subparsers.add_parser("run-daily", help="Find new roles and generate today's digest.")
    daily.add_argument("--use-llm", action="store_true", help="Use Claude for application drafting.")
    daily.add_argument(
        "--ai-enhanced-search",
        action="store_true",
        help="Use Claude to write relevance summaries for promising postings during search.",
    )
    daily.add_argument("--include-seen", action="store_true", help="Process jobs even if already seen.")
    daily.add_argument("--mark-seen", action="store_true", help="Mark processed jobs as seen after the run.")
    daily.add_argument("--include-weak", action="store_true", help="Generate packages for weak matches too.")
    daily.add_argument(
        "--skip-materials", action="store_true", help="Skip CV/application/form material generation during discovery."
    )
    daily.add_argument(
        "--test-run", action="store_true", help="Mark this as a test run; it will not update seen-job state."
    )

    args = parser.parse_args()
    if args.command == "run-daily":
        run_daily(
            use_llm=args.use_llm,
            ai_enhanced_search=args.ai_enhanced_search,
            include_seen=args.include_seen,
            mark_seen=args.mark_seen,
            include_weak=args.include_weak,
            generate_materials=not args.skip_materials,
            is_test=args.test_run,
        )


def run_daily(
    use_llm: bool = False,
    ai_enhanced_search: bool = False,
    include_seen: bool = False,
    mark_seen: bool = False,
    include_weak: bool = False,
    generate_materials: bool = True,
    is_test: bool = False,
) -> None:
    result = run_daily_agent(
        RunOptions(
            use_llm=use_llm,
            ai_enhanced_search=ai_enhanced_search,
            include_seen=include_seen,
            include_weak=include_weak,
            mark_seen=mark_seen,
            generate_materials=generate_materials,
            is_test=is_test,
        )
    )
    record = result.record
    print(f"Run: {record.run_id}")
    print(f"Status: {record.status}")
    print(f"Test run: {record.is_test}")
    print(f"Loaded jobs: {record.total_loaded}")
    print(f"New jobs: {record.new_roles}")
    print(f"Changed jobs: {record.changed_roles}")
    print(f"Jobs in digest: {record.generated_job_count}")
    print(f"Filtered/excluded jobs: {record.weak_matches + record.excluded_roles}")
    print(f"Source warnings: {record.source_warnings}")
    print(f"Digest: {record.digest_path}")
    print(f"Excluded summary: {record.excluded_path}")
    print(f"Run log: {record.run_log_path}")
    if not mark_seen:
        print("Seen-job state was not updated. Re-run with --mark-seen after reviewing output.")


if __name__ == "__main__":
    main()
