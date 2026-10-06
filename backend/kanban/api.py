"""Local API over durable records and the asynchronous agent engine."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from .contracts import (
    Activity, Artifact, Decision, Project, ProjectInput, ProviderCheck, QueueState,
    Review, Run, Settings, Ticket, TicketInput, TicketPatch,
    Status,
)
from .storage import Conflict, Invalid, NotFound


class DomainRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except NotFound as exc:
                raise HTTPException(404, str(exc)) from exc
            except Invalid as exc:
                raise HTTPException(422, str(exc)) from exc
            except RequestValidationError as exc:
                # Pydantic normally echoes rejected inputs, which may include a
                # credential accidentally pasted into a settings field.
                detail = [{key: error[key] for key in ("loc", "msg", "type") if key in error}
                          for error in exc.errors()]
                raise HTTPException(422, detail) from exc

        return handler


class Health(BaseModel):
    service: Literal["kanban-agent-board"] = "kanban-agent-board"
    version: Literal["0.1.0"] = "0.1.0"
    mode: Literal["deterministic", "live", "mixed"]


router = APIRouter(prefix="/api", route_class=DomainRoute)


def store(request):
    return request.app.state.store


def wake(request):
    request.app.state.wake.set()


@router.get("/health", response_model=Health)
async def health(request: Request):
    settings = store(request).get_settings()
    deterministic = (settings.engineer.kind == "deterministic", settings.frontier.kind == "deterministic")
    mode = "deterministic" if all(deterministic) else "mixed" if any(deterministic) else "live"
    return Health(mode=mode)


@router.get("/projects", response_model=list[Project])
async def projects(request: Request):
    return store(request).list_projects()


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(value: ProjectInput, request: Request):
    return store(request).create_project(value)


@router.put("/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, value: ProjectInput, request: Request):
    return store(request).update_project(project_id, value)


@router.get("/tickets", response_model=list[Ticket])
async def tickets(request: Request, project_id: str | None = None):
    return store(request).list_tickets(project_id)


@router.post("/tickets", response_model=Ticket, status_code=201)
async def create_ticket(value: TicketInput, request: Request):
    ticket = store(request).create_ticket(value)
    wake(request)
    return ticket


@router.get("/tickets/{ticket_id}", response_model=Ticket)
async def ticket(ticket_id: str, request: Request):
    return store(request).get_ticket(ticket_id)


@router.patch("/tickets/{ticket_id}", response_model=Ticket)
async def patch_ticket(ticket_id: str, value: TicketPatch, request: Request):
    ticket = store(request).patch_ticket(ticket_id, value)
    wake(request)
    return ticket


@router.get("/queue", response_model=QueueState)
async def queue(request: Request):
    return store(request).queue_state()


@router.post("/queue/start", response_model=QueueState)
async def start_queue(request: Request):
    value = store(request).set_queue_enabled(True)
    wake(request)
    return value


@router.post("/queue/pause", response_model=QueueState)
async def pause_queue(request: Request):
    value = store(request).set_queue_enabled(False)
    wake(request)
    return value


@router.get("/runs", response_model=list[Run])
async def runs(request: Request, ticket_id: str | None = None):
    return store(request).list_runs(ticket_id)


@router.get("/runs/{run_id}", response_model=Run)
async def run(run_id: str, request: Request):
    return store(request).get_run(run_id)


async def resume_graph(request, run_id, decision=None):
    try:
        await request.app.state.engine.resume(run_id, decision)
    except Exception as exc:
        current = store(request).get_run(run_id)
        if current.outcome == "running":
            store(request).update_run(run_id, outcome="blocked", interruption_reason="Workflow continuation unavailable; inspect saved decision before resuming")
            store(request).set_ticket_status(current.ticket_id, Status.BLOCKED)
        store(request).add_activity(run_id, "System", "continuation_unavailable",
                                    "The decision was saved, but workflow continuation failed. Inspect the run before resuming.")
        if isinstance(exc, (Conflict, Invalid, NotFound)):
            raise
        raise HTTPException(503, "Decision saved; workflow continuation unavailable") from exc
    finally:
        wake(request)


@router.post("/runs/{run_id}/resume", response_model=Run)
async def resume(run_id: str, request: Request):
    store(request).claim_resume(run_id)
    await resume_graph(request, run_id)
    return store(request).get_run(run_id)


@router.get("/runs/{run_id}/activity", response_model=list[Activity])
async def activity(run_id: str, request: Request, after: int = Query(default=0, ge=0)):
    return store(request).list_activity(run_id, after)


@router.get("/runs/{run_id}/reviews", response_model=list[Review])
async def reviews(run_id: str, request: Request):
    return store(request).list_reviews(run_id)


@router.get("/runs/{run_id}/artifact", response_model=Artifact)
async def artifact(run_id: str, request: Request):
    store(request).get_run(run_id)
    return await request.app.state.engine.inspect_artifact(run_id)


@router.post("/runs/{run_id}/approve", response_model=Run)
async def approve(run_id: str, value: Decision, request: Request):
    store(request).decide(run_id, "approve", value)
    await resume_graph(request, run_id, {"action": "approve", **value.model_dump()})
    return store(request).get_run(run_id)


@router.post("/runs/{run_id}/changes", response_model=Run)
async def changes(run_id: str, value: Decision, request: Request):
    store(request).decide(run_id, "changes", value)
    await resume_graph(request, run_id, {"action": "changes", **value.model_dump()})
    return store(request).get_run(run_id)


@router.post("/runs/{run_id}/confirm-merge", response_model=Run)
async def confirm_merge(run_id: str, value: Decision, request: Request):
    result = store(request).confirm_merge(run_id, value)
    wake(request)
    return result


@router.get("/settings", response_model=Settings)
async def settings(request: Request):
    return store(request).get_settings()


@router.put("/settings", response_model=Settings)
async def save_settings(value: Settings, request: Request):
    return store(request).save_settings(value)


@router.post("/providers/{role}/check", response_model=ProviderCheck)
async def check_provider(role: Literal["engineer", "frontier"], request: Request):
    return await request.app.state.engine.check_provider(role)
