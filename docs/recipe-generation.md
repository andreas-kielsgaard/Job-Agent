# Recipe Generation

Recipe generation is a local review workflow for turning saved calibration artifacts into constrained YAML recipes. It helps discover selectors and patterns, but it does not create executable adapters, enable sources, or change daily-run execution by itself.

The workflow uses local artifacts under:

```text
output/recipe-calibration/
```

Generation and refinement read files such as `summary.md`, `selector-report.json`, `candidate-elements.html`, `visible-text.txt`, and `page.html`. Tests use fake LLM clients; real API calls are not required for automated tests.

## Lifecycle

1. Capture a calibration artifact.
2. Suggest or refine recipe YAML from the saved artifact.
3. Save a pending candidate review object.
4. Review, show, or reject the candidate.
5. Explicitly approve a pending candidate into `sources/recipes/`.
6. Run a local preview against artifact `page.html` and save source health.
7. Adopt an approved recipe for a source by updating the source registry recipe path.
8. Separately use guarded execution setup, dry-run, or single-source run when you are ready.

Approval, adoption, and execution enablement are intentionally separate.

## CLI Workflow

Capture evidence:

```powershell
python -m job_agent.cli calibrate-recipe https://example.com/jobs --static
```

Suggest and save a pending candidate:

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --source-name "Eursap Jobs" --start-url https://eursap.eu/jobs --refine --max-attempts 3 --save-candidate
```

Review candidates:

```powershell
python -m job_agent.cli list-recipe-candidates
python -m job_agent.cli list-recipe-candidates --status pending
python -m job_agent.cli show-recipe-candidate <candidate-id>
python -m job_agent.cli reject-recipe-candidate <candidate-id> --reason "Selector captures filter navigation"
```

Approve a pending candidate:

```powershell
python -m job_agent.cli approve-recipe-candidate <candidate-id> --recipe-path sources/recipes/experimental/<name>.yaml --source-id <source-id>
```

Adopt an approved recipe for a source:

```powershell
python -m job_agent.cli adopt-approved-recipe <candidate-id> --source-id <source-id>
python -m job_agent.cli adopt-approved-recipe <candidate-id> --source-id <source-id> --prepare-disabled-execution-entry
```

Inspect workflow state:

```powershell
python -m job_agent.cli recipe-generation-status --source-id <source-id>
```

Verify or exercise later stages manually:

```powershell
python -m job_agent.cli test-recipe sources/recipes/experimental/<name>.yaml output/recipe-calibration/<folder>/page.html --base-url https://example.com/jobs --source-id <source-id>
python -m job_agent.cli dry-run-source <source-id>
python -m job_agent.cli run-source <source-id>
```

`dry-run-source` does not write packages. `run-source` writes normal outputs, but only for an enabled execution source.

## Web Workflow

Recipe-backed source detail pages show a Recipe Lifecycle panel:

- calibration artifacts found locally
- pending, rejected, and approved candidates
- latest approved recipe path
- source health status
- execution entry presence and enabled state

The Recipe Generation panel lets you select a local calibration artifact, optionally refine, and save a pending candidate. Candidate detail pages show schema status, local quality, warnings, attempt history, YAML, approval controls for pending candidates, and post-approval preview metadata for approved candidates.

Approved candidate pages also show an adoption control when opened from a source. Adoption updates the source registry recipe path to the approved recipe. A checkbox can prepare a disabled execution entry, but it never enables the source.

## Approval

Approval is the first point where generated YAML becomes a real recipe file. It:

- only works for `pending` candidates
- requires an explicit recipe path under `sources/recipes/`
- refuses overwrite unless `--overwrite` is supplied
- validates the YAML again
- requires local artifact `page.html`
- writes the recipe YAML
- runs local recipe preview against `page.html`
- saves source health when a source id is supplied
- marks the candidate `approved` with recipe path, source id, preview counts, and warnings

Approval does not create or enable daily-run execution entries and does not edit `sources/recruiting-sites.yaml`.

## Adoption

Adoption is the step after approval that tells a source registry entry to use the approved recipe path. It:

- only works for `approved` candidates
- requires a source id
- requires the approved recipe file to still exist under `sources/recipes/`
- updates `sources/source-registry.yaml` for that source
- records adoption metadata on the candidate
- can optionally prepare or refresh a disabled `recipe_html` execution entry

Preparing a disabled execution entry can edit `sources/recruiting-sites.yaml`, but only when explicitly requested with `--prepare-disabled-execution-entry` or the matching web checkbox. If an existing execution entry is enabled, adoption refuses to refresh it; disable it first and then update deliberately.

Adoption does not run the source, run the daily workflow, or enable execution.

## Troubleshooting

Missing `page.html`: approval fails before writing a recipe, because local preview and health would be misleading.

Invalid YAML: suggestion can save a candidate for review, but approval blocks schema-invalid YAML.

Zero jobs extracted: refinement treats this as poor quality. Approval can still preview the written recipe and source health will show failing/warning counts rather than enabling anything.

Approved recipe path differs from source registry path: source detail and `recipe-generation-status` show a workflow note. Review whether the source registry recipe path should be updated in a later, explicit task.

Source health is good but execution is disabled or missing: this is expected. Use guarded execution setup separately when you want daily-run behavior.

Enabled execution entry exists during adoption: adoption refuses disabled-entry refresh until the source is disabled, so an active daily-run source is not silently rewritten.

## Boundaries

- Generation/refinement uses local saved artifacts.
- No hidden endpoint or API discovery is performed.
- No arbitrary executable adapters are generated.
- No source is enabled automatically.
- Daily-run execution remains controlled by `sources/recruiting-sites.yaml`.
- Existing recipe files are not overwritten without explicit overwrite.
- Candidate approval writes recipe YAML and preview health only.
- Candidate adoption updates source registry only, unless disabled execution preparation is explicitly requested.
- Adoption never enables source execution.
- Tests do not call real Claude/API services.
