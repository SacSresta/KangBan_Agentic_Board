# Repository instructions

Read PLAN.md before coding. It defines the approved implementation.

## Stack and scope
- Python: FastAPI, LangGraph, LangChain, SQLite.
- TypeScript: Next.js static frontend and Electron shell.
- Implement the coding MVP and Electron shell described in PLAN.md.
- Defer Kaggle automation, Laya, installers, and parallel application tasks.

## Coordination
- The coordinator may use up to three subagents with exclusive ownership.
- Define shared contracts before parallel implementation.
- Only the coordinator changes shared contracts, manifests, and lockfiles.
- Follow assigned ownership; request cross-boundary changes.
- Report changed files, checks, limitations, and integration needs.
- Finish assigned work; avoid duplicate or speculative implementations.

## Engineering invariants
- Exactly one application task may execute at a time.
- Human priority and dependencies constrain ticket selection.
- Repository changes happen in dedicated Git worktrees.
- Frontier review is mandatory before human approval.
- Approval applies to the exact reviewed artifact commit.
- Never merge automatically or bypass review through board actions.
- Persist workflow state; do not blindly replay interrupted mutations.
- Keep credentials out of frontend bundles, logs, and committed files.
- Preserve user changes and third-party license notices.
- Do not repair inherited Git metadata without explicit authorization.

## Verification and delivery
- Use deterministic model fixtures and temporary Git repositories in tests.
- Run relevant tests, TypeScript checks, and the static production build.
- Verify the integrated application before reporting completion.
- Document startup commands and limitations in REVIEW_HANDOFF.md.
- Distinguish mocked verification from live-model verification.
