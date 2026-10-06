"""Durable application records and atomic execution-slot ownership.

Each write uses BEGIN IMMEDIATE; the partial unique index is the final guard
against two callers claiming the execution slot, including separate Store objects.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from uuid import uuid4

from .contracts import (
    Activity, Decision, Project, ProjectInput, QueueState, Review, ReviewInput,
    Run, Settings, Status, Ticket, TicketInput, TicketPatch,
)


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class ArtifactChanged(Conflict):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "app.sqlite"
        with self._connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,
                    outcome TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_run
                    ON runs ((1)) WHERE outcome = 'running';
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    role TEXT NOT NULL, event_type TEXT NOT NULL,
                    message TEXT NOT NULL, timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tools (
                    run_id TEXT NOT NULL, call_id TEXT NOT NULL, name TEXT NOT NULL,
                    args TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                    started_at TEXT NOT NULL, finished_at TEXT,
                    PRIMARY KEY (run_id, call_id)
                );
                INSERT OR IGNORE INTO meta VALUES ('queue_enabled', 'false');
            """)

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise Conflict("Another task already owns the execution slot") from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, table, id, model):
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (id,)).fetchone()
        if not row:
            raise NotFound(f"{model.__name__} not found")
        return model.model_validate_json(row["data"])

    @staticmethod
    def _put(db, table, obj):
        if table == "runs":
            db.execute("INSERT INTO runs(id,ticket_id,outcome,data) VALUES (?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET outcome=excluded.outcome,data=excluded.data",
                       (obj.id, obj.ticket_id, obj.outcome, obj.model_dump_json()))
        else:
            db.execute(f"INSERT INTO {table}(id,data) VALUES (?,?) "
                       "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (obj.id, obj.model_dump_json()))

    @staticmethod
    def _git(path, *args):
        try:
            result = subprocess.run(["git", "-C", str(path), *args], shell=False,
                                    capture_output=True, text=True, timeout=15,
                                    env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Invalid("Git could not inspect the selected repository") from exc
        if result.returncode:
            raise Invalid("Git repository or selected branch could not be verified")
        return result.stdout.strip()

    def _validate_project(self, value):
        path = Path(value.repository_path).expanduser().resolve()
        if not path.is_dir():
            raise Invalid("Repository directory does not exist")
        root = self._git(path, "rev-parse", "--show-toplevel")
        if Path(root).resolve() != path:
            raise Invalid("Select the Git repository root")
        self._git(path, "rev-parse", "--verify", "HEAD^{commit}")
        self._git(path, "check-ref-format", "--branch", value.base_branch)
        self._git(path, "rev-parse", "--verify", f"refs/heads/{value.base_branch}^{{commit}}")
        return value.model_copy(update={"repository_path": str(path)})

    def list_projects(self):
        with self._connection() as db:
            return [Project.model_validate_json(r[0]) for r in db.execute("SELECT data FROM projects ORDER BY rowid")]

    def get_project(self, id):
        with self._connection() as db:
            return self._get(db, "projects", id, Project)

    def create_project(self, value: ProjectInput):
        value = self._validate_project(value)
        project = Project(id=str(uuid4()), **value.model_dump())
        with self._connection(True) as db:
            self._put(db, "projects", project)
        return project

    def update_project(self, id, value: ProjectInput):
        value = self._validate_project(value)
        with self._connection(True) as db:
            old = self._get(db, "projects", id, Project)
            if old.test_commands != value.test_commands:
                for row in db.execute("SELECT data FROM runs WHERE outcome='running'"):
                    active = Run.model_validate_json(row[0])
                    if self._get(db, "tickets", active.ticket_id, Ticket).project_id == id:
                        raise Conflict("Test commands cannot change during an executing task")
            if old.repository_path != value.repository_path or old.base_branch != value.base_branch:
                for row in db.execute("SELECT data FROM runs WHERE outcome != 'done'"):
                    run = Run.model_validate_json(row[0])
                    if self._get(db, "tickets", run.ticket_id, Ticket).project_id == id:
                        raise Conflict("Repository and base branch are fixed while unfinished runs exist")
            project = Project(id=id, **value.model_dump())
            self._put(db, "projects", project)
        return project

    def list_tickets(self, project_id=None):
        with self._connection() as db:
            values = [Ticket.model_validate_json(r[0]) for r in db.execute("SELECT data FROM tickets")]
        return sorted((t for t in values if project_id is None or t.project_id == project_id),
                      key=lambda t: (t.priority, t.position, t.created_at, t.id))

    def get_ticket(self, id):
        with self._connection() as db:
            return self._get(db, "tickets", id, Ticket)

    def _dependencies(self, db, ticket):
        if len(ticket.dependencies) != len(set(ticket.dependencies)):
            raise Invalid("Dependencies must be unique")
        for dep in ticket.dependencies:
            if dep == ticket.id:
                raise Invalid("A ticket cannot depend on itself")
            other = self._get(db, "tickets", dep, Ticket)
            if other.project_id != ticket.project_id:
                raise Invalid("Dependencies must belong to the same project")
            pending, seen = list(other.dependencies), set()
            while pending:
                current = pending.pop()
                if current == ticket.id:
                    raise Invalid("Dependencies cannot contain a cycle")
                if current not in seen:
                    seen.add(current)
                    pending.extend(self._get(db, "tickets", current, Ticket).dependencies)
        if ticket.linked_ticket_id:
            other = self._get(db, "tickets", ticket.linked_ticket_id, Ticket)
            if other.project_id != ticket.project_id:
                raise Invalid("Linked tickets must belong to the same project")

    def create_ticket(self, value: TicketInput):
        if value.status not in {Status.BACKLOG, Status.READY}:
            raise Conflict("New tickets must start in Backlog or Ready")
        timestamp = now()
        ticket = Ticket(id=str(uuid4()), created_at=timestamp, updated_at=timestamp, **value.model_dump())
        with self._connection(True) as db:
            self._get(db, "projects", ticket.project_id, Project)
            self._dependencies(db, ticket)
            self._put(db, "tickets", ticket)
        return ticket

    def patch_ticket(self, id, patch: TicketPatch):
        fields = patch.model_dump(exclude_none=True)
        with self._connection(True) as db:
            old = self._get(db, "tickets", id, Ticket)
            if "status" in fields and (old.status not in {Status.BACKLOG, Status.READY} or
                                       fields["status"] not in {Status.BACKLOG, Status.READY}):
                raise Conflict("Board moves may only change Backlog and Ready tickets")
            if old.status not in {Status.BACKLOG, Status.READY} and any(
                    field in fields for field in ("title", "description", "acceptance_criteria", "dependencies")):
                raise Conflict("Request changes before modifying a ticket with an existing run")
            ticket = Ticket.model_validate({**old.model_dump(), **fields, "updated_at": now()})
            self._dependencies(db, ticket)
            self._put(db, "tickets", ticket)
        return ticket

    def _ticket_status(self, db, id, status):
        ticket = self._get(db, "tickets", id, Ticket).model_copy(update={"status": Status(status), "updated_at": now()})
        self._put(db, "tickets", ticket)
        return ticket

    def set_ticket_status(self, id, status):
        with self._connection(True) as db:
            return self._ticket_status(db, id, status)

    def claim_next(self):
        with self._connection(True) as db:
            if db.execute("SELECT 1 FROM runs WHERE outcome='running'").fetchone():
                return None
            tickets = [Ticket.model_validate_json(r[0]) for r in db.execute("SELECT data FROM tickets")]
            by_id = {t.id: t for t in tickets}
            eligible = [t for t in tickets if t.status == Status.READY and
                        all(by_id[d].status == Status.DONE for d in t.dependencies)]
            if not eligible:
                return None
            ticket = min(eligible, key=lambda t: (t.priority, t.position, t.created_at, t.id))
            timestamp, id = now(), str(uuid4())
            run = Run(id=id, ticket_id=ticket.id, thread_id=id, created_at=timestamp, updated_at=timestamp)
            self._put(db, "runs", run)
            self._ticket_status(db, ticket.id, Status.IN_PROGRESS)
            self._activity(db, id, "Scrum", "claimed", "Ticket claimed for sequential execution")
            return run

    def get_run(self, id):
        with self._connection() as db:
            return self._get(db, "runs", id, Run)

    def list_runs(self, ticket_id=None):
        with self._connection() as db:
            rows = db.execute("SELECT data FROM runs ORDER BY rowid") if ticket_id is None else db.execute(
                "SELECT data FROM runs WHERE ticket_id=? ORDER BY rowid", (ticket_id,))
            return [Run.model_validate_json(r[0]) for r in rows]

    def update_run(self, id, **fields):
        if set(fields) - (set(Run.model_fields) - {"id", "ticket_id", "thread_id", "created_at"}):
            raise Invalid("Unsupported run update")
        with self._connection(True) as db:
            old = self._get(db, "runs", id, Run)
            if "artifact_commit" in fields and old.artifact_commit != fields["artifact_commit"]:
                fields["approved_commit"] = None
                if old.outcome in {"approved", "awaiting_human"} and "outcome" not in fields:
                    fields.update(outcome="blocked", interruption_reason="Artifact changed; testing and frontier review are required")
                    self._ticket_status(db, old.ticket_id, Status.BLOCKED)
            run = Run.model_validate({**old.model_dump(), **fields, "updated_at": now()})
            self._put(db, "runs", run)
            return run

    def _activity(self, db, run_id, role, event_type, message):
        message = self._redact(db, message)
        timestamp = now()
        cursor = db.execute("INSERT INTO activity(run_id,role,event_type,message,timestamp) VALUES (?,?,?,?,?)",
                            (run_id, role, event_type, message, timestamp))
        return Activity(id=cursor.lastrowid, run_id=run_id, role=role, event_type=event_type,
                        message=message, timestamp=timestamp)

    def add_activity(self, run_id, role, event_type, message):
        with self._connection(True) as db:
            self._get(db, "runs", run_id, Run)
            return self._activity(db, run_id, role, event_type, message)

    def list_activity(self, run_id, after=0):
        with self._connection() as db:
            self._get(db, "runs", run_id, Run)
            return [Activity(**dict(r)) for r in db.execute("SELECT * FROM activity WHERE run_id=? AND id>? ORDER BY id", (run_id, after))]

    def add_review(self, run_id, value: ReviewInput):
        with self._connection(True) as db:
            run = self._get(db, "runs", run_id, Run)
            if not run.artifact_commit or run.artifact_commit != value.artifact_commit:
                raise Conflict("Review must refer to the current artifact commit")
            review = Review(id=str(uuid4()), run_id=run_id, created_at=now(), **self._redact(db, value.model_dump()))
            db.execute("INSERT INTO reviews(id,run_id,data) VALUES (?,?,?)", (review.id, run_id, review.model_dump_json()))
            if run.approved_commit:
                self._put(db, "runs", run.model_copy(update={"approved_commit": None, "outcome": "blocked", "updated_at": now()}))
                self._ticket_status(db, run.ticket_id, Status.BLOCKED)
            return review

    def list_reviews(self, run_id):
        with self._connection() as db:
            self._get(db, "runs", run_id, Run)
            return [Review.model_validate_json(r[0]) for r in db.execute("SELECT data FROM reviews WHERE run_id=? ORDER BY seq", (run_id,))]

    def get_settings(self):
        with self._connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
            return Settings.model_validate_json(row[0]) if row else Settings()

    @staticmethod
    def _redact(db, value):
        row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        settings = Settings.model_validate_json(row[0]) if row else Settings()
        secrets = [os.environ.get(provider.credential_env, "") for provider in (settings.engineer, settings.frontier)
                   if provider.credential_env]

        def clean(item):
            if isinstance(item, str):
                for secret in secrets:
                    if secret:
                        item = item.replace(secret, "[REDACTED]")
                return item
            if isinstance(item, dict):
                return {clean(key): clean(content) for key, content in item.items()}
            if isinstance(item, list):
                return [clean(content) for content in item]
            return item

        return clean(value)

    def save_settings(self, value: Settings):
        with self._connection(True) as db:
            if db.execute("SELECT 1 FROM runs WHERE outcome='running'").fetchone():
                raise Conflict("Model settings cannot change during an executing task")
            db.execute("INSERT INTO meta VALUES ('settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value.model_dump_json(),))
        return value

    def queue_state(self):
        with self._connection() as db:
            enabled = json.loads(db.execute("SELECT value FROM meta WHERE key='queue_enabled'").fetchone()[0])
            active = db.execute("SELECT id FROM runs WHERE outcome='running'").fetchone()
            return QueueState(enabled=enabled, active_run_id=active[0] if active else None)

    def set_queue_enabled(self, enabled):
        with self._connection(True) as db:
            previous = json.loads(db.execute("SELECT value FROM meta WHERE key='queue_enabled'").fetchone()[0])
            if enabled and previous:
                raise Conflict("Queue is already started")
            db.execute("UPDATE meta SET value=? WHERE key='queue_enabled'", (json.dumps(bool(enabled)),))
            active = db.execute("SELECT id FROM runs WHERE outcome='running'").fetchone()
            return QueueState(enabled=enabled, active_run_id=active[0] if active else None)

    def recover(self):
        with self._connection(True) as db:
            db.execute("UPDATE meta SET value='false' WHERE key='queue_enabled'")
            for row in db.execute("SELECT data FROM runs WHERE outcome='running'").fetchall():
                run = Run.model_validate_json(row[0])
                message = "Execution stopped before completion. Inspect the retained worktree and tool journal, then explicitly resume."
                self._put(db, "runs", run.model_copy(update={"outcome": "blocked", "interruption_reason": message, "updated_at": now()}))
                self._ticket_status(db, run.ticket_id, Status.BLOCKED)
                self._activity(db, run.id, "System", "recovery", message)

    def claim_resume(self, run_id):
        with self._connection(True) as db:
            run = self._get(db, "runs", run_id, Run)
            if run.outcome != "blocked":
                raise Conflict("Only Blocked work can be resumed")
            if db.execute("SELECT 1 FROM runs WHERE outcome='running'").fetchone():
                raise Conflict("Another task already owns the execution slot")
            run = run.model_copy(update={"outcome": "running", "approved_commit": None,
                                         "artifact_commit": None, "interruption_reason": None, "updated_at": now()})
            self._put(db, "runs", run)
            self._ticket_status(db, run.ticket_id, Status.IN_PROGRESS)
            self._activity(db, run_id, "Human", "resume", "User explicitly resumed after inspecting retained work")
            return run

    def _reviewed_artifact(self, db, run, decision):
        if not run.artifact_commit or decision.artifact_commit != run.artifact_commit:
            raise Conflict("Decision is stale; inspect the current artifact")
        row = db.execute("SELECT data FROM reviews WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run.id,)).fetchone()
        review = Review.model_validate_json(row[0]) if row else None
        if not review or review.artifact_commit != run.artifact_commit or review.verdict != "approve":
            raise Conflict("The exact artifact needs an approving frontier review")
        if not run.worktree:
            raise Conflict("Reviewed worktree is unavailable")
        try:
            head = self._git(run.worktree, "rev-parse", "HEAD")
            dirty = self._git(run.worktree, "status", "--porcelain", "--untracked-files=all")
        except Invalid as exc:
            raise ArtifactChanged("Reviewed worktree could not be verified") from exc
        if head != run.artifact_commit or dirty:
            raise ArtifactChanged("Artifact changed in the worktree; testing and frontier review are required")

    def _invalidate_changed_artifact(self, run_id):
        with self._connection(True) as db:
            run = self._get(db, "runs", run_id, Run)
            if run.outcome in {"awaiting_human", "approved"}:
                self._put(db, "runs", run.model_copy(update={"outcome": "blocked", "approved_commit": None,
                    "interruption_reason": "Reviewed worktree changed; test and review it again", "updated_at": now()}))
                self._ticket_status(db, run.ticket_id, Status.BLOCKED)
                self._activity(db, run_id, "System", "artifact_changed", "Reviewed artifact is no longer the clean worktree HEAD; prior approval invalidated")

    def decide(self, run_id, action, decision: Decision):
        try:
            return self._decide(run_id, action, decision)
        except ArtifactChanged:
            self._invalidate_changed_artifact(run_id)
            raise

    def _decide(self, run_id, action, decision: Decision):
        if action not in {"approve", "changes"}:
            raise Invalid("Unknown human decision")
        with self._connection(True) as db:
            run = self._get(db, "runs", run_id, Run)
            if run.outcome not in ({"awaiting_human"} if action == "approve" else {"awaiting_human", "approved"}):
                raise Conflict("Run is not awaiting this human decision")
            self._reviewed_artifact(db, run, decision)
            if action == "changes" and db.execute("SELECT 1 FROM runs WHERE outcome='running'").fetchone():
                raise Conflict("Pause or finish the executing task before requesting changes")
            run = run.model_copy(update={"outcome": "approved" if action == "approve" else "running",
                                         "approved_commit": run.artifact_commit if action == "approve" else None,
                                         "interruption_reason": None, "updated_at": now()})
            self._put(db, "runs", run)
            self._ticket_status(db, run.ticket_id, Status.APPROVED if action == "approve" else Status.IN_PROGRESS)
            self._activity(db, run_id, "Human", f"human_{action}", decision.message or ("Artifact approved for manual merge" if action == "approve" else "Changes requested; repeat tests and frontier review"))
            return run

    def confirm_merge(self, run_id, decision: Decision):
        try:
            return self._confirm_merge(run_id, decision)
        except ArtifactChanged:
            self._invalidate_changed_artifact(run_id)
            raise

    def _confirm_merge(self, run_id, decision: Decision):
        with self._connection(True) as db:
            run = self._get(db, "runs", run_id, Run)
            if run.outcome != "approved" or not run.approved_commit or decision.artifact_commit != run.approved_commit:
                raise Conflict("Only the exact human-approved artifact can be confirmed")
            self._reviewed_artifact(db, run, decision)
            ticket = self._get(db, "tickets", run.ticket_id, Ticket)
            project = self._get(db, "projects", ticket.project_id, Project)
            try:
                self._git(project.repository_path, "merge-base", "--is-ancestor", run.approved_commit, f"refs/heads/{project.base_branch}")
            except Invalid as exc:
                raise Conflict("Approved artifact is not contained in the configured base branch; merge manually first") from exc
            run = run.model_copy(update={"outcome": "done", "phase": "Done", "updated_at": now()})
            self._put(db, "runs", run)
            self._ticket_status(db, run.ticket_id, Status.DONE)
            self._activity(db, run_id, "Human", "merged", decision.message or "Verified approved artifact ancestry after manual merge")
            return run

    def begin_tool(self, run_id, call_id, name, args):
        with self._connection(True) as db:
            self._get(db, "runs", run_id, Run)
            args = self._redact(db, args)
            row = db.execute("SELECT * FROM tools WHERE run_id=? AND call_id=?", (run_id, call_id)).fetchone()
            if row:
                if row["name"] != name or json.loads(row["args"]) != args:
                    raise Conflict("Tool execution ID was reused for different arguments")
                if row["status"] == "completed":
                    return False
                raise Conflict("Previous tool execution did not complete; inspect retained work before continuing")
            db.execute("INSERT INTO tools(run_id,call_id,name,args,status,started_at) VALUES (?,?,?,?,?,?)",
                       (run_id, call_id, name, json.dumps(args), "pending", now()))
            return True

    def finish_tool(self, run_id, call_id, result, success=True):
        with self._connection(True) as db:
            row = db.execute("SELECT status FROM tools WHERE run_id=? AND call_id=?", (run_id, call_id)).fetchone()
            if not row:
                raise NotFound("Tool execution not found")
            if row[0] != "pending":
                raise Conflict("Tool execution already finished")
            db.execute("UPDATE tools SET result=?,status=?,finished_at=? WHERE run_id=? AND call_id=?",
                       (json.dumps(self._redact(db, result)), "completed" if success else "failed", now(), run_id, call_id))

    def tool_result(self, run_id, call_id):
        with self._connection() as db:
            row = db.execute("SELECT result,status FROM tools WHERE run_id=? AND call_id=?", (run_id, call_id)).fetchone()
            if not row:
                raise NotFound("Tool execution not found")
            if row["status"] != "completed":
                raise Conflict("Tool execution did not complete")
            return json.loads(row["result"])

    def pending_tools(self, run_id):
        with self._connection() as db:
            self._get(db, "runs", run_id, Run)
            values = []
            for row in db.execute("SELECT * FROM tools WHERE run_id=? AND status!='completed' ORDER BY started_at", (run_id,)):
                value = dict(row)
                value["args"] = json.loads(value["args"])
                value["result"] = json.loads(value["result"]) if value["result"] else None
                values.append(value)
            return values
