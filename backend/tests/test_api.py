"""API persistence, execution-slot, review and manual-merge acceptance cases."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kanban.api import router
from kanban.contracts import (
    Artifact, Decision, ProjectInput, ProviderCheck, ReviewInput,
    Settings, Status, TicketInput, TicketPatch,
)
from kanban.storage import Conflict, Invalid, Store


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=15,
                          env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")}).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "README.md").write_text("Initial source\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    return repo


@pytest.fixture
def records(tmp_path, repository):
    store = Store(tmp_path / "data")
    project = store.create_project(ProjectInput(name="Fixture", repository_path=str(repository)))
    return store, project


class FakeEngine:
    def __init__(self, store):
        self.store = store
        self.decisions = []
        self.fail = False

    async def resume(self, run_id, decision=None):
        self.decisions.append((run_id, decision))
        if self.fail:
            raise RuntimeError("SECRET must never be returned")

    async def inspect_artifact(self, run_id):
        run = self.store.get_run(run_id)
        return Artifact(run_id=run.id, artifact_commit=run.artifact_commit,
                        base_commit=run.base_commit, branch=run.branch,
                        worktree=run.worktree, patch="fixture")

    async def check_provider(self, role):
        return ProviderCheck(ok=True, mode="deterministic", chat=True,
                             tool_calling=True, structured_response=True, message="Scripted test")


@pytest.fixture
def client(records):
    store, project = records
    app = FastAPI()
    app.state.store = store
    app.state.engine = FakeEngine(store)
    app.state.wake = asyncio.Event()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store, project, app.state.engine


def create(store, project, **fields):
    return store.create_ticket(TicketInput(project_id=project.id, title=fields.pop("title", "Ticket"),
                                           status=fields.pop("status", Status.READY), **fields))


def awaiting(records, tmp_path):
    store, project = records
    ticket = create(store, project)
    run = store.claim_next()
    branch = f"kanban/{run.id}"
    worktree = tmp_path / "worktree"
    base = git(project.repository_path, "rev-parse", "HEAD")
    git(project.repository_path, "worktree", "add", "-b", branch, str(worktree), base)
    (worktree / "feature.txt").write_text("Verified artifact\n")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "Artifact")
    commit = git(worktree, "rev-parse", "HEAD")
    run = store.update_run(run.id, artifact_commit=commit, base_commit=base,
                           worktree=str(worktree), branch=branch, phase="Human Review")
    store.add_review(run.id, ReviewInput(artifact_commit=commit, verdict="approve",
                                       acceptance_assessment={"feature": "passes"}))
    store.update_run(run.id, outcome="awaiting_human")
    store.set_ticket_status(ticket.id, Status.AWAITING_HUMAN)
    return store.get_run(run.id), worktree


def test_ticket_crud_dependencies_and_priority_survive_restart(client):
    http, store, project, engine = client
    dependency = http.post("/api/tickets", json={"project_id": project.id, "title": "Dependency"}).json()
    response = http.post("/api/tickets", json={"project_id": project.id, "title": "Work", "dependencies": [dependency["id"]]})
    assert response.status_code == 201
    ticket = response.json()
    edit = http.patch(f"/api/tickets/{ticket['id']}", json={"priority": 1, "description": "persisted", "status": "Ready"})
    assert edit.status_code == 200
    restarted = Store(store.data_dir)
    value = restarted.get_ticket(ticket["id"])
    assert (value.priority, value.description, value.dependencies) == (1, "persisted", [dependency["id"]])
    assert restarted.claim_next() is None
    assert http.get("/api/health").json()["mode"] == "deterministic"


def test_human_board_moves_cannot_bypass_review(client):
    http, store, project, engine = client
    ticket = create(store, project)
    for status in ["In Progress", "Review", "Awaiting Human", "Approved", "Blocked", "Done"]:
        assert http.patch(f"/api/tickets/{ticket.id}", json={"status": status}).status_code == 409
    assert http.post("/api/tickets", json={"project_id": project.id, "title": "Bypass", "status": "Approved"}).status_code == 409
    run = store.claim_next()
    assert http.patch(f"/api/tickets/{ticket.id}", json={"description": "new criteria"}).status_code == 409
    assert http.patch(f"/api/tickets/{ticket.id}", json={"position": 2}).status_code == 200


def test_dependencies_reject_cycles_and_cross_project(records, repository):
    store, project = records
    first = create(store, project)
    second = create(store, project, dependencies=[first.id])
    with pytest.raises(Invalid, match="cycle"):
        store.patch_ticket(first.id, TicketPatch(dependencies=[second.id]))
    other_project = store.create_project(ProjectInput(name="Other", repository_path=str(repository)))
    with pytest.raises(Invalid, match="same project"):
        create(store, other_project, dependencies=[first.id])
    with pytest.raises(Invalid, match="itself"):
        store.patch_ticket(first.id, TicketPatch(dependencies=[first.id]))


def test_atomic_claim_and_duplicate_start(records):
    store, project = records
    create(store, project, title="lower", priority=5)
    high = create(store, project, title="highest", priority=1)
    barrier = Barrier(6)

    def claim():
        separate = Store(store.data_dir)
        barrier.wait()
        return separate.claim_next()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: claim(), range(6)))
    claimed = [r for r in results if r]
    assert len(claimed) == 1
    assert claimed[0].ticket_id == high.id
    assert len(store.list_runs()) == 1
    assert store.queue_state().active_run_id == claimed[0].id


def test_concurrent_queue_start_returns_one_success(client):
    http, store, project, engine = client
    barrier = Barrier(4)

    def start():
        barrier.wait()
        return http.post("/api/queue/start").status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: start(), range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
    assert http.post("/api/queue/pause").json()["enabled"] is False


def test_dependency_done_unlocks_priority_and_fifo(records):
    store, project = records
    prerequisite = create(store, project, priority=5)
    dependent = create(store, project, priority=1, dependencies=[prerequisite.id])
    same_priority_first = create(store, project, priority=2)
    create(store, project, priority=2)
    assert store.claim_next().ticket_id == same_priority_first.id
    active = store.list_runs()[-1]
    store.update_run(active.id, outcome="blocked")
    store.set_ticket_status(active.ticket_id, Status.BLOCKED)
    store.set_ticket_status(prerequisite.id, Status.DONE)
    assert store.claim_next().ticket_id == dependent.id


def test_human_approval_does_not_merge_and_done_verifies_manual_merge(records, tmp_path):
    store, project = records
    run, worktree = awaiting(records, tmp_path)
    decision = Decision(artifact_commit=run.artifact_commit)
    base_before = git(project.repository_path, "rev-parse", "main")
    approved = store.decide(run.id, "approve", decision)
    assert approved.approved_commit == run.artifact_commit
    assert git(project.repository_path, "rev-parse", "main") == base_before
    with pytest.raises(Conflict, match="merge manually"):
        store.confirm_merge(run.id, decision)
    git(project.repository_path, "merge", "--ff-only", run.branch)
    done = store.confirm_merge(run.id, decision)
    assert done.outcome == "done"
    assert store.get_ticket(done.ticket_id).status == Status.DONE


def test_stale_decision_and_changed_worktree_invalidates_approval(records, tmp_path):
    store, project = records
    run, worktree = awaiting(records, tmp_path)
    with pytest.raises(Conflict, match="stale"):
        store.decide(run.id, "approve", Decision(artifact_commit="wrong"))
    decision = Decision(artifact_commit=run.artifact_commit)
    store.decide(run.id, "approve", decision)
    (worktree / "feature.txt").write_text("Changed after approval")
    with pytest.raises(Conflict, match="Artifact changed"):
        store.confirm_merge(run.id, decision)
    assert store.get_run(run.id).approved_commit is None
    assert store.get_run(run.id).outcome == "blocked"


def test_frontier_gate_and_changed_artifact_require_review(records, tmp_path):
    store, project = records
    run, worktree = awaiting(records, tmp_path)
    store.add_review(run.id, ReviewInput(artifact_commit=run.artifact_commit, verdict="changes"))
    with pytest.raises(Conflict, match="approving frontier"):
        store.decide(run.id, "approve", Decision(artifact_commit=run.artifact_commit))
    store.add_review(run.id, ReviewInput(artifact_commit=run.artifact_commit, verdict="approve"))
    store.decide(run.id, "approve", Decision(artifact_commit=run.artifact_commit))
    changed = store.update_run(run.id, artifact_commit="new-commit")
    assert changed.approved_commit is None and changed.outcome == "blocked"
    with pytest.raises(Conflict, match="current artifact"):
        store.add_review(run.id, ReviewInput(artifact_commit=run.artifact_commit, verdict="approve"))


def test_human_changes_claim_slot_and_preserve_durable_decision(client, tmp_path):
    http, store, project, engine = client
    run, worktree = awaiting((store, project), tmp_path)
    engine.fail = True
    response = http.post(f"/api/runs/{run.id}/changes", json={"artifact_commit": run.artifact_commit, "message": "Add edge case"})
    assert response.status_code == 503
    assert "SECRET" not in response.text
    assert store.get_run(run.id).outcome == "blocked"
    assert store.get_run(run.id).approved_commit is None
    assert any(e.event_type == "human_changes" and e.message == "Add edge case" for e in store.list_activity(run.id))
    assert store.claim_next() is None


def test_changes_conflict_with_other_active_run(records, tmp_path):
    store, project = records
    run, worktree = awaiting(records, tmp_path)
    create(store, project)
    competing = store.claim_next()
    with pytest.raises(Conflict, match="executing task"):
        store.decide(run.id, "changes", Decision(artifact_commit=run.artifact_commit))
    assert store.get_run(run.id).outcome == "awaiting_human"
    assert store.queue_state().active_run_id == competing.id


def test_recovery_preserves_tools_and_requires_explicit_resume(records):
    store, project = records
    create(store, project)
    run = store.claim_next()
    store.update_run(run.id, worktree="/retained/worktree", base_commit="base")
    store.begin_tool(run.id, "write-1", "write", {"path": "source.py"})
    store.set_queue_enabled(True)
    restarted = Store(store.data_dir)
    restarted.recover()
    recovered = restarted.get_run(run.id)
    assert recovered.outcome == "blocked" and recovered.worktree == "/retained/worktree"
    assert restarted.queue_state().enabled is False
    assert restarted.pending_tools(run.id)[0]["args"] == {"path": "source.py"}
    resumed = restarted.claim_resume(run.id)
    assert resumed.outcome == "running" and resumed.worktree == recovered.worktree
    with pytest.raises(Conflict, match="did not complete"):
        restarted.begin_tool(run.id, "write-1", "write", {"path": "source.py"})


def test_tool_journal_completed_replay_and_failed_rejection(records):
    store, project = records
    create(store, project)
    run = store.claim_next()
    assert store.begin_tool(run.id, "one", "read", {"path": "README.md"}) is True
    store.finish_tool(run.id, "one", {"content": "fixture"}, True)
    assert store.begin_tool(run.id, "one", "read", {"path": "README.md"}) is False
    assert store.tool_result(run.id, "one") == {"content": "fixture"}
    with pytest.raises(Conflict, match="different arguments"):
        store.begin_tool(run.id, "one", "read", {"path": "other"})
    store.begin_tool(run.id, "two", "write", {})
    store.finish_tool(run.id, "two", {"error": "failed"}, False)
    with pytest.raises(Conflict, match="did not complete"):
        store.begin_tool(run.id, "two", "write", {})
    with pytest.raises(Conflict, match="already finished"):
        store.finish_tool(run.id, "one", {})


def test_api_activity_incremental_artifact_and_provider_contract(client, tmp_path):
    http, store, project, engine = client
    run, worktree = awaiting((store, project), tmp_path)
    first = http.get(f"/api/runs/{run.id}/activity").json()
    event = store.add_activity(run.id, "Tester", "test_result", "Tests passed")
    incremental = http.get(f"/api/runs/{run.id}/activity", params={"after": first[-1]["id"]}).json()
    assert [e["id"] for e in incremental] == [event.id]
    assert http.get(f"/api/runs/{run.id}/artifact").json()["artifact_commit"] == run.artifact_commit
    assert http.get(f"/api/runs/{run.id}/reviews").json()[0]["verdict"] == "approve"
    assert http.post("/api/providers/engineer/check").json()["tool_calling"] is True
    assert http.get("/api/runs/missing").status_code == 404
    assert http.get(f"/api/runs/{run.id}/activity?after=-1").status_code == 422


def test_project_rejects_non_repository_and_missing_initial_commit(tmp_path):
    store = Store(tmp_path / "data")
    path = tmp_path / "empty"
    path.mkdir()
    with pytest.raises(Invalid):
        store.create_project(ProjectInput(name="No repo", repository_path=str(path)))
    git(path, "init", "-b", "main")
    with pytest.raises(Invalid):
        store.create_project(ProjectInput(name="No commit", repository_path=str(path)))


def test_settings_never_accept_credential_values_or_url_credentials(client):
    http, store, project, engine = client
    for config in [{"credential_env": "sk-secret-key"}, {"base_url": "https://user:secret@models.invalid"},
                   {"base_url": "https://models.invalid?key=secret"}]:
        rejected = http.put("/api/settings", json={"engineer": config})
        assert rejected.status_code == 422
        assert "sk-secret-key" not in rejected.text and "user:secret" not in rejected.text
    response = http.put("/api/settings", json={"engineer": {"kind": "openai", "model": "local", "base_url": "http://localhost:8000/v1", "credential_env": "MODEL_TOKEN"}})
    assert response.status_code == 200
    assert response.json()["engineer"]["credential_env"] == "MODEL_TOKEN"
    assert http.get("/api/health").json()["mode"] == "mixed"


def test_configuration_freezes_during_execution(records):
    store, project = records
    create(store, project)
    store.claim_next()
    with pytest.raises(Conflict, match="Model settings"):
        store.save_settings(Settings())
    with pytest.raises(Conflict, match="Test commands"):
        store.update_project(project.id, ProjectInput(name=project.name, repository_path=project.repository_path,
            test_commands=[["python", "-m", "pytest"]]))


def test_journal_and_activity_redact_configured_credential_values(records, monkeypatch):
    store, project = records
    monkeypatch.setenv("KANBAN_FIXTURE_SECRET", "fixture-sensitive-token")
    store.save_settings(Settings.model_validate({"engineer": {"credential_env": "KANBAN_FIXTURE_SECRET"}}))
    create(store, project)
    run = store.claim_next()
    args = {"content": "fixture-sensitive-token"}
    store.begin_tool(run.id, "secret-check", "write", args)
    store.finish_tool(run.id, "secret-check", {"content": "fixture-sensitive-token"})
    store.add_activity(run.id, "Engineer", "note", "token=fixture-sensitive-token")
    assert store.begin_tool(run.id, "secret-check", "write", args) is False
    assert store.tool_result(run.id, "secret-check")["content"] == "[REDACTED]"
    assert store.list_activity(run.id)[-1].message == "token=[REDACTED]"
    assert b"fixture-sensitive-token" not in store.path.read_bytes()


def test_changed_committed_head_blocks_approval(records, tmp_path):
    store, project = records
    run, worktree = awaiting(records, tmp_path)
    (worktree / "feature.txt").write_text("Another commit\n")
    git(worktree, "add", ".")
    git(worktree, "commit", "-m", "After frontier review")
    with pytest.raises(Conflict, match="Artifact changed"):
        store.decide(run.id, "approve", Decision(artifact_commit=run.artifact_commit))
    assert store.get_run(run.id).outcome == "blocked"
