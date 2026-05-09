from __future__ import annotations

import argparse
import sys

from .run_service import run_daily_agent
from .run_store import RunOptions
from .services.job_board_check_service import check_job_board_compatibility
from .services.recipe_calibration_service import capture_recipe_calibration
from .services.recipe_preview_service import RecipePreviewResult, preview_recipe


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
        "--generate-materials", action="store_true", help="Generate CV/application/form materials during discovery."
    )
    daily.add_argument(
        "--test-run", action="store_true", help="Mark this as a test run; it will not update seen-job state."
    )
    compatibility = subparsers.add_parser(
        "check-board",
        help="Check whether one public job-board URL works with the generic extractor.",
    )
    compatibility.add_argument("url")
    compatibility.add_argument(
        "--no-render",
        action="store_true",
        help="Skip the optional Playwright-rendered comparison.",
    )
    recipe = subparsers.add_parser(
        "test-recipe",
        help="Test a constrained job-board extraction recipe against one URL or local HTML file.",
    )
    recipe.add_argument("recipe_path")
    recipe.add_argument("url_or_html_path")
    recipe.add_argument("--base-url", default="", help="Base URL to resolve links when testing a local HTML file.")
    recipe.add_argument(
        "--rendered",
        action="store_true",
        help="Render the provided public URL with Playwright before running the recipe.",
    )
    recipe.add_argument(
        "--static",
        action="store_true",
        help="Force static HTML mode even if the recipe uses mode: rendered_html.",
    )
    recipe.add_argument(
        "--source-id",
        default="",
        help="Optional source registry id. When provided, save this preview result to source-health.yaml.",
    )
    calibrate = subparsers.add_parser(
        "calibrate-recipe",
        help="Capture one public page and report candidate regions for manual recipe calibration.",
    )
    calibrate.add_argument("url")
    calibrate.add_argument("--recipe", default="", help="Optional recipe path to audit against the captured page.")
    calibrate.add_argument("--rendered", action="store_true", help="Force Playwright-rendered capture mode.")
    calibrate.add_argument("--static", action="store_true", help="Force static HTML capture mode.")
    calibrate.add_argument("--max-candidates", type=int, default=30, help="Maximum candidate regions to report.")

    args = parser.parse_args()
    if args.command == "run-daily":
        run_daily(
            use_llm=args.use_llm,
            ai_enhanced_search=args.ai_enhanced_search,
            include_seen=args.include_seen,
            mark_seen=args.mark_seen,
            include_weak=args.include_weak,
            generate_materials=args.generate_materials and not args.skip_materials,
            is_test=args.test_run,
        )
    if args.command == "check-board":
        check_board(args.url, render=not args.no_render)
    if args.command == "test-recipe":
        test_recipe(
            args.recipe_path,
            args.url_or_html_path,
            base_url=args.base_url,
            rendered=args.rendered,
            static=args.static,
            source_id=args.source_id,
        )
    if args.command == "calibrate-recipe":
        calibrate_recipe(
            args.url,
            recipe_path=args.recipe or None,
            rendered=args.rendered,
            static=args.static,
            max_candidates=args.max_candidates,
        )


