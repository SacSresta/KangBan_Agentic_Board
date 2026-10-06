"""Single-run LangGraph workflow with SQLite checkpoints and exact-commit review."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import providers
from .contracts import Artifact, Status
from .tools import MAX_TOOL_CALLS, RepositoryTools, ToolError, git, redact, sanitize


class State(TypedDict, total=False):
    run_id: str
    plan: str
    notes: str
    tests: list
    tests_passed: bool
    route: str
    engineer_history: list
    tester_history: list
    frontier_history: list
    scrum_history: list
    resume_decision: dict | None
    inspected_resume: bool
    inspected_tool_ids: list[str]
    feedback: str


class Engine:
    def __init__(self, store, data_dir: Path):
        self.store, self.data_dir = store, Path(data_dir)
        self.graph = None
        self._saver_context = None
        self._executing: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._saver_context = AsyncSqliteSaver.from_conn_string(str(self.data_dir / "checkpoints.sqlite"))
        saver = await self._saver_context.__aenter__()
        await saver.setup()
        builder = StateGraph(State)
        builder.add_node("Scrum", self._scrum)
        builder.add_node("Engineer", self._engineer)
        builder.add_node("Tester", self._tester)
        builder.add_node("Frontier Review", self._frontier)
        builder.add_node("Human Review", self._human)
        builder.add_edge(START, "Scrum")
        builder.add_edge("Scrum", "Engineer")
        builder.add_edge("Engineer", "Tester")
        builder.add_conditional_edges("Tester", lambda state: state["route"], {"Engineer": "Engineer", "Frontier Review": "Frontier Review", "end": END})
        builder.add_conditional_edges("Frontier Review", lambda state: state["route"], {"Engineer": "Engineer", "Human Review": "Human Review", "end": END})
        builder.add_conditional_edges("Human Review", lambda state: state["route"], {"Engineer": "Engineer", "end": END})
        self.graph = builder.compile(checkpointer=saver)

    def _config(self, run_id: str):
        return {"configurable": {"thread_id": self.store.get_run(run_id).thread_id}, "recursion_limit": 40}

    def _phase(self, run_id, phase, status=Status.IN_PROGRESS):
        self.store.update_run(run_id, phase=phase)
        self.store.set_ticket_status(self.store.get_run(run_id).ticket_id, status)
        self.store.add_activity(run_id, phase, "phase", phase)

    def _block(self, run_id: str, reason: str):
        message = redact(reason, self.store)
        run = self.store.update_run(run_id, outcome="blocked", interruption_reason=message)
        self.store.set_ticket_status(run.ticket_id, Status.BLOCKED)
        self.store.add_activity(run_id, "System", "blocked", message)

    async def _assist_and_block(self, run_id: str, reason: str):
        run = self.store.get_run(run_id)
        self.store.add_activity(run_id, "Frontier Reviewer", "assistance_requested", reason)
        try:
            result = await providers.assistance(self.store.get_settings().frontier, self.store.get_ticket(run.ticket_id), reason)
            self.store.add_activity(run_id, "Frontier Reviewer", "assistance", json.dumps(sanitize(result.model_dump(mode="json"), self.store)))
        except providers.ProviderError as exc:
            self.store.add_activity(run_id, "Frontier Reviewer", "assistance_unavailable", str(exc))
        self._block(run_id, reason + "; inspect the retained worktree before explicitly resuming")

    async def execute(self, run_id: str):
        if run_id in self._executing:
            raise ToolError("This run is already executing")
        if self.store.get_run(run_id).outcome != "running":
            return
        self._executing[run_id] = asyncio.current_task()
        try:
            async with self._locks.setdefault(run_id, asyncio.Lock()):
                await self._drive(run_id)
        except asyncio.CancelledError:
            if self.store.get_run(run_id).outcome == "running":
                self._block(run_id, "Application stopped during active work. Inspect retained worktree before resuming; pending mutations will not replay.")
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ToolError, providers.ProviderError)) else f"Workflow failed ({type(exc).__name__}); inspect retained worktree"
            if isinstance(exc, ToolError) and "budget" in message:
                await self._assist_and_block(run_id, message)
            else:
                self._block(run_id, message)
        finally:
            self._executing.pop(run_id, None)

    async def _drive(self, run_id):
        config = self._config(run_id)
        snapshot = await self.graph.aget_state(config)
        state = snapshot.values or {}
        inspected = set(state.get("inspected_tool_ids", []))
        pending = self.store.pending_tools(run_id)
        if any(tool["call_id"] not in inspected for tool in pending):
            self._block(run_id, "Interrupted or failed repository tool requires explicit worktree inspection; it will not be replayed")
            return
        if snapshot.next and "Human Review" in snapshot.next:
            decision = state.get("resume_decision")
            if not decision:
                # Recover a durable API decision even if checkpoint preparation failed.
                changes = [event for event in self.store.list_activity(run_id) if event.event_type == "human_changes"]
                if changes:
                    decision = {"action": "changes", "message": changes[-1].message}
            if not decision:
                self._block(run_id, "Human checkpoint requires a recorded decision")
                return
            await self.graph.ainvoke(Command(resume=decision), config)
        elif snapshot.values:
            await self.graph.ainvoke(None, config)
        else:
            await self.graph.ainvoke({"run_id": run_id, "feedback": "", "resume_decision": None, "inspected_tool_ids": []}, config)

    async def resume(self, run_id: str, decision: dict | None = None):
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            await self._resume_checkpoint(run_id, decision)

    async def _resume_checkpoint(self, run_id, decision):
        config = self._config(run_id)
        snapshot = await self.graph.aget_state(config)
        if decision and decision["action"] == "approve":
            # Store.decide already validates the artifact and records human approval.
            # This resume executes only the human node and END, never the engineer.
            if "Human Review" in snapshot.next:
                await self.graph.ainvoke(Command(resume=decision), config)
            return
        if decision:
            if snapshot.values:
                if "Human Review" in snapshot.next:
                    await self.graph.aupdate_state(config, {"resume_decision": sanitize(decision, self.store)})
                else:
                    # Approved threads have reached END. A new human change cycle
                    # starts at Engineer instead of silently invoking an ended graph.
                    self.store.update_run(run_id, attempts=0)
                    await self.graph.aupdate_state(config, {"resume_decision": None, "feedback": redact(decision.get("message", "Human requested changes"), self.store)}, as_node="Scrum")
            return
        # Explicit UI inspection consent. Skip failed node replay and plan fresh work
        # from current files. Historical pending tools remain as immutable evidence.
        inspected_ids = [tool["call_id"] for tool in self.store.pending_tools(run_id)]
        if snapshot.values:
            await self.graph.aupdate_state(config, {"inspected_tool_ids": inspected_ids, "resume_decision": None, "feedback": "User inspected the retained worktree and requested a fresh continuation. Do not replay interrupted mutations."}, as_node="Scrum")
        else:
            await self.graph.aupdate_state(config, {"run_id": run_id, "inspected_tool_ids": inspected_ids, "feedback": "User inspected the retained worktree; continue from current files."}, as_node="Scrum")
        self.store.add_activity(run_id, "Human", "inspection_consent", "Explicit worktree inspection acknowledged; interrupted tool calls will not be replayed")

    async def _scrum(self, state: State):
        run_id = state["run_id"]
        self._phase(run_id, "Scrum")
        ticket = self.store.get_ticket(self.store.get_run(run_id).ticket_id)
        project = self.store.get_project(ticket.project_id)
        plan = json.dumps(sanitize({"goal": ticket.title, "acceptance_criteria": ticket.acceptance_criteria,
            "steps": ["Inspect relevant files", "Implement only ticket scope", "Run approved tests", "Review exact artifact", "Wait for human decision"],
            "test_commands": project.test_commands, "limits": {"repairs": 2, "tool_calls": 20, "timeout_seconds": 120}}, self.store))
        self.store.add_activity(run_id, "Scrum", "plan", plan)
        tools = RepositoryTools(self.store, self.data_dir, run_id)
        await tools.call("create_worktree", {}, role="Scrum")
        return {"plan": plan, "scrum_history": [plan]}

    async def _engineer(self, state: State):
        run_id = state["run_id"]
        self._phase(run_id, "Engineer")
        # The first mutation invalidates the previous reviewed/human-approved commit.
        self.store.update_run(run_id, artifact_commit=None, approved_commit=None)
        run = self.store.get_run(run_id)
        ticket = self.store.get_ticket(run.ticket_id)
        tools = RepositoryTools(self.store, self.data_dir, run_id)
        if not run.worktree:
            await tools.call("create_worktree", {}, role="Scrum")

        async def call(name, args):
            if name not in {"read", "search", "write", "run_test", "suggest_ticket"}:
                raise ToolError("Engineer requested an unavailable tool")
            return await tools.call(name, args)

        reserve = len(tools.project.test_commands) + 1
        result = await providers.engineer(self.store.get_settings().engineer, ticket, run.attempts,
            state.get("plan", "") + "\nFeedback: " + state.get("feedback", ""), call,
            lambda: MAX_TOOL_CALLS - tools.run.tool_calls - reserve, state.get("engineer_history"))
        notes = redact(result["notes"], self.store)
        self.store.add_activity(run_id, "Engineer", "implementation_notes", notes)
        return {"notes": notes, "engineer_history": sanitize(result["history"], self.store)}

    async def _tester(self, state: State):
        run_id = state["run_id"]
        self._phase(run_id, "Tester")
        tools = RepositoryTools(self.store, self.data_dir, run_id)
        tests = []
        for command in tools.project.test_commands:
            tests.append(await tools.call("run_test", {"command": command}, role="Tester"))
        passed = all(test["returncode"] == 0 and not test["timed_out"] for test in tests)
        if not tests:
            self.store.add_activity(run_id, "Tester", "test_output", "No approved test commands configured; no automatic tests were executed.")
        feedback = json.dumps(tests)
        if not passed:
            if tools.run.attempts < 2:
                self.store.update_run(run_id, attempts=tools.run.attempts + 1)
                self.store.add_activity(run_id, "Tester", "repair_requested", "Approved tests failed; bounded repair requested")
                return {"tests": tests, "tests_passed": False, "feedback": feedback, "tester_history": tests, "route": "Engineer"}
            await self._assist_and_block(run_id, "Tests failed after two repair rounds")
            return {"tests": tests, "tests_passed": False, "route": "end", "tester_history": tests}
        await tools.call("artifact", {}, role="Tester")
        return {"tests": tests, "tests_passed": True, "tester_history": tests, "route": "Frontier Review"}

    async def _frontier(self, state: State):
        run_id = state["run_id"]
        self._phase(run_id, "Frontier Review", Status.REVIEW)
        run = self.store.get_run(run_id)
        tools = RepositoryTools(self.store, self.data_dir, run_id)
        if not run.artifact_commit or await git(tools.root, "rev-parse", "HEAD") != run.artifact_commit or await git(tools.root, "status", "--porcelain"):
            self._block(run_id, "Artifact changed before frontier review; inspect worktree")
            return {"route": "end"}
        ticket = self.store.get_ticket(run.ticket_id)
        try:
            result = await providers.review(self.store.get_settings().frontier, ticket, run.artifact_commit, await tools.patch(run.artifact_commit), state["tests"], run.attempts)
            if result.verdict == "approve" and any(
                not result.acceptance_assessment.get(criterion, "").strip()
                for criterion in ticket.acceptance_criteria
            ):
                raise providers.ProviderError("Frontier approval omitted an acceptance-criteria assessment")
        except providers.ProviderError as exc:
            self._block(run_id, "Mandatory frontier review unavailable: " + str(exc))
            return {"route": "end"}
        # A concurrent external edit invalidates the exact artifact review.
        if await git(tools.root, "rev-parse", "HEAD") != run.artifact_commit or await git(tools.root, "status", "--porcelain"):
            self._block(run_id, "Artifact changed during frontier review; review discarded")
            return {"route": "end"}
        result = type(result).model_validate(sanitize(result.model_dump(mode="json"), self.store))
        saved = self.store.add_review(run_id, result)
        self.store.add_activity(run_id, "Frontier Reviewer", "review", redact(saved.model_dump_json(), self.store))
        history = [result.model_dump(mode="json")]
        if result.verdict == "unavailable":
            self._block(run_id, "Mandatory frontier review unavailable")
            return {"route": "end", "frontier_history": history}
        if result.verdict == "changes":
            if run.attempts < 2:
                self.store.update_run(run_id, attempts=run.attempts + 1)
                return {"route": "Engineer", "feedback": json.dumps(result.findings), "frontier_history": history}
            await self._assist_and_block(run_id, "Frontier reviewer rejected artifact after two repair rounds")
            return {"route": "end", "frontier_history": history}
        self.store.update_run(run_id, phase="Human Review", outcome="awaiting_human", interruption_reason=None)
        self.store.set_ticket_status(run.ticket_id, Status.AWAITING_HUMAN)
        return {"route": "Human Review", "frontier_history": history}

    async def _human(self, state: State):
        # Nothing before interrupt mutates records: the node restarts on resume.
        run = self.store.get_run(state["run_id"])
        decision = interrupt({"run_id": run.id, "artifact_commit": run.artifact_commit, "actions": ["approve", "changes"], "message": "Review this exact artifact. Approval never merges."})
        if decision.get("action") == "approve":
            return {"route": "end", "resume_decision": None}
        if decision.get("action") != "changes":
            raise ToolError("Invalid human checkpoint decision")
        self.store.update_run(run.id, attempts=0)
        return {"route": "Engineer", "feedback": redact(decision.get("message", "Human requested changes"), self.store), "resume_decision": None}

    async def inspect_artifact(self, run_id: str) -> Artifact:
        run = self.store.get_run(run_id)
        tools = RepositoryTools(self.store, self.data_dir, run_id)
        patch = await tools.patch(run.artifact_commit) if run.worktree and run.base_commit else ""
        return Artifact(run_id=run.id, artifact_commit=run.artifact_commit, base_commit=run.base_commit,
            branch=run.branch, worktree=run.worktree, patch=patch)

    async def check_provider(self, role: str):
        if role not in {"engineer", "frontier"}:
            raise ToolError("Provider role must be engineer or frontier")
        return await providers.check(getattr(self.store.get_settings(), role))

    async def shutdown(self):
        active = [task for task in self._executing.values() if task is not asyncio.current_task()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        if self._saver_context:
            await self._saver_context.__aexit__(None, None, None)
            self._saver_context = None
