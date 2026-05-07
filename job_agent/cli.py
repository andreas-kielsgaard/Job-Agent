from __future__ import annotations

import argparse
from pathlib import Path

import requests

from .run_service import run_daily_agent
from .run_store import RunOptions
from .services.job_board_check_service import check_job_board_compatibility, validate_public_url
from .services.job_board_recipe_service import check_recipe_against_html, load_job_board_recipe


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
        test_recipe(args.recipe_path, args.url_or_html_path, base_url=args.base_url, rendered=args.rendered)


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


def test_recipe(recipe_path: str, url_or_html_path: str, base_url: str = "", rendered: bool = False) -> None:
    try:
        recipe = load_job_board_recipe(Path(recipe_path))
        html, resolved_base_url, warnings = _load_recipe_test_input(
            url_or_html_path, recipe.start_url, base_url, rendered=rendered
        )
    except ValueError as exc:
        print(exc)
        return
    quality = check_recipe_against_html(html, base_url=resolved_base_url, recipe=recipe)
    quality.warnings.extend(warnings)
    print(f"Recipe: {recipe.source_name}")
    print(f"Base URL: {resolved_base_url}")
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


def _load_recipe_test_input(
    url_or_html_path: str,
    recipe_start_url: str,
    base_url: str,
    rendered: bool = False,
) -> tuple[str, str, list[str]]:
    value = url_or_html_path.strip()
    if value.startswith(("http://", "https://")):
        url = validate_public_url(value)
        if rendered:
            return _render_recipe_test_url(url)
        try:
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Job-Agent recipe tester (public page; low volume)"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ValueError(f"Fetch failed: {exc}") from exc
        return response.text, response.url, []

    if rendered:
        raise ValueError("--rendered can only be used with a public http(s) URL.")
    resolved_base_url = base_url.strip() or recipe_start_url.strip()
    if not resolved_base_url:
        raise ValueError("Testing a local HTML file requires --base-url or recipe.start_url.")
    path = Path(value)
    if not path.exists():
        raise ValueError(f"HTML fixture not found: {path}")
    return path.read_text(encoding="utf-8"), resolved_base_url, []


def _render_recipe_test_url(url: str) -> tuple[str, str, list[str]]:
    warnings = []
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise ValueError(
            "Playwright is not installed. Install requirements-playwright.txt and Chromium to use --rendered."
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightError:
                    warnings.append("Rendered page did not become network-idle before the polite timeout.")
                return page.content(), page.url, warnings
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ValueError(f"Playwright render failed: {exc}") from exc


if __name__ == "__main__":
    main()
