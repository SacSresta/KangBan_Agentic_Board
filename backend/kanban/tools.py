"""Worktree-scoped repository operations with a durable execution journal."""
from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from typing import Any

from .storage import NotFound
from .contracts import TicketInput

MAX_TOOL_CALLS = 20
TIMEOUT = 120
MAX_TEXT = 512_000


class ToolError(RuntimeError):
    pass


def sensitive(path: Path) -> bool:
    for part in path.parts:
        low = part.lower()
        if low in {".git", ".ssh", ".aws", ".codex", ".agents", ".npmrc", ".netrc", "credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}:
            return True
        if low == ".env" or low.startswith(".env.") or low.endswith((".pem", ".key", ".p12", ".pfx")):
            return True
    return False


def scoped_path(root: Path, name: str) -> Path:
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts or sensitive(relative):
        raise ToolError("Path is outside the worktree or is excluded")
    if root.resolve(strict=True) != root.absolute():
        raise ToolError("Worktree root may not be replaced with a symlink")
    root = root.resolve(strict=True)
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root)
    except (ValueError, RuntimeError):
        raise ToolError("Symlink escapes the worktree") from None
    # Reject all symlinks, including links to other worktree files, to keep writes predictable.
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ToolError("Agent file tools do not follow symlinks")
    return candidate


def redact(value: str, store=None) -> str:
    if store:
        settings = store.get_settings()
        for config in (settings.engineer, settings.frontier):
            if config.credential_env:
                secret = os.environ.get(config.credential_env)
                if secret:
                    value = value.replace(secret, "[REDACTED]")
    return value[:MAX_TEXT]


def sanitize(value, store):
    if isinstance(value, str):
        return redact(value, store)
    if isinstance(value, dict):
        return {redact(key, store) if isinstance(key, str) else key: sanitize(item, store) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, store) for item in value]
    return value


async def subprocess_result(argv: list[str], cwd: Path, timeout: int = TIMEOUT) -> dict:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        truncated = len(stdout) > MAX_TEXT or len(stderr) > MAX_TEXT
        return {"command": argv, "returncode": process.returncode,
                "stdout": stdout[:MAX_TEXT].decode(errors="replace"),
                "stderr": stderr[:MAX_TEXT].decode(errors="replace"), "timed_out": False,
                "output_truncated": truncated,
                "output_note": "Captured output exceeded 512 KB and was truncated." if truncated else "Complete captured output."}
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
                if process.returncode is None:
                    process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, FileNotFoundError):
            if process.returncode is None:
                process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return {"command": argv, "returncode": -1, "stdout": "", "stderr": f"Command exceeded {timeout} seconds", "timed_out": True, "output_truncated": False, "output_note": "Process was terminated at the timeout."}


async def git(cwd: Path, *arguments: str) -> str:
    result = await subprocess_result(["git", "-c", f"core.hooksPath={os.devnull}", *arguments], cwd)
    if result["returncode"] != 0:
        # Do not expose arbitrary Git hook or credential-helper output.
        raise ToolError("Git operation failed: " + arguments[0])
    if result["output_truncated"]:
        raise ToolError("Git output exceeds the 512 KB review limit; complete metadata or patch is required")
    return result["stdout"].rstrip("\n")


