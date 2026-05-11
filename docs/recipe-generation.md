# Recipe Generation

Recipe suggestion is a manual assistant for proposing constrained YAML recipes from saved local calibration artifacts.

It uses folders under:

```text
output/recipe-calibration/
```

The suggestion flow reads local evidence such as `summary.md`, `selector-report.json`, `candidate-elements.html`, and `visible-text.txt`. It builds a compact prompt for an LLM and asks for strict JSON containing proposed recipe YAML, assumptions, warnings, confidence, and strategy.

It does not browse websites, fetch pages, inspect hidden endpoints, generate Python adapters, enable sources, or connect recipes to daily runs.

## Command

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --source-name "Eursap Jobs" --start-url https://eursap.eu/jobs
```

With an existing recipe for comparison:

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --existing-recipe sources/recipes/experimental/eursap-jobs.yaml
```

To write the suggestion to a file:

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --output scratch/suggested-eursap.yaml
```

Existing output files are not overwritten unless `--overwrite` is supplied.

To save the result as a pending review object:

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --save-candidate
```

This writes a candidate under:

```text
output/recipe-candidates/
```

The candidate stores the final YAML plus source name, artifact path, schema validation status, assumptions, warnings, evidence summary, referenced artifact files, and selected strategy. When refinement is used, it also stores the attempt history and final local quality summary.

## Local Refinement

The suggestion command can also run a bounded local validation/refinement loop:

```powershell
python -m job_agent.cli suggest-recipe output/recipe-calibration/<folder> --refine --max-attempts 3
```

The refinement loop stays inside the saved artifact folder:

1. Ask the LLM for strict JSON containing proposed recipe YAML.
2. Validate the YAML against the existing recipe schema.
3. Run the suggested recipe against local `page.html`.
4. Summarize extraction quality, including extracted jobs, useful titles, generic labels, unique URLs, and average description length.
5. If the schema is invalid or local extraction is poor, ask for a revised strict JSON response.
6. Stop when the candidate is acceptable or when `--max-attempts` is reached.

Refinement does not follow detail pages, fetch public URLs, browse sites, discover APIs, or write real recipe files automatically. If `--output` is supplied, only the final candidate YAML is written, and existing files are still protected unless `--overwrite` is supplied.

## Candidate Review

Pending recipe candidates are durable review objects between generation and any future approval/promotion step. They are not real recipe files, are not connected to sources, and are never executed by daily runs.

List candidates:

```powershell
python -m job_agent.cli list-recipe-candidates
python -m job_agent.cli list-recipe-candidates --status pending
```

Show one candidate:

```powershell
python -m job_agent.cli show-recipe-candidate <candidate-id>
```

Reject one candidate:

```powershell
python -m job_agent.cli reject-recipe-candidate <candidate-id> --reason "Selector captures filter navigation"
```

Rejecting a candidate records the rejection reason and timestamp. Approval and promotion to `sources/recipes/` are intentionally future work.

## Boundaries

- Proposes YAML only.
- Validates the suggested YAML against the existing recipe schema.
- Optional refinement validates extraction against local `page.html` only.
- `--save-candidate` creates a pending review object, not an active recipe.
- Does not edit real recipe files unless an explicit output path is provided.
- Does not enable a source or update the source registry.
- Does not promote candidates to `sources/recipes/`.
- Does not add pagination, browser automation, login handling, or arbitrary executable adapters.
- Tests use fake LLM clients and do not call Claude.

Next likely steps are approval/promotion review, recipe diff checks, and explicit source workflow integration after candidates have been inspected.
