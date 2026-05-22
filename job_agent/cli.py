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
    recipe.add_argument(
        "--detail-input",
        default="",
        help="Optional local HTML file for one job detail page sample.",
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
    dry_run = subparsers.add_parser(
        "dry-run-source",
        help="Run one configured execution source through its adapter without writing packages or run state.",
    )
    dry_run.add_argument("source_id")
    dry_run.add_argument(
        "--force-disabled",
        action="store_true",
        help="Execute a disabled source entry for inspection without enabling it.",
    )
    dry_run.add_argument(
        "--save-readiness",
        action="store_true",
        help="Save this dry-run as the latest source go-live readiness result.",
    )
    run_source = subparsers.add_parser(
        "run-source",
        help="Run one enabled execution source through the normal package-writing pipeline.",
    )
    run_source.add_argument("source_id")
    suggest = subparsers.add_parser(
        "suggest-recipe",
        help="Suggest constrained recipe YAML from a local calibration artifact folder.",
    )
    suggest.add_argument("artifact_dir")
    suggest.add_argument("--source-name", default="")
    suggest.add_argument("--start-url", default="")
    suggest.add_argument("--existing-recipe", default="")
    suggest.add_argument("--output", default="")
    suggest.add_argument("--overwrite", action="store_true")
    suggest.add_argument("--refine", action="store_true", help="Run a bounded local validation/refinement loop.")
    suggest.add_argument("--max-attempts", type=int, default=3, help="Maximum LLM suggestion attempts when refining.")
    suggest.add_argument("--save-candidate", action="store_true", help="Save a pending recipe candidate review object.")
    list_candidates = subparsers.add_parser(
        "list-recipe-candidates",
        help="List pending/rejected generated recipe candidates.",
    )
    list_candidates.add_argument("--status", default="", choices=["", "pending", "rejected", "approved"])
    show_candidate = subparsers.add_parser(
        "show-recipe-candidate",
        help="Show a generated recipe candidate review object.",
    )
    show_candidate.add_argument("candidate_id")
    reject_candidate = subparsers.add_parser(
        "reject-recipe-candidate",
        help="Reject a generated recipe candidate without promoting it.",
    )
    reject_candidate.add_argument("candidate_id")
    reject_candidate.add_argument("--reason", default="")
    approve_candidate = subparsers.add_parser(
        "approve-recipe-candidate",
        help="Approve a pending candidate into a recipe file and save local preview health.",
    )
    approve_candidate.add_argument("candidate_id")
    approve_candidate.add_argument("--recipe-path", required=True)
    approve_candidate.add_argument("--source-id", default="")
    approve_candidate.add_argument("--overwrite", action="store_true")
    generation_status = subparsers.add_parser(
        "recipe-generation-status",
        help="Summarize local recipe generation state for one source.",
    )
    generation_status.add_argument("--source-id", required=True)
    go_live = subparsers.add_parser(
        "source-go-live-status",
        help="Show go-live readiness for one execution source.",
    )
    go_live.add_argument("source_id")
    enable_ready = subparsers.add_parser(
        "enable-source-when-ready",
        help="Enable one execution source only when saved readiness checks pass.",
    )
    enable_ready.add_argument("source_id")
    adopt_candidate = subparsers.add_parser(
        "adopt-approved-recipe",
        help="Adopt an approved candidate recipe path into a source registry entry.",
    )
    adopt_candidate.add_argument("candidate_id")
    adopt_candidate.add_argument("--source-id", required=True)
    adopt_candidate.add_argument("--prepare-disabled-execution-entry", action="store_true")

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
            detail_input=args.detail_input,
        )
    if args.command == "calibrate-recipe":
        calibrate_recipe(
            args.url,
            recipe_path=args.recipe or None,
            rendered=args.rendered,
            static=args.static,
            max_candidates=args.max_candidates,
        )
    if args.command == "dry-run-source":
        dry_run_source(args.source_id, force_disabled=args.force_disabled, save_readiness=args.save_readiness)
    if args.command == "run-source":
        run_source_now(args.source_id)
    if args.command == "suggest-recipe":
        suggest_recipe(
            args.artifact_dir,
            source_name=args.source_name,
            start_url=args.start_url,
            existing_recipe=args.existing_recipe,
            output=args.output,
            overwrite=args.overwrite,
            refine=args.refine,
            max_attempts=args.max_attempts,
            save_candidate=args.save_candidate,
        )
    if args.command == "list-recipe-candidates":
        list_recipe_candidates(status=args.status)
    if args.command == "show-recipe-candidate":
        show_recipe_candidate(args.candidate_id)
    if args.command == "reject-recipe-candidate":
        reject_recipe_candidate(args.candidate_id, reason=args.reason)
    if args.command == "approve-recipe-candidate":
        approve_recipe_candidate(
            args.candidate_id,
            recipe_path=args.recipe_path,
            source_id=args.source_id,
            overwrite=args.overwrite,
        )
    if args.command == "recipe-generation-status":
        recipe_generation_status(args.source_id)
    if args.command == "source-go-live-status":
        source_go_live_status(args.source_id)
    if args.command == "enable-source-when-ready":
        enable_source_when_ready(args.source_id)
    if args.command == "adopt-approved-recipe":
        adopt_approved_recipe(
            args.candidate_id,
            source_id=args.source_id,
            prepare_disabled_execution_entry=args.prepare_disabled_execution_entry,
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
    detail_input: str = "",
) -> None:
    try:
        preview = preview_recipe(
            recipe_path,
            url_or_html_path,
            base_url=base_url,
            rendered=rendered,
            static=static,
            detail_input_value=detail_input,
        )
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
    if preview.listing_observed_count:
        _safe_print(f"Listing cards observed: {preview.listing_observed_count}")
    for explanation in preview.count_explanations:
        _safe_print(f"Count note: {explanation}")
    _safe_print(f"Useful titles: {preview.useful_titles}")
    _safe_print(f"Generic labels: {preview.generic_labels}")
    _safe_print(f"Unique URLs: {preview.unique_urls}")
    _safe_print(f"Average description length: {preview.average_description_length}")
    if preview.request_notes:
        _safe_print("")
        _safe_print("Request budget:")
        for note in preview.request_notes:
            _safe_print(f"- {note}")
    if preview.field_coverage:
        _safe_print("")
        _safe_print("Listing field coverage:")
        for field in preview.field_coverage:
            _safe_print(f"- {field.label}: {field.present_count}/{field.total_count}")
    if preview.capability_checks:
        _safe_print("")
        _safe_print("Capability checks:")
        for check in preview.capability_checks:
            expected = "expected" if check.expected else "not expected"
            _safe_print(f"- {check.label}: {check.status} ({expected}) - {check.detail}")
    if preview.field_checks:
        _safe_print("")
        _safe_print("Report field expectations:")
        for field in preview.field_checks:
            expected = "expected" if field.expected else "not expected"
            _safe_print(f"- {field.label}: {field.status} ({expected}) {field.present_count}/{field.total_count}")
    _safe_print("")
    _safe_print(
        "Detail follow: "
        f"{'yes' if preview.detail_follow_enabled else 'no'} "
        f"(max {preview.detail_max_pages}, delay {preview.detail_request_delay_seconds:g}s)"
    )
    _safe_print(
        "Pagination: "
        f"{'configured' if preview.pagination_configured else 'not configured'}, "
        f"{preview.pagination_link_count} link(s) found, max pages {preview.pagination_max_pages}"
    )
    for link in preview.pagination_links[:8]:
        marker = "next" if link.is_next else "page"
        _safe_print(f"- {link.label} [{marker}] {link.url}")
    if preview.detail_attempts:
        _safe_print("")
        _safe_print("Detail proof:")
        for attempt in preview.detail_attempts:
            _safe_print(
                f"- {attempt.status} {attempt.url}; found: {', '.join(attempt.found_fields) or 'none'}; "
                f"missing: {', '.join(attempt.missing_fields) or 'none'}"
            )
    if preview.detail_sample:
        _safe_print("")
        _safe_print("Detail sample:")
        _safe_print(f"   Title: {preview.detail_sample.title}")
        _safe_print(f"   URL: {preview.detail_sample.url}")
        _safe_print(f"   Location: {preview.detail_sample.location}")
        _safe_print(f"   Rate/pay: {preview.detail_sample.rate}")
        _safe_print(f"   Workload/work type: {preview.detail_sample.workload}")
        _safe_print(f"   Posted date: {preview.detail_sample.posted_date}")
        _safe_print(f"   Description: {preview.detail_sample.description_preview}")
        if preview.detail_field_coverage:
            _safe_print("   Detail field coverage:")
            for field in preview.detail_field_coverage:
                _safe_print(f"   - {field.label}: {field.present_count}/{field.total_count}")
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


