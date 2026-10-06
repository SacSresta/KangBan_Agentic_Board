import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio

from kanban.contracts import Decision, ProjectInput, ProviderConfig, ReviewInput, Settings, Status, TicketInput
from kanban.engine import Engine
from kanban.storage import Conflict, Store
from kanban.tools import RepositoryTools, ToolError, scoped_path, subprocess_result


def git_sync(repository, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, *args], cwd=repository, env=env, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest_asyncio.fixture
async def setup_engine(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git_sync(repository, "init", "-b", "main")
    (repository / "value.txt").write_text("before\n")
    git_sync(repository, "add", ".")
    git_sync(repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "Initial commit")
    data = tmp_path / "data"
    store = Store(data)
    command = [sys.executable, "-c", "from pathlib import Path; assert Path('value.txt').read_text() == 'after\\n'"]
    project = store.create_project(ProjectInput(name="Fixture", repository_path=str(repository), test_commands=[command]))
    engine = Engine(store, data)
    await engine.start()
    yield store, engine, project, repository, data
    await engine.shutdown()


def claim(store, project, script):
    ticket = store.create_ticket(TicketInput(project_id=project.id, title="Change value", description="```test-script\n" + json.dumps(script) + "\n```", acceptance_criteria=["Value becomes after"], status=Status.READY))
    store.set_queue_enabled(True)
    run = store.claim_next()
    assert run.ticket_id == ticket.id
    return ticket, run


GOOD = {"edits": [{"path": "value.txt", "content": "after\n"}]}


@pytest.mark.asyncio
async def test_real_worktree_tests_review_human_and_manual_merge(setup_engine):
    store, engine, project, repository, _ = setup_engine
    (repository / "value.txt").write_text("source user changes\n")
    ticket, claimed = claim(store, project, GOOD)
    initial = git_sync(repository, "rev-parse", "main")
    await engine.execute(claimed.id)
    run = store.get_run(claimed.id)
    assert run.outcome == "awaiting_human", run.interruption_reason
    assert store.get_ticket(ticket.id).status == Status.AWAITING_HUMAN
    assert Path(run.worktree, "value.txt").read_text() == "after\n"
    assert (repository / "value.txt").read_text() == "source user changes\n"
    assert run.artifact_commit != initial
    assert any(event.event_type == "test_output" and "returncode" in event.message for event in store.list_activity(run.id))
    assert "after" in (await engine.inspect_artifact(run.id)).patch
    snapshot = await engine.graph.aget_state(engine._config(run.id))
    assert snapshot.next == ("Human Review",)
    assert snapshot.tasks[0].interrupts
    decision = Decision(artifact_commit=run.artifact_commit)
    store.decide(run.id, "approve", decision)
    await engine.resume(run.id, {"action": "approve", **decision.model_dump()})
    assert store.get_run(run.id).outcome == "approved"
    assert git_sync(repository, "rev-parse", "main") == initial
    with pytest.raises(Conflict):
        store.confirm_merge(run.id, decision)
    # Manual merge happens here in the fixture, never inside the application.
    (repository / "value.txt").write_text("before\n")
    git_sync(repository, "merge", "--ff-only", run.artifact_commit)
    assert store.confirm_merge(run.id, decision).outcome == "done"


@pytest.mark.asyncio
async def test_tests_fail_then_repair_and_frontier_changes(setup_engine):
    store, engine, project, _, _ = setup_engine
    script = {"edits": [{"path": "value.txt", "content": "bad\n"}], "repair_edits": [{"path": "value.txt", "content": "after\n"}], "review_verdicts": ["changes", "approve"]}
    _, run = claim(store, project, script)
    await engine.execute(run.id)
    run = store.get_run(run.id)
    assert run.outcome == "awaiting_human", run.interruption_reason
    assert run.attempts == 1
    assert any(event.event_type == "repair_requested" for event in store.list_activity(run.id))


@pytest.mark.asyncio
async def test_frontier_rejection_retests_new_exact_artifact(setup_engine):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, {**GOOD, "review_verdicts": ["changes", "approve"]})
    await engine.execute(run.id)
    reviews = store.list_reviews(run.id)
    assert [review.verdict for review in reviews] == ["changes", "approve"]
    assert reviews[0].artifact_commit != reviews[1].artifact_commit
    assert store.get_run(run.id).outcome == "awaiting_human"


