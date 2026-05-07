from __future__ import annotations

import argparse
from pathlib import Path

from .run_service import run_daily_agent
from .run_store import RunOptions
from .services.job_board_check_service import check_job_board_compatibility
from .services.job_board_recipe_service import (
    RecipeExtractionResult,
    extract_jobs_with_recipe,
    extract_jobs_with_recipe_from_url,
    load_job_board_recipe,
    quality_from_recipe_result,
)


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
) -> None:
    try:
        recipe = load_job_board_recipe(Path(recipe_path))
        if rendered and static:
            raise ValueError("Use either --rendered or --static, not both.")
        result = _run_recipe_test(recipe, url_or_html_path, base_url=base_url, rendered=rendered, static=static)
    except ValueError as exc:
        print(exc)
        return
    quality = quality_from_recipe_result(result, recipe)
    print(f"Recipe: {recipe.source_name}")
    print(f"Input mode: {result.mode_used}")
    print(f"Base URL: {result.base_url}")
    print(f"Jobs extracted: {quality.candidate_count}")
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


def _run_recipe_test(
    recipe,
    url_or_html_path: str,
    base_url: str,
    rendered: bool = False,
    static: bool = False,
) -> RecipeExtractionResult:
    value = url_or_html_path.strip()
    if value.startswith(("http://", "https://")):
        forced_rendered = None
        if rendered:
            forced_rendered = True
        elif static:
            forced_rendered = False
        return extract_jobs_with_recipe_from_url(value, recipe, rendered=forced_rendered)

    if rendered:
        raise ValueError("--rendered can only be used with a public http(s) URL.")
    resolved_base_url = base_url.strip() or recipe.start_url.strip()
    if not resolved_base_url:
        raise ValueError("Testing a local HTML file requires --base-url or recipe.start_url.")
    path = Path(value)
    if not path.exists():
        raise ValueError(f"HTML fixture not found: {path}")
    warnings = []
    if recipe.mode == "rendered_html":
        warnings.append("Local fixture HTML ignores recipe mode: rendered_html.")
    html = path.read_text(encoding="utf-8")
    jobs = extract_jobs_with_recipe(html, base_url=resolved_base_url, recipe=recipe)
    return RecipeExtractionResult(
        jobs=jobs,
        base_url=resolved_base_url,
        mode_used="local_fixture_html",
        warnings=warnings,
    )


if __name__ == "__main__":
    main()
