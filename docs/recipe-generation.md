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

## Boundaries

- Proposes YAML only.
- Validates the suggested YAML against the existing recipe schema.
- Does not edit real recipe files unless an explicit output path is provided.
- Does not enable a source or update the source registry.
- Does not add pagination, browser automation, login handling, or arbitrary executable adapters.
- Tests use fake LLM clients and do not call Claude.

Next likely steps are iterative validation, recipe diff review, and a pending-review workflow before any suggested recipe is promoted.