@pytest.mark.asyncio
async def test_repeated_failure_is_bounded_and_gets_assistance(setup_engine):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, {"edits": [{"path": "value.txt", "content": "bad\n"}]})
    await engine.execute(run.id)
    run = store.get_run(run.id)
    assert run.outcome == "blocked"
    assert run.attempts == 2
    assert run.tool_calls <= 20
    assert any(event.event_type == "assistance" for event in store.list_activity(run.id))


@pytest.mark.asyncio
async def test_frontier_unavailable_blocks(setup_engine):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, {**GOOD, "review_verdicts": ["unavailable"]})
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "blocked"
    assert store.get_ticket(run.ticket_id).status == Status.BLOCKED


@pytest.mark.asyncio
async def test_frontier_approval_requires_each_acceptance_assessment(setup_engine, monkeypatch):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, GOOD)

    async def incomplete_review(config, ticket, artifact, patch, tests, attempt):
        return ReviewInput(artifact_commit=artifact, verdict="approve")

    monkeypatch.setattr("kanban.providers.review", incomplete_review)
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "blocked"
    assert store.list_reviews(run.id) == []
    assert "acceptance-criteria assessment" in store.get_run(run.id).interruption_reason


@pytest.mark.asyncio
async def test_unrelated_issue_creates_linked_backlog_ticket(setup_engine):
    store, engine, project, _, _ = setup_engine
    ticket, run = claim(store, project, {**GOOD, "discovered_ticket": {"title": "Separate issue", "description": "Unrelated bug; future work"}})
    await engine.execute(run.id)
    linked = [item for item in store.list_tickets() if item.linked_ticket_id == ticket.id]
    assert len(linked) == 1 and linked[0].status == Status.BACKLOG
    assert store.get_run(run.id).outcome == "awaiting_human"


@pytest.mark.asyncio
async def test_persisted_human_changes_and_approved_changes(setup_engine):
    store, engine, project, _, data = setup_engine
    _, run = claim(store, project, GOOD)
    await engine.execute(run.id)
    await engine.shutdown()
    restored = Engine(Store(data), data)
    await restored.start()
    try:
        for approve_first in [False, True]:
            old = store.get_run(run.id)
            decision = Decision(artifact_commit=old.artifact_commit, message="Please revise")
            if approve_first:
                store.decide(run.id, "approve", decision)
                await restored.resume(run.id, {"action": "approve", **decision.model_dump()})
            store.decide(run.id, "changes", decision)
            await restored.resume(run.id, {"action": "changes", **decision.model_dump()})
            assert store.get_run(run.id).outcome == "running"
            await restored.execute(run.id)
            current = store.get_run(run.id)
            assert current.outcome == "awaiting_human", current.interruption_reason
            assert current.artifact_commit != old.artifact_commit
            assert current.approved_commit is None
            with pytest.raises(Conflict):
                store.decide(run.id, "approve", decision)
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_pending_mutation_not_replayed_and_explicit_inspection(setup_engine):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, GOOD)
    tools = RepositoryTools(store, engine.data_dir, run.id)
    await tools.call("create_worktree", {})
    store.begin_tool(run.id, "interrupted-write", "write", {"path": "marker.txt", "content": "MUST NOT REPLAY"})
    store.recover()
    store.claim_resume(run.id)
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "blocked"
    assert not Path(tools.root, "marker.txt").exists()
    store.claim_resume(run.id)
    await engine.resume(run.id)
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "awaiting_human"
    assert not Path(tools.root, "marker.txt").exists()
    assert store.pending_tools(run.id)[0]["call_id"] == "interrupted-write"
    # Historical interruption stays acknowledged through a later human cycle.
    decision = Decision(artifact_commit=store.get_run(run.id).artifact_commit)
    store.decide(run.id, "changes", decision)
    await engine.resume(run.id, {"action": "changes", **decision.model_dump()})
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "awaiting_human"


