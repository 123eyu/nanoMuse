<!-- Thanks. Keep it small; one change per PR lands faster than three. -->

## What

<!-- One or two sentences. Link the issue if there is one: For #123 (not Fixes or Closes; an issue stays open until the fix has shipped) -->

## Why

<!-- The problem this solves, or what a person can do afterwards that they could not before. -->

## Checks

<!-- Tick what applies; the commands are in AGENTS.md, one block per part of the tree. -->

- [ ] The checks for the part I changed pass (runtime: `ruff check nanomuse tests scripts && ruff format --check nanomuse tests scripts && mypy && python -m pytest -q -m "not live"`; relay: `cd cloud && ruff check . && ruff format --check . && pytest`; web: `cd web && npm run check && npm run build`; desktop: `cd harness/dsh-nanomuse && pnpm build && pnpm typecheck && pnpm test`)
- [ ] New behaviour has a test; tests use `MockLLM`, no network
- [ ] If a person sees or does something differently: a line under `## [Unreleased]` in `CHANGELOG.md` (past tense, concrete, in the right section) and the page under `docs/` with its `docs/zh/` twin
- [ ] If a user-visible string was added or changed: every locale of that file (English first, then 简体中文, then the rest; same placeholders)
- [ ] If the web app changed: the build under `nanomuse/server/static` is committed
- [ ] If a catalogue changed: `node scripts/connectors-json.mjs --check` and `node scripts/providers-json.mjs --check` pass; the ideas JSON files stay byte-identical
- [ ] If a tool or the Sentinel changed: risk level, `assess()` summary and `docs/sentinel.md` are up to date; if a wire contract changed: `docs/hub.md`, `docs/cloud.md` or `docs/gui.md` too
- [ ] Commits follow Conventional Commits and carry a DCO sign-off (`git commit -s`)
- [ ] No secrets, internal hostnames or personal data in the diff; no version bump

## Screenshots

<!-- For anything visible in the app: before / after, phone-sized. Delete this section otherwise. -->