def dry_run_source(source_id: str, force_disabled: bool = False, save_readiness: bool = False, root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.source_dry_run_service import SourceDryRunService
    from .services.source_execution_readiness_service import SourceExecutionReadinessService

    project_root = Path(root) if root else ROOT
    service = SourceDryRunService(project_root) if root else SourceDryRunService()
    result = service.dry_run(source_id, force_disabled=force_disabled)
    _safe_print(f"Source id: {result.source_id}")
    _safe_print(f"Source name: {result.source_name or 'Not found'}")
    _safe_print(f"Source type: {result.source_type or 'Not found'}")
    _safe_print(f"Enabled: {result.source_enabled}")
    if result.forced_disabled:
        _safe_print("Forced disabled source execution: true")
    _safe_print(f"Dry-run status: {result.status}")
    _safe_print(f"Jobs extracted: {result.job_count}")
    if result.listing_observed_count:
        _safe_print(f"Listing cards observed: {result.listing_observed_count}")
    for explanation in result.count_explanations:
        _safe_print(f"Count note: {explanation}")
    _safe_print(f"Warnings: {result.warning_count}")
    for warning in result.warnings:
        _safe_print(f"Warning: {warning}")
    for index, job in enumerate(result.jobs[:10], start=1):
        languages = ", ".join(job.languages) or "Not listed"
        notes = "; ".join(job.extraction_notes) or "none"
        _safe_print("")
        _safe_print(f"{index}. {job.title}")
        _safe_print(f"   URL: {job.url}")
        _safe_print(f"   Source: {job.source}")
        _safe_print(f"   Source id: {job.source_id}")
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
    _safe_print("")
    _safe_print("No packages, seen state, materials, digests, or run records were written.")
    if save_readiness:
        readiness = SourceExecutionReadinessService(project_root).save_from_dry_run(result)
        _safe_print(f"Readiness saved: {readiness.readiness_status}")
        _safe_print(f"Readiness summary: {readiness.readiness_summary}")


def run_source_now(source_id: str) -> None:
    from .services.single_source_run_service import SingleSourceRunService

    result = SingleSourceRunService().run(source_id)
    _safe_print(f"Source id: {result.source_id}")
    _safe_print(f"Source name: {result.source_name or 'Not found'}")
    _safe_print(f"Source type: {result.source_type or 'Not found'}")
    _safe_print(f"Status: {result.status}")
    if result.run_id:
        _safe_print(f"Run id: {result.run_id}")
        _safe_print(f"View results: {result.run_detail_url}")
    _safe_print(f"Extracted jobs: {result.extracted_job_count}")
    _safe_print(f"Packages written: {result.package_count}")
    _safe_print(f"Strong matches: {result.strong_matches}")
    _safe_print(f"Exploratory matches: {result.exploratory_matches}")
    for warning in result.warnings:
        _safe_print(f"Warning: {warning}")
    for index, package in enumerate(result.packages[:10], start=1):
        _safe_print("")
        _safe_print(f"{index}. {package.title}")
        _safe_print(f"   Source id: {package.source_id}")
        _safe_print(f"   Match: {package.match_score}% / {package.match_category}")
        _safe_print(f"   Package: {package.package_path}")
        _safe_print(f"   URL: {package.job_url}")
    _safe_print("")
    _safe_print("Materials were not generated by default. Disabled sources are not run.")


def suggest_recipe(
    artifact_dir: str,
    *,
    source_name: str = "",
    start_url: str = "",
    existing_recipe: str = "",
    output: str = "",
    overwrite: bool = False,
    refine: bool = False,
    max_attempts: int = 3,
    save_candidate: bool = False,
    root=None,
) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_candidate_service import RecipeCandidateStore
    from .services.recipe_suggestion_service import suggest_recipe_from_artifact, suggest_recipe_with_refinement

    project_root = Path(root) if root else ROOT
    output_path = Path(output) if output else None
    if output_path and output_path.exists() and not overwrite:
        _safe_print(f"Output already exists: {output_path}. Re-run with --overwrite to replace it.")
        return
    try:
        if refine:
            refinement = suggest_recipe_with_refinement(
                Path(artifact_dir),
                source_name=source_name,
                start_url=start_url,
                existing_recipe_path=Path(existing_recipe) if existing_recipe else None,
                max_attempts=max_attempts,
                root=project_root,
            )
            result = refinement.final_result
        else:
            refinement = None
            result = suggest_recipe_from_artifact(
                Path(artifact_dir),
                source_name=source_name,
                start_url=start_url,
                existing_recipe_path=Path(existing_recipe) if existing_recipe else None,
                root=project_root,
            )
    except RuntimeError as exc:
        _safe_print(f"Recipe suggestion unavailable: {exc}")
        return
    except ValueError as exc:
        _safe_print(f"Recipe suggestion failed: {exc}")
        return

    if refinement:
        _print_recipe_refinement(refinement)
    else:
        _print_recipe_suggestion(result)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.suggested_recipe_yaml.strip() + "\n", encoding="utf-8")
        _safe_print(f"Suggested recipe written to: {output_path}")
    if save_candidate:
        store = RecipeCandidateStore(project_root)
        candidate = (
            store.save_candidate_from_refinement(refinement)
            if refinement
            else store.save_candidate_from_suggestion(result)
        )
        _safe_print(f"Recipe candidate saved: {candidate.candidate_id}")
        _safe_print(f"Candidate path: {store.candidate_path(candidate.candidate_id)}")


