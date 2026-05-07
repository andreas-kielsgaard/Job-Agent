# Source Recipe Experiments

These recipes are experimental fixtures for validating the constrained recipe engine. They are not enabled in daily runs and do not claim broad recruiter-site support.

## Whitehall Resources SAP Contract Jobs

- URL: https://www.whitehallresources.com/sap-jobs/contract/
- Expected mode: `static_html`
- Compatibility checker result: normal HTML exposed real `/job/` URLs, but mixed them with filters, navigation, and `Apply Now` anchors.
- Recipe intent: scope extraction to visible job result cards, use title links rather than CTA links, accept only `/job/` URLs, and reject filters/application anchors.
- Current quality status: promising.
- Known limitations: fixture only covers listing-card extraction. Real pages may need selector adjustment if markup changes; detail-page enrichment is not enabled.

Fixture smoke:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/whitehall-sap-contract.yaml tests/fixtures/real_sources/whitehall-sap-contract.html --base-url https://www.whitehallresources.com/sap-jobs/contract/
```

## Montreal Associates Job Search

- URL: https://www.montrealassociates.com/uk/candidates/job-search/
- Expected mode: `rendered_html`
- Compatibility checker result: static HTML mostly exposed navigation/category links, while Playwright-rendered HTML exposed real job rows such as SAP ABAP Consultant.
- Recipe intent: validate that rendered visible job cards can be scoped with separate title and generic `View job` link selectors.
- Current quality status: promising, but depends on rendered page content.
- Known limitations: live URL testing may require Playwright. Fixture tests use saved rendered snippets and do not require Playwright.

Fixture smoke:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/montreal-associates-jobs.yaml tests/fixtures/real_sources/montreal-associates-jobs.html --base-url https://www.montrealassociates.com/uk/candidates/job-search/
```

Live testing of a `rendered_html` recipe may require:

```powershell
pip install -r requirements-playwright.txt
python -m playwright install chromium
python -m job_agent.cli test-recipe sources/recipes/experimental/montreal-associates-jobs.yaml https://www.montrealassociates.com/uk/candidates/job-search/
```

## Deferred Sources

- Eursap: current generic extraction over-matches navigation/CTA links such as Upload SAP Job, Improve my CV, and Services. It needs more inspection before a useful fixture recipe.
- Accuro: compatibility checks mostly found service/category links and rendered mode returned 451. It is not worth automating yet.
