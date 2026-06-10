# Git Maintenance

This repository uses Git as both a version ledger and a backup system. The daily
maintenance job should keep work recoverable without turning commits into noisy
snapshots or risking private data.

## Observed Project Pattern

- The main remote is `origin` at `https://github.com/andreas-kielsgaard/Job-Agent.git`.
- `main` tracks `origin/main`.
- Local project work has usually happened on short `codex/...` topic branches,
  for example `codex/source-refactor-cleanup`, `codex/recipe-editor`, and
  `codex/usability-refinement`.
- Recent commit subjects are short, imperative, and progress-oriented:
  `Refine source management UI`, `Add source go-live readiness`,
  `Clean up source learning organization`, and similar.
- Broad work has still been committed in coherent slices: implementation,
  UI/workflow updates, source/recipe changes, docs, tests, and sample profile
  updates are grouped by purpose rather than dumped randomly.

## Hard Boundaries

- Never add behavior that submits applications, creates accounts, logs in,
  bypasses captcha or bot protection, uploads CVs, or sends emails.
- Never commit private local files or secrets. In this repo, private data and
  generated output normally live in ignored paths such as `profile/`, `.env`,
  `output/`, `jobs/seen_jobs.json`, `jobs/application_status.json`,
  `jobs/source_listing_index.json`, `sources/source-execution-readiness.yaml`,
  `sources/source-sessions.yaml`, and `sources/sessions/`.
- Treat all existing uncommitted changes as user-owned. Do not reset, checkout,
  clean, rebase, squash, or force-push unless the user explicitly asks.
- Do not use `git add -A` or `git add .` in an automated run when the worktree is
  busy. Stage explicit files or coherent file groups after reviewing status and
  diffs.
- Do not push with `--force` or `--force-with-lease`.

## Daily Routine

1. Run `git status --short --branch`.
2. Read the recent history with `git log --oneline --decorate --graph --all -30`
   and inspect local branches with `git branch -vv`.
3. If there are no uncommitted changes, push branches that are ahead of their
   upstreams. If nothing is ahead, report that the repository is already backed
   up.
4. If there are uncommitted changes, inspect them before staging:
   - Use `git diff --stat`, `git diff --name-only`, and targeted `git diff`
     reads for unfamiliar or risky files.
   - Check untracked files carefully before adding them.
   - Leave private-looking, generated, credential, cache, virtualenv, and local
     state files unstaged.
5. Choose the branch:
   - If already on an appropriate `codex/...` topic branch, continue there.
   - If on `main` with work to commit, create a topic branch before committing.
     Use `codex/<short-topic>` when one topic is clear, or
     `codex/daily-ledger-YYYYMMDD` when the changes are a mixed recovery batch.
   - If separate unrelated workstreams can be split safely, use separate topic
     branches. If splitting branches would require risky worktree surgery, keep
     one branch and split the work into coherent commits instead.
6. Build commits at a reasonable granularity. Good grouping examples:
   - Product documentation and agent guidance.
   - Profile example schema/content changes.
   - Scoring, highlights, prompts, and generated material wording.
   - Source registry, recipe, fetching, extraction, pagination, and readiness
     services.
   - Web routes, workflows, view models, templates, and CSS for one user-facing
     workflow.
   - Tests that verify the implementation grouped with, or immediately after,
     the implementation they cover.
7. Run targeted verification from `docs/agent-test-map.md` when code changed.
   For docs-only maintenance changes, no automated tests are required.
8. Commit with short imperative subjects matching the existing style.
9. Push each maintained branch. Use `git push -u origin <branch>` the first time
   a branch is pushed, then `git push` once upstream tracking exists.
10. Finish with a concise note listing branch names, commit hashes, tests run,
    files intentionally left uncommitted, and any push or verification failures.

## When To Stop And Report

Stop without committing if:

- A diff appears to contain secrets, real private profile details, tokens, or
  personal generated output outside ignored paths.
- The repository is in the middle of a merge, rebase, cherry-pick, or conflicted
  state.
- Tests fail in a way that appears related to the staged changes.
- The remote is missing, authentication fails, or Git refuses the push.
- The change set is too ambiguous to group safely.

In those cases, leave the worktree intact and report the exact blocker.