def _print_recipe_suggestion(result) -> None:
    _safe_print(f"Source: {result.source_name}")
    _safe_print(f"Start URL: {result.start_url}")
    _safe_print(f"Artifact dir: {result.artifact_dir}")
    _safe_print(f"Selected strategy: {result.selected_strategy}")
    _safe_print(f"Confidence: {result.confidence}")
    _safe_print(f"Evidence summary: {result.evidence_summary}")
    _safe_print(f"Schema valid: {result.schema_valid}")
    for error in result.validation_errors:
        _safe_print(f"Validation error: {error}")
    for assumption in result.assumptions:
        _safe_print(f"Assumption: {assumption}")
    for warning in result.warnings:
        _safe_print(f"Warning: {warning}")
    if result.explanation:
        _safe_print(f"Explanation: {result.explanation}")
    _safe_print("")
    _safe_print("Suggested recipe YAML:")
    _safe_print(result.suggested_recipe_yaml)


def _print_recipe_refinement(refinement) -> None:
    _safe_print(f"Refinement attempts: {len(refinement.attempts)}")
    _safe_print(f"Accepted: {refinement.accepted}")
    for attempt in refinement.attempts:
        _safe_print("")
        _safe_print(f"Attempt {attempt.attempt_number}")
        _safe_print(f"  Schema valid: {attempt.schema_valid}")
        _safe_print(f"  Quality status: {attempt.quality_status}")
        _safe_print(f"  Jobs extracted: {attempt.extracted_job_count}")
        _safe_print(f"  Useful titles: {attempt.useful_titles}")
        _safe_print(f"  Generic labels: {attempt.generic_labels}")
        _safe_print(f"  Unique URLs: {attempt.unique_urls}")
        _safe_print(f"  Average description length: {attempt.average_description_length}")
        for error in attempt.validation_errors:
            _safe_print(f"  Validation error: {error}")
        for warning in attempt.quality_warnings:
            _safe_print(f"  Quality warning: {warning}")
        if attempt.revision_reason:
            _safe_print(f"  Revision reason: {attempt.revision_reason}")
    _safe_print("")
    _print_recipe_suggestion(refinement.final_result)


