"""Verify the actual lifespan, queue worker, persisted graph and API together."""
import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from kanban.app import ProcessLock, create_app
from kanban.contracts import ProviderConfig
from kanban.providers import ProviderError, adapter


@pytest.fixture(autouse=True)
def isolated_application_env(tmp_path, monkeypatch):
    # Tests must never load the user's actual application .env credentials.
    monkeypatch.setattr("kanban.app.ROOT", tmp_path)


def git(repository, *args):
    return subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True).stdout.strip()


def wait_run(client, ticket_id, outcome="awaiting_human"):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        runs = client.get("/api/runs", params={"ticket_id": ticket_id}).json()
        if runs and runs[-1]["outcome"] == outcome:
            return runs[-1]
        if runs and runs[-1]["outcome"] == "blocked":
            pytest.fail(str(client.get(f"/api/runs/{runs[-1]['id']}/activity").json()))
        time.sleep(0.05)
    pytest.fail(f"Run did not reach {outcome}")


def test_real_background_queue_human_change_and_restart(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Integration fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "value.txt").write_text("old")
    (repo / "check.py").write_text("from pathlib import Path\nassert Path('value.txt').read_text() == 'new'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    base = git(repo, "rev-parse", "HEAD")
    script = "```test-script\n" + json.dumps({"edits": [{"path": "value.txt", "content": "new"}], "review_verdicts": ["approve"]}) + "\n```"
    data = tmp_path / "data"
    with TestClient(create_app(data, serve_ui=False)) as client:
        project = client.post("/api/projects", json={"name": "Integration", "repository_path": str(repo), "test_commands": [[sys.executable, "check.py"]]}).json()
        ticket = client.post("/api/tickets", json={"project_id": project["id"], "title": "Update value", "description": script, "status": "Ready"}).json()
        start = time.monotonic()
        assert client.post("/api/queue/start").status_code == 200
        assert time.monotonic() - start < 1
        first = wait_run(client, ticket["id"])
        assert git(repo, "rev-parse", "HEAD") == base
        assert client.post("/api/queue/pause").status_code == 200
        result = client.post(f"/api/runs/{first['id']}/changes", json={"artifact_commit": first["artifact_commit"], "message": "Repeat verification"})
        assert result.status_code == 200, result.text
        revised = wait_run(client, ticket["id"])
        assert revised["artifact_commit"] != first["artifact_commit"]
        assert client.post(f"/api/runs/{first['id']}/approve", json={"artifact_commit": first["artifact_commit"]}).status_code == 409
        approved = client.post(f"/api/runs/{revised['id']}/approve", json={"artifact_commit": revised["artifact_commit"]})
        assert approved.status_code == 200, approved.text
        assert approved.json()["outcome"] == "approved"
        assert git(repo, "rev-parse", "HEAD") == base
        events = client.get(f"/api/runs/{first['id']}/activity").json()
        assert any(e["event_type"] == "test_output" and "returncode" in e["message"] for e in events)
    with TestClient(create_app(data, serve_ui=False)) as client:
        assert client.get("/api/queue").json()["enabled"] is False
        run = client.get(f"/api/runs/{first['id']}").json()
        assert run["outcome"] == "approved"
        assert len(client.get(f"/api/runs/{first['id']}/activity").json()) >= len(events)
        git(repo, "merge", "--ff-only", run["branch"])
        assert client.post(f"/api/runs/{run['id']}/confirm-merge", json={"artifact_commit": run["approved_commit"]}).json()["outcome"] == "done"


def test_local_origins_and_exclusive_process_owner(tmp_path):
    lock = ProcessLock(tmp_path / "backend.lock")
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            ProcessLock(tmp_path / "backend.lock")
    finally:
        lock.close()
    with TestClient(create_app(tmp_path / "data", serve_ui=False)) as client:
        assert client.post("/api/queue/start", headers={"Origin": "https://unrelated.invalid"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "unrelated.invalid"}).status_code == 403
        assert client.get("/api/health").json()["mode"] == "deterministic"


def test_application_dotenv_supplies_provider_credentials_without_exposing_them(tmp_path, monkeypatch, caplog):
    variable = "KANBAN_DOTENV_TEST_TOKEN"
    secret = "dotenv-fixture-secret"
    for name in [variable, "KANBAN_DATA_DIR"]:
        # Register restoration even when the original variable was absent.
        monkeypatch.setenv(name, "fixture-cleared")
        monkeypatch.delenv(name)
    data = tmp_path / "dotenv-data"
    (tmp_path / ".env").write_text(
        f"export {variable}='{secret}' # fixture credential\nKANBAN_DATA_DIR='{data}'\n"
    )
    other = tmp_path / "different-working-directory"
    other.mkdir()
    (other / ".env").write_text(f"{variable}=wrong-working-directory-secret\n")
    monkeypatch.chdir(other)
    app = create_app(serve_ui=False)
    with TestClient(app) as client:
        assert app.state.store.data_dir == data
        config = ProviderConfig(kind="openai", model="fixture", base_url="http://127.0.0.1:1/v1", credential_env=variable)
        result = client.put("/api/settings", json={"frontier": config.model_dump()})
        assert result.status_code == 200
        assert secret not in result.text
        assert secret not in client.get("/api/settings").text
        assert secret not in client.get("/api/health").text
        assert adapter(config).openai_api_key.get_secret_value() == secret
    assert secret not in caplog.text


def test_existing_process_environment_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    variable = "KANBAN_DOTENV_TEST_TOKEN"
    monkeypatch.setenv(variable, "process-fixture-secret")
    (tmp_path / ".env").write_text(f"{variable}=file-fixture-secret\n")
    create_app(tmp_path / "data", serve_ui=False)
    config = ProviderConfig(kind="openai", model="fixture", credential_env=variable)
    assert adapter(config).openai_api_key.get_secret_value() == "process-fixture-secret"


def test_missing_credential_identifies_the_variable_and_dotenv_remedy(tmp_path, monkeypatch):
    variable = "KANBAN_DOTENV_TEST_TOKEN"
    monkeypatch.delenv(variable, raising=False)
    create_app(tmp_path / "data", serve_ui=False)
    with pytest.raises(ProviderError, match=rf"{variable}.*application .env.*restart"):
        adapter(ProviderConfig(kind="openai", model="fixture", credential_env=variable))
