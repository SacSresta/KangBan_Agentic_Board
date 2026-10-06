# Kanban Agent Board: implementation handoff

## 1. Deliverable and boundaries

Build a local coding MVP in the current `kanban_agent_board` directory using Python, TypeScript, Next.js, Electron, FastAPI, LangGraph, and SQLite.

The user creates and prioritizes tickets. One task executes at a time through Scrum, Engineer, Tester, and Frontier Reviewer roles. Agents work in an isolated Git worktree. The user approves the reviewed changes and merges them manually.

Include persistent tickets, real repository tools, agent activity, frontier assistance, restart recovery, and an Electron shell. Defer Kaggle startup, Cloudflare management, Laya classification, installers, authentication systems, and simultaneous application tasks.

The directory is empty. Current inherited Git metadata fails with `cannot chdir to 'E:/'`; do not repair or overwrite it. Application tests must create separate temporary Git repositories.

**Documentation delivery:** when execution mode starts, create `PLAN.md` containing this plan, `AGENTS.md` containing the repository instructions, and the requested `AGENT.md` pointing to `AGENTS.md`.

## 2. Architecture and agreed behavior

### Runtime and UI

- Use Python ≥3.12; verify dependencies against the available Python 3.14 runtime. Use a project virtual environment, npm, and committed dependency lockfiles.
- Run one FastAPI process with one worker. It owns ticket storage and a background asynchronous queue consumer; starting a run returns immediately.
- Use SQLite for application records and a separate SQLite LangGraph checkpoint database. Keep runtime data in a gitignored application data directory.
- Build Next.js as a static export with client-side board interactions. FastAPI serves the exported UI and `/api` on `127.0.0.1:8765`.
- During development, Next.js runs on port 3000 and calls the Python API. Allow only the required local development origins.
- Electron starts the configured project Python interpreter, waits for API readiness, and opens the locally served UI. If a matching service already exists, reuse it; report unrelated port conflicts.
- Electron keeps Node integration disabled and context isolation enabled. Its preload exposes only desktop functions such as repository selection.
- On desktop exit, checkpoint active work and stop the Python process only if Electron started it. Reopening recovers persisted state.

### Reusable frontend

