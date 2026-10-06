from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(StrEnum):
    BACKLOG = "Backlog"
    READY = "Ready"
    IN_PROGRESS = "In Progress"
    REVIEW = "Review"
    AWAITING_HUMAN = "Awaiting Human"
    APPROVED = "Approved"
    BLOCKED = "Blocked"
    DONE = "Done"


class ProviderConfig(Model):
    kind: Literal["deterministic", "openai", "anthropic"] = "deterministic"
    base_url: str | None = None
    model: str = "scripted-test-provider"
    credential_env: str | None = None

    @field_validator("credential_env")
    @classmethod
    def env_name(cls, value):
        import re
        if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Use an environment variable name, never a credential value")
        return value

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        from urllib.parse import urlsplit
        if value:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Use an HTTP(S) endpoint without credentials or query parameters")
        return value


class Settings(Model):
    engineer: ProviderConfig = Field(default_factory=ProviderConfig)
    frontier: ProviderConfig = Field(default_factory=ProviderConfig)


class ProjectInput(Model):
    name: str = Field(min_length=1, max_length=200)
    repository_path: str
    base_branch: str = "main"
    test_commands: list[list[str]] = Field(default_factory=list)

    @field_validator("test_commands")
    @classmethod
    def argv_commands(cls, value):
        if any(not cmd or any(not arg or "\0" in arg for arg in cmd) for cmd in value):
            raise ValueError("Commands must be nonempty argument arrays")
        return value


class Project(ProjectInput):
    id: str


class TicketInput(Model):
    project_id: str
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = Field(default=3, ge=1, le=5)
    dependencies: list[str] = Field(default_factory=list)
    status: Status = Status.BACKLOG
    position: float = 0
    linked_ticket_id: str | None = None


class Ticket(TicketInput):
    id: str
    created_at: str
    updated_at: str


class TicketPatch(Model):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    dependencies: list[str] | None = None
    status: Status | None = None
    position: float | None = None


class Run(Model):
    id: str
    ticket_id: str
    phase: str = "Scrum"
    attempts: int = 0
    thread_id: str
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    artifact_commit: str | None = None
    approved_commit: str | None = None
    outcome: Literal["running", "awaiting_human", "approved", "blocked", "done"] = "running"
    tool_calls: int = 0
    interruption_reason: str | None = None
    created_at: str
    updated_at: str


class Activity(Model):
    id: int
    run_id: str
    role: str
    event_type: str
    message: str
    timestamp: str


class ReviewInput(Model):
    artifact_commit: str
    verdict: Literal["approve", "changes", "unavailable"]
    findings: list[str] = Field(default_factory=list)
    acceptance_assessment: dict[str, str] = Field(default_factory=dict)


class Review(ReviewInput):
    id: str
    run_id: str
    created_at: str


class Decision(Model):
    artifact_commit: str
    message: str = ""


class QueueState(Model):
    enabled: bool
    active_run_id: str | None = None


class Artifact(Model):
    run_id: str
    artifact_commit: str | None
    base_commit: str | None
    branch: str | None
    worktree: str | None
    patch: str


class ProviderCheck(Model):
    ok: bool
    mode: str
    chat: bool
    tool_calling: bool
    structured_response: bool
    message: str