def run_daily(
    use_llm: bool = False,
    ai_enhanced_search: bool = False,
    include_seen: bool = False,
    mark_seen: bool = False,
    include_weak: bool = False,
    generate_materials: bool = False,
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


def check_board(url: str, render: bool = True) -> None:
    try:
        report = check_job_board_compatibility(url, render=render)
    except ValueError as exc:
        print(exc)
        return
    print(f"URL: {report.url}")
    print(f"Recommendation: {report.recommendation}")
    print(f"Reason: {report.recommendation_reason}")
    for quality in [report.normal_html, report.rendered_page]:
        if not quality:
            continue
        print("")
        print(quality.label)
        print(f"Status: {quality.status_code if quality.status_code is not None else 'n/a'}")
        print(f"Candidates: {quality.candidate_count}")
        print(f"Useful titles: {quality.useful_title_count}")
        print(f"Generic labels: {quality.generic_title_count}")
        print(f"Unique URLs: {quality.unique_url_count}")
        print(f"Average description length: {quality.average_description_length}")
        for warning in quality.warnings:
            print(f"Warning: {warning}")
        for candidate in quality.candidates[:10]:
            missing = ", ".join(candidate.missing_fields) or "none"
            print(f"- {candidate.title} [{candidate.title_quality}] {candidate.description_length} chars; missing: {missing}")
            print(f"  {candidate.url}")


def test_recipe(
    recipe_path: str,
    url_or_html_path: str,
    base_url: str = "",
    rendered: bool = False,
    static: bool = False,
    source_id: str = "",
) -> None:
    try:
        preview = preview_recipe(recipe_path, url_or_html_path, base_url=base_url, rendered=rendered, static=static)
        saved_source_id = source_id.strip()
        if source_id.strip():
            from .services.source_health_service import SourceHealthService

            SourceHealthService().save_preview(saved_source_id, preview)
    except ValueError as exc:
        if source_id.strip():
            from .services.source_health_service import SourceHealthService

            SourceHealthService().save_failure(
                source_id.strip(),
                url_or_html_path,
                "rendered_html" if rendered else "static_html" if static else "unknown",
                str(exc),
            )
        print(exc)
        return
    _print_recipe_preview(preview)
    if source_id.strip():
        _safe_print(f"Source health saved: {source_id.strip()}")


def _print_recipe_preview(preview: RecipePreviewResult) -> None:
    _safe_print(f"Recipe: {preview.recipe_source_name}")
    _safe_print(f"Recipe path: {preview.recipe_path}")
    _safe_print(f"Recipe status: {preview.recipe_status}")
    _safe_print(f"Input type: {preview.input_type}")
    _safe_print(f"Input mode: {preview.mode_used}")
    _safe_print(f"Base URL: {preview.base_url}")
    _safe_print(f"Jobs extracted: {preview.extracted_job_count}")
    _safe_print(f"Useful titles: {preview.useful_titles}")
    _safe_print(f"Generic labels: {preview.generic_labels}")
    _safe_print(f"Unique URLs: {preview.unique_urls}")
    _safe_print(f"Average description length: {preview.average_description_length}")
    for warning in preview.warnings:
        _safe_print(f"Warning: {warning}")
    for index, job in enumerate(preview.jobs[:10], start=1):
        languages = ", ".join(job.languages) or "Not listed"
        notes = "; ".join(job.extraction_notes) or "none"
        _safe_print("")
        _safe_print(f"{index}. {job.title}")
        _safe_print(f"   URL: {job.url}")
        _safe_print(f"   Location: {job.location}")
        _safe_print(f"   Remote/work arrangement: {job.remote}")
        _safe_print(f"   Rate/pay: {job.rate}")
        _safe_print(f"   Workload/work type: {job.workload}")
        _safe_print(f"   Posted date: {job.posted_date}")
        _safe_print(f"   Start date: {job.start_date}")
        _safe_print(f"   Language: {languages}")
        _safe_print(f"   Notes: {notes}")
        if job.description_preview:
            _safe_print(f"   Description: {job.description_preview}")


def _safe_print(text: str = "") -> None:
    encoding = sys.stdout.encoding or "utf-8"
    print(str(text).encode(encoding, errors="replace").decode(encoding))


def calibrate_recipe(
    url: str,
    recipe_path: str | None = None,
    rendered: bool = False,
    static: bool = False,
    max_candidates: int = 30,
) -> None:
    try:
        if rendered and static:
            raise ValueError("Use either --rendered or --static, not both.")
        rendered_mode = True if rendered else False if static else None
        result = capture_recipe_calibration(
            url,
            recipe_path=recipe_path,
            rendered=rendered_mode,
            max_candidates=max_candidates,
        )
    except ValueError as exc:
        print(exc)
        return
    print(f"Artifacts: {result.artifact_dir}")
    print(f"Capture mode: {result.capture_mode}")
    print(f"Candidate regions: {result.candidate_count}")
    if recipe_path:
        print(f"Recipe extracted jobs: {result.recipe_extracted_count}")
        print(f"Card selector matches: {result.card_selector_match_count}")
    for warning in result.warnings[:10]:
        print(f"Warning: {warning}")
    print(f"Summary: {result.summary_path}")


if __name__ == "__main__":
    main()
