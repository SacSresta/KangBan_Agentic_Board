# Frozen integration contract

The coordinator owns contracts.py, frontend/lib/contracts.ts, api-types.ts, manifests, locks, app.py and Electron. Request changes; do not edit these files independently.

## Storage/API ownership
Implement backend/kanban/storage.py, api.py and backend/tests/test_api.py. SQLite synchronous methods are acceptable for small local records; all mutations use transactions. `Store(data_dir: Path)` initializes app.sqlite. Errors `Conflict` (409), `NotFound` (404), `Invalid` (422). Methods:
- list_projects(), get_project(id), create_project(ProjectInput), update_project(id, ProjectInput)
- list_tickets(project_id=None), get_ticket(id), create_ticket(TicketInput), patch_ticket(id, TicketPatch)
- claim_next() -> Run | None (atomic, dependencies Done; priority 1 first, position then FIFO; only Ready)
- get_run(id), list_runs(ticket_id=None), update_run(id, **fields) -> Run
- set_ticket_status(id, Status) (internal transitions)
- add_activity(run_id, role, event_type, message) -> Activity; list_activity(run_id, after=0)
- add_review(run_id, ReviewInput) -> Review; list_reviews(run_id)
- get_settings() -> Settings; save_settings(Settings)
- queue_state() -> QueueState; set_queue_enabled(bool)
- recover() marks running runs Blocked on startup, pauses queue, records inspection message
- claim_resume(run_id) -> Run: atomically make Blocked run running, block competing runs, invalidate review/human approval and artifact if needed. Keep worktree. Resume is explicit inspection consent via UI.
- decide(run_id, action='approve'|'changes', Decision) checks exact reviewed artifact and worktree HEAD/cleanliness, returns Run; approve sets approved_commit/outcome approved, changes invalidates approvals sets running (atomic slot) and Ready graph continuation handled by app coordinator.
- confirm_merge(run_id, Decision) checks Approved and Git merge-base ancestry on configured base branch, returns Done run.
- begin_tool(run_id, call_id, name, args) -> bool: durable unique ID, reject previously pending/failed, completed duplicate may be read via tool_result. finish_tool(..., result, success); tool_result; pending_tools(run_id). Journal args/results as JSON strings.

`api.router` is APIRouter with prefix /api; uses request.app.state.store and request.app.state.engine. Engine exposes async resume(run_id, decision=None), async inspect_artifact(run_id)->Artifact, async check_provider(role)->ProviderCheck. Queue wake signal request.app.state.wake.set(). Endpoints:
GET /health {service:'kanban-agent-board', version:'0.1.0', mode:'deterministic'|'live'|'mixed'}
GET/POST /projects; PUT /projects/{id}
GET/POST /tickets; PATCH /tickets/{id}; GET /tickets/{id}
GET /queue; POST /queue/start; POST /queue/pause
GET /runs?ticket_id=; GET /runs/{id}; POST /runs/{id}/resume
GET /runs/{id}/activity?after=; GET /runs/{id}/reviews; GET /runs/{id}/artifact
POST /runs/{id}/approve, /changes, /confirm-merge (Decision body)
GET/PUT /settings; POST /providers/{role}/check
Every endpoint has a Pydantic response_model for OpenAPI. Duplicate queue start 409. Approve/changes persist decision then engine.resume for graph interruption. resume errors must not lose durable decision; graph resumes after explicit action.

## Engine ownership
Implement engine.py, providers.py, tools.py and backend/tests/test_engine.py. `Engine(store, data_dir)` owns separate AsyncSqliteSaver. `await start()` enters saver, compiles graph; `await execute(run_id)` handles fresh claimed run; `await resume(run_id, decision=None)` handles persisted graph restart/change request; `await shutdown()` pauses/cancels work safely and exits saver; inspect_artifact and check_provider per API. App owns worker task and executes one engine call at a time. Human approval may graph-resume synchronously without repository mutation; change request claims slot and wakes worker (worker calls execute on running run when not already active). All exceptions record sanitized messages and Blocked state. shutdown/recovery retain worktree and pending tool journal.

Deterministic provider uses an explicit JSON fence in ticket description: ```test-script followed by JSON {"edits":[{"path":"...","content":"..."}],"repair_edits":[...],"review_verdicts":["changes","approve"],"discovered_ticket":{"title":"...","description":"..."}}. Default creates KANBAN_TEST_ARTIFACT.md labelled deterministic. It runs real configured tests. Tests instantiate this interface. Live engineer uses read/search/write/run_test tools; reviewer uses structured ReviewInput, exact diff/test handoff, no mutating tools. Roles histories separate. Preserve no more than 2 repairs, 20 repository calls, 120s timeouts. Tool journal durable before mutation. Mutation pending on restart means inspection required, never auto replay. Human node interrupt is durable. Settings default deterministic visibly labelled everywhere.

## Frontend ownership
Implement frontend/app, frontend/components, frontend/lib/api.ts, frontend type config and frontend-specific tests only. Do not edit lib/contracts.ts or generated types. Root manifests coordinator owned. All API paths above, development base http://127.0.0.1:8765; production same origin. Poll queue/tickets and incremental run events. Use generated contract aliases. Adapt upstream board + chat/tool components and download full MIT licenses into third_party with provenance. Node packages available: Next16 React19, dnd-kit, lucide-react, react-markdown, Playwright. Desktop window.kanbanDesktop.selectRepository(): Promise<string|null>; expose optional type. No auto merge. Explicit resume inspection warning. Responsive board, sidebar settings, editor/detail dialogs, all lifecycle controls. Tests show persisted board and deterministic real-repo flow (coordinator writes overall Playwright tests).
