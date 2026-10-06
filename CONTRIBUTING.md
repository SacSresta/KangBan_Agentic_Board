# Contributing

Read [PLAN.md](PLAN.md) and [AGENTS.md](AGENTS.md) before making changes. The original code remains all rights reserved; contribution guidance does not grant a license to redistribute it. Contributions require the repository owner's authorization.

## Setup

Use Git, uv, Python 3.14 and Node.js 24. Run `uv sync --locked` and `npm ci`. Keep `uv.lock` and `package-lock.json` committed. Use `.env.example` for local configuration; `.env`, application databases, worktrees, dependency directories, and generated builds must remain untracked.

For development, run the backend with `uv run --frozen python -m kanban` and the frontend with `npm run dev`. Build the static UI with `npm run build` before starting Electron with `npm run desktop`.

## Changes

- Preserve sequential execution, priority/dependency selection, isolated worktrees, exact-commit reviews, mandatory frontier review, and manual merging.
- Keep mutations durable and require inspection after interruption. Do not replay unfinished tool calls automatically.
- Preserve credential redaction and upstream license notices. Do not include private endpoint details, API keys, or runtime data in issues, screenshots, patches, or commits.
- Change backend contracts first, then regenerate `openapi.json` and `frontend/lib/api-types.ts` with `uv run --frozen python scripts/export_openapi.py` and `npm run types`.
- Use temporary Git repositories and deterministic model fixtures in tests. Distinguish model fixtures from live-model checks.

## Validation

```bash
uv run --frozen pytest -q
uv run --frozen python scripts/export_openapi.py --check
npm run check
npx playwright install chromium
npm run test:e2e
```

Port 8765 must be free for integration tests. Linux Electron tests need a display; use `xvfb-run -a npm run test:e2e` when Xvfb is available. Install browser/system dependencies with `npx playwright install --with-deps chromium` on a fresh Linux host.

Use a focused pull request that describes the problem, resulting behavior, checks performed, and any limitations. The PR template provides those fields. GitHub CI runs on pushes and pull requests without model credentials.
