# Source Recipe Experiments

These recipes are experimental fixtures for validating the constrained recipe engine. They are not enabled in daily runs and do not claim broad recruiter-site support.

Manual preview workflow: see `docs/recipe-preview.md`. Preview is for reviewing one selected recipe against one saved artifact, fixture, or explicit URL before deciding whether later source integration is worth considering.

Source registry and overview: see `docs/source-registry.md`. The registry is the review/configuration layer and projects recipe-backed sources into daily-run execution.

## Eursap Jobs

- URL: https://eursap.eu/jobs
- Local calibration artifacts used: `output/recipe-calibration/20260508-081750-eursap-eu-jobs/page.html`, `candidate-elements.html`, `selector-report.json`, `summary.md`, and `visible-text.txt`.
- Current status: live-calibrated experimental.
- Mode and strategy: `static_html`; each vacancy is a single anchor card matched by `a.looking__card`. The same anchor supplies the detail URL, while regex patterns extract the clean role title, job ID, location, language, start date, work type, and pay from the full card text.
- Evidence: broad containers such as `section.looking.looking--jobs`, `div.cont`, `#filter-search-result`, and `#filter-result` combine multiple jobs with surrounding page text, so the recipe deliberately scopes to single vacancy anchors.
- Known limitations: no detail-page enrichment; company and posted date are not exposed by the listing card; extraction depends on the current label text (`Job ID`, `Location`, `Language`, `Start Date`, `Work Type`, `Pay`).
- Later daily-run integration: reasonable to consider only after more saved-page checks, because this recipe now extracts real vacancy detail links without CTA, CV, country, services, or blog false positives from the captured page.

Fixture/local smoke:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/eursap-jobs.yaml tests/fixtures/real_sources/eursap-jobs.html --base-url https://eursap.eu/jobs
python -m job_agent.cli test-recipe sources/recipes/experimental/eursap-jobs.yaml output/recipe-calibration/20260508-081750-eursap-eu-jobs/page.html --base-url https://eursap.eu/jobs
```

## Whitehall Resources SAP Contract Jobs

- URL: https://www.whitehallresources.com/sap-jobs/contract/
- Local calibration artifacts used: `output/recipe-calibration/20260508-081748-www-whitehallresources-com-sap-jobs-contract/page.html`, `candidate-elements.html`, `selector-report.json`, `summary.md`, and `visible-text.txt`.
- Current status: live-calibrated experimental.
- Mode and strategy: `static_html`; real listing blocks are `div.job-item`, not the earlier fixture-only `.job-result`. Titles come from `h3 a`; detail URLs come from `a.button.view`; `a.button.apply` URLs ending in `#job-application` are rejected. Job ID and work arrangement/type are captured from listing text/selectors.
- Evidence: the saved calibration summary showed `.job-result` matched 0 elements, while repeated `div.job-item` blocks contained `Job ID`, arrangement/type, title, location, `View Job`, and `Apply Now`.
- Known limitations: listing cards do not expose rate or posted date; location and work arrangement are compact listing labels only; detail-page enrichment remains off.
- Later daily-run integration: reasonable to consider after another manual calibration pass, because the captured page now yields real `/job/` URLs and avoids application anchors.

Fixture/local smoke:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/whitehall-sap-contract.yaml tests/fixtures/real_sources/whitehall-sap-contract.html --base-url https://www.whitehallresources.com/sap-jobs/contract/
python -m job_agent.cli test-recipe sources/recipes/experimental/whitehall-sap-contract.yaml output/recipe-calibration/20260508-081748-www-whitehallresources-com-sap-jobs-contract/page.html --base-url https://www.whitehallresources.com/sap-jobs/contract/
```

## Montreal Associates Job Search

- URL: https://www.montrealassociates.com/uk/candidates/job-search/
- Local calibration artifacts used: `output/recipe-calibration/20260508-081801-www-montrealassociates-com-uk-candidates-job-search/page.html`, `candidate-elements.html`, `selector-report.json`, `summary.md`, and `visible-text.txt`.
- Current status: partial rendered experimental.
- Mode and strategy: `rendered_html`; saved rendered output contains stable repeated `li.job-data` entries inside `div.job-results`. Each job card has one detail link under `/uk/candidates/job/`, a title in `.xs-heading span`, a job reference, posted label, salary/rate, location, job type, and summary.
- Evidence: the old `.job-card` selector matched 0 elements. The saved rendered artifact exposes real job entries, but the static/filter region is large and noisy.
- Known limitations: this broad result page includes non-SAP jobs as well as SAP roles; the recipe depends on rendered page content; only local saved-page and fixture behavior has been checked; no detail-page enrichment is enabled.
- Later daily-run integration: not ready yet. It is worth keeping as partial evidence, but it needs another manual inspection pass before considering daily-run use or SAP-specific scoping.

Fixture/local smoke:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/montreal-associates-jobs.yaml tests/fixtures/real_sources/montreal-associates-jobs.html --base-url https://www.montrealassociates.com/uk/candidates/job-search/
python -m job_agent.cli test-recipe sources/recipes/experimental/montreal-associates-jobs.yaml output/recipe-calibration/20260508-081801-www-montrealassociates-com-uk-candidates-job-search/page.html --base-url https://www.montrealassociates.com/uk/candidates/job-search/
```

## Deferred Sources

- Accuro: compatibility checks mostly found service/category links and rendered mode returned 451. It is not worth automating yet.