@pytest.mark.asyncio
async def test_paths_commands_and_duplicate_execution_rejected(setup_engine, tmp_path):
    store, engine, project, _, _ = setup_engine
    _, run = claim(store, project, GOOD)
    tools = RepositoryTools(store, engine.data_dir, run.id)
    await tools.call("create_worktree", {})
    for name in ["../escape", "/tmp/escape", ".git/config", ".env", ".aws/credentials"]:
        with pytest.raises(ToolError):
            scoped_path(tools.root, name)
    outside = tmp_path / "outside"
    outside.write_text("secret")
    (tools.root / "escape-link").symlink_to(outside)
    with pytest.raises(ToolError):
        await tools.call("read", {"path": "escape-link"})
    with pytest.raises(ToolError):
        await tools.call("run_test", {"command": [sys.executable, "-c", "print('unapproved')"]})
    result = await tools.call("write", {"path": "value.txt", "content": "after\n"}, call_id="unique-write")
    count = tools.run.tool_calls
    assert await tools.call("write", {"path": "value.txt", "content": "after\n"}, call_id="unique-write") == result
    assert tools.run.tool_calls == count
    with pytest.raises(Conflict):
        await tools.call("write", {"path": "value.txt", "content": "other"}, call_id="unique-write")


@pytest.mark.asyncio
async def test_credentials_redacted_and_not_committed(setup_engine, monkeypatch):
    store, engine, project, _, data = setup_engine
    monkeypatch.setenv("KANBAN_SECRET_TEST", "secret-token-test-123")
    store.save_settings(Settings(engineer=ProviderConfig(credential_env="KANBAN_SECRET_TEST")))
    _, run = claim(store, project, GOOD)
    async def leaking_provider(config, ticket, attempt, handoff, tool, remaining, history=None):
        await tool("write", {"path": "value.txt", "content": "secret-token-test-123"})
        raise AssertionError("Credential write should have been rejected")
    monkeypatch.setattr("kanban.providers.engineer", leaking_provider)
    await engine.execute(run.id)
    assert store.get_run(run.id).outcome == "blocked"
    assert "secret-token-test-123" not in json.dumps([event.model_dump() for event in store.list_activity(run.id)])
    assert b"secret-token-test-123" not in (data / "app.sqlite").read_bytes()
    assert b"secret-token-test-123" not in (data / "checkpoints.sqlite").read_bytes()
    assert Path(store.get_run(run.id).worktree, "value.txt").read_text() == "before\n"


@pytest.mark.asyncio
async def test_deterministic_check_visibly_test_only(setup_engine):
    _, engine, _, _, _ = setup_engine
    result = await engine.check_provider("engineer")
    assert result.ok and result.mode == "deterministic"
    assert "no live" in result.message.lower()


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_escalates(setup_engine):
    store, engine, project, _, _ = setup_engine
    edits = [{"path": f"file-{number}.txt", "content": "fixture"} for number in range(30)]
    _, run = claim(store, project, {"edits": edits})
    await engine.execute(run.id)
    current = store.get_run(run.id)
    assert current.outcome == "blocked"
    assert current.tool_calls <= 20
    assert any(event.event_type == "assistance_requested" for event in store.list_activity(run.id))


@pytest.mark.asyncio
async def test_timeout_and_cancelled_test_retain_pending_journal(setup_engine):
    store, engine, project, repository, _ = setup_engine
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    timeout = await subprocess_result(command, repository, timeout=1)
    assert timeout["timed_out"] and timeout["returncode"] == -1
    project = store.update_project(project.id, ProjectInput(name=project.name, repository_path=project.repository_path, base_branch=project.base_branch, test_commands=[command]))
    _, run = claim(store, project, GOOD)
    tools = RepositoryTools(store, engine.data_dir, run.id)
    await tools.call("create_worktree", {})
    task = asyncio.create_task(tools.call("run_test", {"command": command}, call_id="cancelled-test", role="Tester"))
    for _ in range(100):
        if any(event.event_type == "tool_start" and "run_test" in event.message for event in store.list_activity(run.id)):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert store.pending_tools(run.id)[0]["call_id"] == "cancelled-test"
    with pytest.raises(Conflict):
        await tools.call("run_test", {"command": command}, call_id="cancelled-test")


@pytest.mark.asyncio
async def test_oversized_diff_blocks_instead_of_reviewing_truncated_patch(setup_engine):
    store, engine, project, _, _ = setup_engine
    script = {"edits": GOOD["edits"] + [
        {"path": "large-a.txt", "content": "a" * 300_000 + "\n"},
        {"path": "large-b.txt", "content": "b" * 300_000 + "\n"},
    ]}
    _, run = claim(store, project, script)
    await engine.execute(run.id)
    current = store.get_run(run.id)
    assert current.outcome == "blocked"
    assert "complete metadata or patch is required" in current.interruption_reason
    assert store.list_reviews(run.id) == []