class RepositoryTools:
    def __init__(self, store, data_dir: Path, run_id: str):
        self.store, self.data_dir, self.run_id = store, data_dir, run_id

    @property
    def run(self):
        return self.store.get_run(self.run_id)

    @property
    def project(self):
        return self.store.get_project(self.store.get_ticket(self.run.ticket_id).project_id)

    @property
    def root(self) -> Path:
        if not self.run.worktree:
            raise ToolError("No dedicated worktree is available")
        return Path(self.run.worktree)

    async def call(self, name: str, args: dict, call_id: str | None = None, role: str = "Engineer") -> Any:
        run = self.run
        if call_id:
            try:
                previous = self.store.tool_result(run.id, call_id)
            except NotFound:
                pass
            else:
                # Validate tool name and exact arguments even for completed retries.
                self.store.begin_tool(run.id, call_id, name, sanitize(args, self.store))
                return previous
        if run.tool_calls >= MAX_TOOL_CALLS:
            raise ToolError("Repository tool budget of 20 calls exhausted")
        call_id = call_id or f"{run.id}:{run.tool_calls + 1}"
        # Increment before journaling/mutation; interrupted tools never reuse an ID.
        self.store.update_run(run.id, tool_calls=run.tool_calls + 1)
        if not self.store.begin_tool(run.id, call_id, name, sanitize(args, self.store)):
            return self.store.tool_result(run.id, call_id)
        self.store.add_activity(run.id, role, "tool_start", f"{name}: {redact(str(args), self.store)}")
        try:
            method = getattr(self, "_" + name, None)
            if method is None:
                raise ToolError("Unknown repository tool")
            result = await method(**args)
            result = sanitize(result, self.store)
            self.store.finish_tool(run.id, call_id, result, True)
            self.store.add_activity(run.id, role, "test_output" if name == "run_test" else "tool_result", redact(str(result), self.store))
            return result
        except asyncio.CancelledError:
            # Retain pending status: recovery demands explicit user inspection.
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, ToolError) else f"Tool failed ({type(exc).__name__})"
            message = redact(message, self.store)
            self.store.finish_tool(run.id, call_id, {"error": message}, False)
            self.store.add_activity(run.id, role, "tool_error", message)
            raise ToolError(message) from None

    async def _create_worktree(self) -> dict:
        run = self.run
        if run.worktree:
            await git(self.root, "rev-parse", "HEAD")
            return {"worktree": run.worktree, "branch": run.branch, "base_commit": run.base_commit}
        repository = Path(self.project.repository_path).resolve(strict=True)
        base = await git(repository, "rev-parse", "--verify", f"refs/heads/{self.project.base_branch}^{{commit}}")
        branch = "kanban/" + run.id
        destination = (self.data_dir / "worktrees" / run.id).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Persist intent before the Git mutation so interrupted creation is inspectable.
        self.store.update_run(run.id, worktree=str(destination), branch=branch, base_commit=base)
        await git(repository, "worktree", "add", "-b", branch, str(destination), base)
        return {"worktree": str(destination), "branch": branch, "base_commit": base}

    async def _read(self, path: str) -> str:
        target = scoped_path(self.root, path)
        if not target.is_file() or target.stat().st_size > MAX_TEXT:
            raise ToolError("Read requires a regular text file under 512 KB")
        return target.read_text(encoding="utf-8")

    async def _search(self, pattern: str, path: str = ".") -> str:
        folder = scoped_path(self.root, path)
        matcher = re.compile(pattern)
        hits = []
        files = [folder] if folder.is_file() else folder.rglob("*")
        for file in files:
            if len(hits) >= 100:
                break
            relative = file.relative_to(self.root)
            if sensitive(relative) or file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_TEXT:
                continue
            try:
                text = scoped_path(self.root, str(relative)).read_text(encoding="utf-8")
            except (UnicodeError, ToolError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if matcher.search(line):
                    hits.append(f"{relative}:{number}: {line[:300]}")
                    if len(hits) >= 100:
                        break
        return "\n".join(hits)

    async def _write(self, path: str, content: str) -> str:
        target = scoped_path(self.root, path)
        if len(content.encode()) > MAX_TEXT:
            raise ToolError("Write content exceeds 512 KB")
        if redact(content, self.store) != content:
            raise ToolError("Agent writes may not contain configured model credentials")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after creating directories and before writing.
        scoped_path(self.root, path).write_text(content, encoding="utf-8")
        return f"Wrote {path}"

    async def _run_test(self, command: list[str]) -> dict:
        if command not in self.project.test_commands:
            raise ToolError("Test command is not in the project's approved argument arrays")
        return await subprocess_result(command, self.root)

    async def _suggest_ticket(self, title: str, description: str) -> dict:
        ticket = self.store.get_ticket(self.run.ticket_id)
        title, description = redact(title, self.store), redact(description, self.store)
        existing = [item for item in self.store.list_tickets(ticket.project_id) if item.linked_ticket_id == ticket.id and item.title == title]
        linked = existing[0] if existing else self.store.create_ticket(TicketInput(project_id=ticket.project_id, title=title, description=description, linked_ticket_id=ticket.id))
        self.store.add_activity(self.run.id, "Engineer", "discovered_ticket", f"Linked unrelated issue {linked.id}: {linked.title}")
        return {"ticket_id": linked.id}

    async def _artifact(self) -> dict:
        # Stage only safe changed paths; never stage excluded files or symlinks.
        raw = await git(self.root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        paths = []
        items = raw.split("\0")
        index = 0
        while index < len(items):
            item = items[index]
            index += 1
            if len(item) < 4:
                continue
            status, path = item[:2], item[3:]
            if "R" in status or "C" in status:
                if index < len(items):
                    paths.append(items[index])
                    index += 1
            paths.append(path)
        for path in paths:
            scoped_path(self.root, path)
        if paths:
            await git(self.root, "add", "--all", "--", *paths)
        # Reject unsafe pre-staged files as well as changes discovered above.
        staged = await git(self.root, "diff", "--cached", "--name-only", "-z")
        for path in staged.split("\0"):
            if path:
                scoped_path(self.root, path)
        staged_patch = await git(self.root, "diff", "--cached")
        if redact(staged_patch, self.store) != staged_patch:
            raise ToolError("Artifact contains configured model credentials")
        await git(self.root, "-c", "user.name=Kanban Agent", "-c", "user.email=kanban@localhost", "commit", "--allow-empty", "-m", f"Kanban artifact {self.run.id} attempt {self.run.attempts}")
        commit = await git(self.root, "rev-parse", "HEAD")
        self.store.update_run(self.run.id, artifact_commit=commit, approved_commit=None)
        return {"artifact_commit": commit}

    async def patch(self, commit: str | None = None) -> str:
        if not self.run.worktree or not self.run.base_commit:
            return ""
        names = await git(self.root, "diff", "--name-only", "-z", self.run.base_commit, commit or "HEAD")
        paths = [name for name in names.split("\0") if name and not sensitive(Path(name))]
        if not paths:
            return ""
        return redact(await git(self.root, "diff", self.run.base_commit, commit or "HEAD", "--", *paths), self.store)
