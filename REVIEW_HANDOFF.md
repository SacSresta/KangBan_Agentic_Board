# Implementation and review handoff

Delivered the local coding MVP and Electron shell from PLAN.md. The inherited parent Git metadata was neither repaired nor overwritten. On the user's subsequent instruction to initialize and publish this project, a separate `.git` directory was created inside the application directory. `uv.lock` and `package-lock.json` are included for reproducible installation.

## Run locally

Validated runtime: Python 3.14.4, Node 24.19.0, npm 11.17.0. GitHub preparation also verified the full backend suite and schema on Python 3.12.14. Python 3.12+ and Node 22.12+ are declared; other platform combinations were not executed during this handoff.

```bash
uv sync --locked
npm ci
npm run build
.venv/bin/python -m kanban
```

Open http://127.0.0.1:8765. Alternatively, `npm run desktop` starts/reuses the API and opens Electron. Its first use may download the Electron binary. Windows uses `.venv/Scripts/python.exe`. `KANBAN_PYTHON` selects another installed project interpreter; `KANBAN_DATA_DIR` selects another data directory. For development, run the backend and `npm run dev` separately; Next.js listens on http://127.0.0.1:3000.

In Settings, select a separate Git repository with an initial commit, its local base branch, and approved test commands as argument arrays. Example: `[["python", "-m", "pytest", "-q"]]`. Configure each provider independently. Credential fields accept environment-variable names only.

The backend loads the application directory's `.env` at startup, with existing process environment values taking precedence. Credential variable names in Settings must match the keys in that file. Restart the backend after editing `.env`; this also applies when Electron reuses a service started separately. The selected coding repository's `.env` is never loaded automatically. Credentials remain excluded from settings responses and logs.

Create tickets in Backlog or Ready, set priority and dependencies, then start the queue. Priority 1 is highest. Only Done dependencies unlock work. Dragging moves Backlog/Ready tickets and changes ordering; review states are controlled by the workflow. Pause stops new claims while active work reaches its review/blocking checkpoint.

Inspect the exact artifact, real test output, activity and frontier assessment before approval. Approval records a commit and never merges. Merge the shown `kanban/<run-id>` branch into the selected base branch yourself, then Confirm manual merge. Confirmation verifies that the approved commit is an ancestor of that branch.

## Implemented behavior

- A single FastAPI worker serves the static Next.js export and owns an asynchronous sequential queue. A process lock prevents two backends sharing a data directory; a SQLite unique index prevents two active runs.
- SQLite stores projects, ticket priorities/dependencies, runs, ordered activity, reviews, human decisions and a unique tool-execution journal. A separate SQLite AsyncSqliteSaver persists LangGraph checkpoints and human interruptions.
- Explicit Scrum, Engineer, Tester, Frontier Review and Human Review nodes use separate role histories and concise handoffs. Engineering uses real scoped repository tools; testing executes only approved argv commands without a shell. Frontier review has no modifying tools.
- Dedicated Git branches/worktrees preserve source changes. File tools reject traversal, excluded credential/Git files and symlink escapes. Artifact creation rejects unsafe staged paths and configured model secrets. Review and human decisions check the exact commit and clean worktree HEAD.
- Automatic repair is bounded, repository calls are limited to 20 per run, and model requests/test commands time out after 120 seconds. Exhaustion records frontier assistance or Blocked status. Oversized Git metadata/diffs block review rather than presenting a partial patch as complete.
- Awaiting Human and Approved release the execution slot. Requested changes invalidate approval and repeat testing/frontier review. Changed worktrees invalidate approval. Done requires verified manual merging.
- Restart pauses the queue and blocks interrupted work for inspection. Journals and worktrees remain. Explicit resume acknowledges particular unfinished tool IDs; historical entries remain, and interrupted calls are never blindly replayed.
- The frontend includes all eight columns, priority/dependency editing, settings, review inbox, incremental activity polling, test output, patches, review history, human decisions and inspected resume. Desktop and mobile project selection are available.
- Electron disables Node integration, enables context isolation and renderer sandboxing, restricts navigation, and exposes only repository selection through its preload. It reuses matching services, rejects unrelated port occupants, and stops only its own Python child on exit.

Runtime data defaults to `.data/app.sqlite`, `.data/checkpoints.sqlite`, and `.data/worktrees/`; it is gitignored. Branches/worktrees remain available for manual merging and inspection. Approved test commands run code from the selected repository with the current user's permissions; this MVP does not add OS-level isolation for repository tests.

## Verification results

