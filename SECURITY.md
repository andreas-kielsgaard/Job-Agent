# Secret Handling

This repo is intended to be safe to publish publicly.

## Never Commit API Keys

Put real secrets only in `.env`, which is ignored by Git:

```text
ANTHROPIC_API_KEY=your_real_key_here
CLAUDE_MODEL=claude-sonnet-4-20250514
```

Commit `.env.example`, but never commit `.env`.

## Never Commit Personal Profile Data

Put real CV, contact details, address, preferences, and work history only in `profile/`, which is ignored by Git.

Commit `profile.example/`, but never commit `profile/`.

## If A Key Is Exposed

Revoke it in the provider dashboard and create a new key. Treat keys pasted into chats, issues, commits, logs, or screenshots as exposed.

## Before Publishing

Run:

```powershell
rg -n "sk-ant|ANTHROPIC_API_KEY=.*[A-Za-z0-9_-]{20,}" .
rg -n "your.real.email@example.com|your phone|your address" .
git status --short
```

The search should not show a real key. It is fine for docs and code to mention the variable name `ANTHROPIC_API_KEY`.

## GitHub Actions

If this agent is later run from GitHub Actions, store the key as a GitHub repository secret named `ANTHROPIC_API_KEY`, not in a file.