Adapt the board interactions from [George Griffiths’ MIT-licensed Kanban example](https://github.com/Georgegriff/react-dnd-kit-tailwind-shadcn-ui). Port the components into Next.js; use the new project’s dependency versions rather than copying its old Vite setup. Preserve attribution and license notices.

Reuse useful conversation and tool-message components from [LangChain’s MIT-licensed Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui). Connect them to our FastAPI activity API; do not assume our backend implements the LangGraph Server protocol.

Deliver:

- Board columns: Backlog, Ready, In Progress, Review, Awaiting Human, Approved, Blocked, Done.
- Ticket editor: description, acceptance criteria, priority, and dependencies.
- Ticket detail: phase, activity, findings, test output, patch, and frontier review.
- Controls: start/pause queue, resume blocked work, approve, request changes, and confirm manual merge.
- Settings: repository, approved test commands, model endpoints, and served model names.

Dragging cards changes only permitted human-controlled states and ordering. It cannot bypass review or mark active work complete.

### Ticket and API contract

Before delegating implementation, the coordinator defines shared Pydantic models and a matching TypeScript contract:

- `Project`: repository path, selected base branch, approved test commands.
- `Ticket`: ID, project, title, description, acceptance criteria, priority, dependencies, status.
- `Run`: ticket, current phase, attempts, checkpoint/thread ID, worktree, base commit, artifact commit, outcome.
- `Activity`: ordered event ID, run, role, event type, message, timestamp.
- `Review`: artifact commit, verdict, findings, and acceptance-criteria assessment.

Use FastAPI OpenAPI to generate frontend API types. The coordinator owns contract changes and generation.

Provide API groups for projects, tickets and dependencies, queue controls, run inspection/resume, activity retrieval, artifacts/reviews, human decisions, and provider connection checks. Use incremental event polling for v1 rather than adding a streaming infrastructure dependency.

Return conflicts for duplicate starts, invalid transitions, and stale approval attempts. Do not expose model credentials through settings responses or logs.

### Agent workflow

Implement explicit LangGraph nodes:

`Scrum → Engineer → Tester → Frontier Review → Human Review`

- **Scrum:** inspect eligible tickets, respect human priorities, dependencies, and FIFO ties; prepare a bounded plan. Suggestions become ticket activity or linked tickets.
- **Engineer:** read and modify the selected worktree using tools; prepare implementation notes.
- **Tester:** execute approved test commands, inspect results, and return actionable findings.
- **Frontier Reviewer:** review the exact artifact commit, acceptance criteria, diff, and test evidence. This role cannot modify files.
- **Human Review:** use a persisted LangGraph interruption for approval or requested changes.

Keep role histories separate and pass concise structured handoffs. Persist checkpoints using the SQLite checkpointer. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

Initial limits: two repair rounds, 20 tool calls per run, and 120 seconds per model request or test command. Record exhausted limits and escalate to frontier assistance. If assistance or mandatory frontier review remains unavailable, mark the task Blocked.

A frontier approval permits Awaiting Human; it never permits automatic merging. Human approval records the artifact commit. Requested changes invalidate previous approvals and require testing and frontier review again.

Awaiting Human and Approved tasks release the execution slot so the next eligible ticket can proceed. Blocked dependencies remain ineligible.

### Model connections and execution tools

- Connect existing vLLM endpoints through LangChain’s OpenAI-compatible chat adapter with configurable base URL, model name, and credential environment-variable reference.
- Configure frontier models independently through OpenAI or Anthropic adapters. Keep Kaggle hosting code outside this implementation.
- Include a deterministic test provider that produces scripted tool calls and review results. Label test mode visibly; never present it as real-model verification.
- Verify tool calling and structured-response behavior before claiming a live endpoint works. Ordinary chat compatibility is insufficient.

Repository tools must:

- Require a user-selected Git repository with an initial commit.
- Create a dedicated branch and worktree from the selected base commit; preserve existing source changes.
- Read, search, and edit files only inside that worktree, rejecting traversal and symlink escapes.
- Run only user-configured test commands as argument arrays, with `shell=False`, timeouts, and captured output.
- Create an artifact commit for review; preserve the branch and worktree for manual merging.
- Exclude Git internals and credential files from agent file tools.
- Record tool execution and artifacts durably. After an interrupted mutation, retain the worktree and pause for inspection instead of blindly replaying it.

After the user merges manually, the “Confirm merge” action verifies that the approved artifact commit is contained in the configured base branch before marking Done.

## 3. Subagent implementation sequence

The coding coordinator uses **up to three implementation subagents**, inheriting the user’s selected cheaper model. This coding parallelism is separate from the application’s sequential task execution.

### Phase A — coordinator establishes the foundation

Create the documentation, package structure, dependency manifests, shared contracts, test-provider interface, and startup commands. Freeze contracts before parallel coding.

The coordinator owns root documentation, shared contracts, dependency manifests/lockfiles, application startup wiring, and final integration.

### Phase B — parallel assignments

| Subagent | Exclusive ownership | Required handoff |
|---|---|---|
| Storage/API | Backend storage, project/ticket APIs, queue claiming, human decisions, API tests | Persistent CRUD, atomic single-task claiming, dependency enforcement, contract-compliant endpoints |
| Agent engine/tools | Backend orchestration, model adapters, repository tools, checkpoints, engine tests | Working LangGraph workflow, real worktree edits/tests, review gating, bounded recovery |
| Frontend | Frontend components, board interactions, API hooks, frontend tests | Reused board UI, ticket/activity/review views, settings and working API integration |

Each subagent receives this plan, its ownership boundaries, frozen interfaces, and acceptance cases. It must report changed files, checks performed, outstanding limitations, and integration requirements.

Subagents must not change shared contracts or dependency manifests independently. They request those changes from the coordinator and continue unrelated work.

### Phase C — integration and Electron

The coordinator connects storage, engine, and UI; generates API types; resolves integration failures; and completes Electron lifecycle handling.

An idle subagent may receive a bounded integration or test task after its first handoff. Avoid starting duplicate implementations or speculative extra agents.

### Phase D — review handoff

Run the acceptance suite and produce `REVIEW_HANDOFF.md` with startup commands, checks and results, live-model validation status, known limitations, source attribution, and the implementation summary.

Do not declare completion based solely on subagent reports. The coordinator must verify the integrated application.

## 4. Acceptance and final-review tests

Use pytest for backend/engine tests and Playwright for browser and Electron integration.

Required scenarios:

1. Create and edit tickets; priorities and dependencies survive restart.
2. Concurrent start requests produce only one active task.
3. The deterministic provider edits a temporary repository, runs its real tests, and produces a reviewable artifact.
4. Failed tests trigger repair; repeated failure triggers bounded frontier assistance or Blocked.
5. An unrelated discovered bug becomes a linked ticket without silently expanding scope.
6. Frontier rejection returns work for changes; frontier approval enables human review.
7. Changed artifacts invalidate previous reviews and human approvals.
8. Restart preserves tickets, activity, worktrees, and checkpoints; interrupted mutations are not blindly replayed.
9. Approval performs no merge; Done requires verification of a manual merge.
10. File traversal, symlink escape, unapproved commands, and duplicate tool executions are rejected or safely handled.
11. Browser and Electron show the same persisted board; desktop exit/reopen recovers state.
12. Frontend type checking, production static export, backend tests, and integration checks pass.

A live endpoint smoke test additionally verifies chat, tool invocation, test-result handoff, and frontier review. If credentials or a running endpoint are unavailable, document that limitation explicitly.

## 5. Repository agent instructions

The approved repository instructions are reproduced in AGENTS.md. AGENT.md points to AGENTS.md and this plan.