| Check | Result |
|---|---|
| Python 3.14 dependency installation/imports | Passed with locked versions |
| `.venv/bin/python -m pytest -q` | 39 passed |
| FastAPI OpenAPI generation and `npm run types` | Passed; generated TypeScript contracts included |
| `npm run typecheck` | Passed |
| `npm run build` | Passed; Next.js 16.3.8 static export |
| Playwright browser/Electron integration | 3 passed |
| Production Electron startup with Chromium sandbox enabled | Passed |
| npm dependency audit | Zero known vulnerabilities at verification |

The backend suite covers persistent editing/dependencies, simultaneous queue starts/claims, FIFO/priority ordering, real edits/tests/artifacts, failing-test repairs, frontier changes/unavailability, linked unrelated issues, exact-commit approvals, manual-merge confirmation, restart checkpoints, inspection consent, traversal/symlink restrictions, command allowlisting, duplicate execution IDs, credential redaction, timeout/cancellation, exhausted tools and oversized diffs. Integrated lifespan tests also exercise background execution, human changes, persistence, local browser-origin restrictions, application .env loading, process-environment precedence and credential availability without exposure through API responses.

Playwright creates temporary repositories/databases, exercises ticket creation/editing and drag transitions, runs real tests, reviews patches, requests changes, rejects stale approval, approves without merging, confirms a fixture's manual merge, and checks mobile layout. Electron tests verify the same persisted board, preload/security preferences, reuse without stopping an external service, owned shutdown during a real test, and recovery without another tool execution.

To repeat browser/desktop verification, stop any service using port 8765 first:

```bash
npx playwright install chromium
npm run test:e2e
```

Linux CI needs a display; with Xvfb installed use `xvfb-run -a npm run test:e2e`. This session used an Xvfb package extracted under `/tmp`, without changing system packages. Test fixtures never use this directory's inherited Git metadata.

## Live-model status and boundaries

**Live-model verification was not performed** during the automated implementation checks. A subsequently supplied application .env was checked only for the presence of the configured credential variable; no credential values were printed and no model request was sent. Default engineer/frontier results are visibly labelled deterministic fixtures. Those fixtures exercise actual Git operations and test subprocesses but script implementation/review decisions.

OpenAI-compatible/vLLM and Anthropic adapters are implemented. Connection checks require chat, an actual tool invocation with result handoff, and schema-conforming structured output. Run each check and a small ticket through mandatory frontier review before treating an endpoint as operational. Ordinary chat success is insufficient.

Windows/macOS Electron lifecycle remains unverified. Worktree retention is intentional; there is no automatic cleanup. Files and review patches are bounded at 512 KB. A manual merge confirmation verifies ancestry; it does not rerun tests on the merged base branch. The local MVP assumes a trusted selected repository and approved test commands.

Kaggle startup, Cloudflare management, Laya classification, installers, authentication systems and concurrent application tasks remain deferred as requested.

## GitHub preparation

Target repository: [SacSresta/KangBan_Agentic_Board](https://github.com/SacSresta/KangBan_Agentic_Board). The user selected all rights reserved for original project code. LICENSE and Python/npm metadata reflect that choice; complete upstream MIT notices remain intact.

Added setup and API documentation, `.env.example` with empty credentials, broader runtime/credential/build ignore rules, consistent line endings, runtime version files, contribution/security guidance, issue forms, a PR template, and Dependabot updates for npm, uv, and GitHub Actions.

CI uses immutable action commit references and read-only repository permissions. It checks locked installation and 39 backend tests on both Python 3.12 and 3.14, generated OpenAPI/TypeScript consistency, frontend type checking, static export, and browser/Electron integration under Xvfb. No model credentials are configured for CI.

Local publication checks passed: 39 backend tests on each Python version, schema consistency, regenerated API types, production build, fresh-source type checking without preexisting Next.js generated files, workflow validation with actionlint, and npm audit with zero known vulnerabilities. The browser/Electron suite was not rerun locally during publication preparation because the user's backend was already using port 8765; its earlier result was 3 passed. The initial hosted CI result is available in GitHub Actions after pushing.

The new Git repository excludes `.env`, databases, worktrees, caches, virtual environments, node_modules, test traces, and generated frontend builds. Candidate source files were also checked for the configured local secret values before publication.

## Attribution

Board components adapt [George Griffiths' Kanban example](https://github.com/Georgegriff/react-dnd-kit-tailwind-shadcn-ui); activity/tool presentation adapts [LangChain Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui). Complete upstream MIT notices and source/component provenance are in `third_party/`. This application uses its own dependencies and FastAPI activity API rather than the LangGraph Server protocol. Workflow persistence uses [LangGraph's SQLite checkpointing](https://docs.langchain.com/oss/python/langgraph/persistence).
