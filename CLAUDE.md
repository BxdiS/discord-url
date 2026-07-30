# Notes for Claude

## Branches and PRs

Don't commit to `main` directly. Every change goes through a branch and a PR, even small ones.

Branch naming: `feat/` for features, `fix/` for fixes, `docs/` for documentation. Keep it descriptive — `feat/proxy-rotation`, `fix/rate-limit-edge-case`.

Open the PR right after pushing:

```bash
gh pr create --title "Your PR Title" --body "..."
```

The description should say what changed and why. A list of touched files isn't a description.

**Never add tool attribution lines** like "🤖 Generated with Claude Code" or similar metadata. Focus on the technical content — what the code does, why it matters, and any relevant context.

Once it's merged, clean up:

```bash
git branch -D branch-name
git push origin --delete branch-name
```

Only `main` should survive long-term.

## Before opening a PR

- Tests pass: `pytest -q`
- Code runs without errors
- Style matches the surrounding codebase
- New features documented in README.md if user-facing

## Documentation

When behavior changes, update the relevant docs:
- **README.md** — user-facing setup, usage, examples, troubleshooting
- **config.example.toml** — when config schema changes
- **This file (CLAUDE.md)** — if workflow or guidelines change

## Configuration and Examples

**config.example.toml** and **watchlist.example.txt** are reference templates. When adding fields or changing behavior:
- Update `config.example.toml` with the new setting and explanation
- Update `watchlist.example.txt` if format changes
- These files are copied to `config.toml` / `watchlist.txt` on first run, so they must stay in sync with actual code

## Testing

```bash
pytest -q
```

All tests must pass before opening a PR.

## Tooling

Needs `git` and `gh` on PATH, with `gh` authenticated. Use:
```bash
gh auth login
```

Or set `GITHUB_TOKEN` environment variable.