def list_recipe_candidates(status: str = "", root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_candidate_service import RecipeCandidateStore

    store = RecipeCandidateStore(Path(root) if root else ROOT)
    candidates = store.list_candidates(status=status)
    if not candidates:
        _safe_print("No recipe candidates found.")
        return
    for candidate in candidates:
        _safe_print(
            f"{candidate.candidate_id} | {candidate.status} | {candidate.source_name} | "
            f"created {candidate.created_at} | schema_valid={candidate.schema_valid} | "
            f"refinement_accepted={candidate.refinement_accepted} | "
            f"quality={candidate.quality_status or 'n/a'} | artifact={candidate.artifact_dir}"
        )


def show_recipe_candidate(candidate_id: str, root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_candidate_service import RecipeCandidateStore

    try:
        candidate = RecipeCandidateStore(Path(root) if root else ROOT).load_candidate(candidate_id)
    except ValueError as exc:
        _safe_print(str(exc))
        return
    _print_recipe_candidate(candidate)


def reject_recipe_candidate(candidate_id: str, reason: str = "", root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_candidate_service import RecipeCandidateStore

    try:
        candidate = RecipeCandidateStore(Path(root) if root else ROOT).reject_candidate(candidate_id, reason=reason)
    except ValueError as exc:
        _safe_print(str(exc))
        return
    _safe_print(f"Recipe candidate rejected: {candidate.candidate_id}")
    if candidate.rejection_reason:
        _safe_print(f"Reason: {candidate.rejection_reason}")


def approve_recipe_candidate(
    candidate_id: str,
    *,
    recipe_path: str,
    source_id: str = "",
    overwrite: bool = False,
    root=None,
) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_candidate_approval_service import RecipeCandidateApprovalService

    try:
        result = RecipeCandidateApprovalService(Path(root) if root else ROOT).approve(
            candidate_id,
            recipe_path,
            source_id=source_id,
            overwrite=overwrite,
        )
    except ValueError as exc:
        _safe_print(f"Recipe candidate approval failed: {exc}")
        return
    _safe_print(f"Recipe candidate approved: {result.candidate.candidate_id}")
    _safe_print(f"Approved recipe path: {result.recipe_path}")
    _safe_print(f"Preview ran: {result.preview is not None}")
    if result.preview:
        _safe_print(f"Jobs extracted: {result.preview.extracted_job_count}")
        _safe_print(f"Useful titles: {result.preview.useful_titles}")
        _safe_print(f"Unique URLs: {result.preview.unique_urls}")
    _safe_print(f"Source health saved: {result.health_record is not None}")
    for warning in result.warnings:
        _safe_print(f"Warning: {warning}")
    _safe_print("Source execution was not enabled and daily-run configuration was not changed.")


def recipe_generation_status(source_id: str, root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.recipe_generation_status_service import RecipeGenerationStatusService

    try:
        status = RecipeGenerationStatusService(Path(root) if root else ROOT).build_for_source(source_id)
    except ValueError as exc:
        _safe_print(str(exc))
        return
    _safe_print(f"Source: {status.source_name} ({status.source_id})")
    _safe_print(f"URL: {status.source_url or 'not listed'}")
    _safe_print(f"Registry recipe path: {status.source_recipe_path or 'none'}")
    _safe_print(f"Calibration artifacts: {status.artifact_count}")
    if status.best_artifact:
        _safe_print(f"Best artifact: {status.best_artifact.artifact_dir} [{status.best_artifact.match_status}]")
    _safe_print(
        f"Candidates: pending={status.pending_candidates}, "
        f"approved={status.approved_candidates}, rejected={status.rejected_candidates}"
    )
    _safe_print(f"Latest candidate: {status.latest_candidate_id or 'none'} [{status.latest_candidate_status or 'n/a'}]")
    _safe_print(f"Latest approved recipe: {status.latest_approved_recipe_path or 'none'}")
    _safe_print(f"Approved path matches registry: {status.approved_matches_source_recipe_path}")
    _safe_print(f"Source health: {status.source_health_status} - {status.source_health_summary}")
    _safe_print(f"Execution entry present: {status.execution_entry_exists}")
    _safe_print(f"Execution enabled: {status.execution_enabled}")
    for warning in status.warnings:
        _safe_print(f"Workflow note: {warning}")
    _safe_print("Approval/source health and execution enablement are separate; this command does not mutate anything.")


def source_go_live_status(source_id: str, root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.source_execution_readiness_service import SourceExecutionReadinessService

    readiness = SourceExecutionReadinessService(Path(root) if root else ROOT).evaluate(source_id)
    _safe_print(f"Source id: {readiness.source_id}")
    _safe_print(f"Readiness status: {readiness.readiness_status}")
    _safe_print(f"Readiness summary: {readiness.readiness_summary}")
    _safe_print(f"Last dry-run: {readiness.last_checked_at or 'never'}")
    _safe_print(f"Dry-run status: {readiness.dry_run_status}")
    _safe_print(f"Dry-run jobs: {readiness.dry_run_job_count}")
    _safe_print(f"Dry-run warnings: {readiness.dry_run_warning_count}")
    _safe_print(f"Source health: {readiness.checks.get('source_health_status', 'unknown')}")
    _safe_print(f"Execution entry present: {readiness.checks.get('execution_entry_exists', False)}")
    _safe_print(f"Execution enabled: {readiness.checks.get('execution_entry_enabled', False)}")
    _safe_print(
        "Execution recipe matches registry: "
        f"{readiness.checks.get('execution_entry_recipe_path_matches_registry', False)}"
    )
    for blocker in readiness.blockers:
        _safe_print(f"Blocker: {blocker}")
    for warning in readiness.warnings:
        _safe_print(f"Warning: {warning}")
    _safe_print("Enablement is separate. This command does not run sources or mutate configuration.")


def enable_source_when_ready(source_id: str, root=None) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.source_execution_readiness_service import SourceExecutionReadinessService

    result = SourceExecutionReadinessService(Path(root) if root else ROOT).enable_when_ready(source_id)
    if result.enabled:
        _safe_print(f"Source enabled: {source_id}")
        _safe_print("No source run or daily run was started.")
        return
    _safe_print(f"Source not enabled: {source_id}")
    for blocker in result.check.blockers:
        _safe_print(f"Blocker: {blocker}")
    for warning in result.check.warnings:
        _safe_print(f"Warning: {warning}")


def adopt_approved_recipe(
    candidate_id: str,
    *,
    source_id: str,
    prepare_disabled_execution_entry: bool = False,
    root=None,
) -> None:
    from pathlib import Path

    from .config import ROOT
    from .services.approved_recipe_adoption_service import ApprovedRecipeAdoptionService

    try:
        result = ApprovedRecipeAdoptionService(Path(root) if root else ROOT).adopt(
            candidate_id,
            source_id,
            prepare_disabled_execution_entry=prepare_disabled_execution_entry,
        )
    except ValueError as exc:
        _safe_print(f"Approved recipe adoption failed: {exc}")
        return
    _safe_print(f"Candidate adopted: {result.candidate.candidate_id}")
    _safe_print(f"Source: {result.source_name} ({result.source_id})")
    _safe_print(f"Previous registry recipe path: {result.previous_recipe_path or 'none'}")
    _safe_print(f"Adopted recipe path: {result.adopted_recipe_path}")
    _safe_print(f"Registry updated: {result.registry_updated}")
    _safe_print(f"Disabled execution entry created: {result.execution_entry_created}")
    _safe_print(f"Disabled execution entry updated: {result.execution_entry_updated}")
    for warning in result.warnings:
        _safe_print(f"Warning: {warning}")
    _safe_print("Execution was not enabled. Daily-run enablement remains a separate guarded action.")


def _print_recipe_candidate(candidate) -> None:
    _safe_print(f"Candidate id: {candidate.candidate_id}")
    _safe_print(f"Status: {candidate.status}")
    _safe_print(f"Created: {candidate.created_at}")
    _safe_print(f"Updated: {candidate.updated_at}")
    _safe_print(f"Source: {candidate.source_name}")
    _safe_print(f"Start URL: {candidate.start_url}")
    _safe_print(f"Artifact dir: {candidate.artifact_dir}")
    _safe_print(f"Selected strategy: {candidate.selected_strategy}")
    _safe_print(f"Confidence: {candidate.confidence}")
    _safe_print(f"Schema valid: {candidate.schema_valid}")
    _safe_print(f"Refinement used: {candidate.refinement_used}")
    _safe_print(f"Refinement accepted: {candidate.refinement_accepted}")
    _safe_print(f"Attempt count: {candidate.attempt_count}")
    _safe_print(f"Quality status: {candidate.quality_status or 'n/a'}")
    _safe_print(f"Jobs extracted: {candidate.extracted_job_count}")
    _safe_print(f"Useful titles: {candidate.useful_titles}")
    _safe_print(f"Generic labels: {candidate.generic_labels}")
    _safe_print(f"Unique URLs: {candidate.unique_urls}")
    _safe_print(f"Average description length: {candidate.average_description_length}")
    for error in candidate.validation_errors:
        _safe_print(f"Validation error: {error}")
    for warning in candidate.warnings:
        _safe_print(f"Warning: {warning}")
    for warning in candidate.quality_warnings:
        _safe_print(f"Quality warning: {warning}")
    for assumption in candidate.assumptions:
        _safe_print(f"Assumption: {assumption}")
    if candidate.rejection_reason:
        _safe_print(f"Rejection reason: {candidate.rejection_reason}")
    if candidate.approved_recipe_path:
        _safe_print(f"Approved recipe path: {candidate.approved_recipe_path}")
        _safe_print(f"Approved source id: {candidate.approved_source_id or 'none'}")
        _safe_print(f"Preview saved: {candidate.preview_saved}")
        _safe_print(f"Preview status: {candidate.preview_status}")
        _safe_print(f"Preview jobs: {candidate.preview_extracted_job_count}")
    for attempt in candidate.attempts:
        _safe_print("")
        _safe_print(f"Attempt {attempt.get('attempt_number')}")
        _safe_print(f"  Schema valid: {attempt.get('schema_valid')}")
        _safe_print(f"  Quality status: {attempt.get('quality_status')}")
        _safe_print(f"  Jobs extracted: {attempt.get('extracted_job_count')}")
        for warning in attempt.get("quality_warnings", []):
            _safe_print(f"  Quality warning: {warning}")
    _safe_print("")
    _safe_print("Suggested recipe YAML:")
    _safe_print(candidate.suggested_recipe_yaml)


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
