"""A2A tasks over the capability registry: one task per skill call, run by a worker pool.

A task is ``TASK_STATE_SUBMITTED`` while its call waits for a worker, ``WORKING`` while it runs,
then ``COMPLETED`` (one artifact: a cited summary, the result data and its grounded facts),
``FAILED`` (``timeout``, ``unavailable``, ``internal``, ``too_large``, ``not_found``),
``REJECTED`` (refused before it ran: a facility outside the allowlist, research-only data,
invalid input, or every worker busy) or ``CANCELED``. A failed, rejected or cancelled task's
status message carries the error code and notices in the same ``grounded-facts`` data part.

The runner is :class:`camber.mcp.WorkerPool` under ``camber a2a`` (killable worker processes,
time caps, a bounded queue); :class:`LocalRunner` runs calls on threads in this process, for
library use and tests.
"""

from __future__ import annotations

import collections
import dataclasses
import logging
import threading
import time
from typing import Any

from ..capabilities._grounding import error_info, grounding
from . import _protocol as p

log = logging.getLogger("camber.a2a")

#: finished tasks kept for GetTask / ListTasks; the oldest finished ones go first
MAX_TASKS = 256
#: error codes of a call refused before it ran: the task is REJECTED, not FAILED
REJECTED_CODES = frozenset(
    {"invalid_params", "unknown_capability", "not_allowed", "refused_licence", "busy"}
)
_ERROR_TEXT = "the skill failed"


@dataclasses.dataclass
class SkillCall:
    """One registry call, as a :class:`camber.mcp.WorkerPool` takes it."""

    name: str
    arguments: dict
    context: Any


def _part(kind: str, value: Any, role: str) -> dict:
    media = "text/plain" if kind == "text" else "application/json"
    return {kind: value, "mediaType": media, "metadata": {p.PART_KEY: role}}


def grounded_part(facts: Any, notices: Any, error: dict | None = None) -> dict:
    """The ``grounded-facts`` data part: :func:`camber.capabilities._grounding.grounding` plus
    the full fact records (kind, value, equipment, ...) under ``records``."""
    body = grounding(facts, notices, error=error)
    body["records"] = [f for f in facts or () if isinstance(f, dict)]
    return _part("data", body, "grounded-facts")


def _json_safe(value: Any) -> Any:
    from ..mcp._server import json_safe

    return json_safe(value)


class Task:
    """One task. Its fields change under :attr:`TaskStore.lock` only."""

    def __init__(self, skill: p.Skill, arguments: dict, message: dict, context_id: str):
        self.id = p.new_id()
        self.context_id = context_id
        self.skill = skill
        self.arguments = arguments
        self.created = time.monotonic()
        self.state = p.SUBMITTED
        self.timestamp = p.now()
        self.status_message: dict | None = None
        self.artifacts: list = []
        self.job: Any = None
        self.done = threading.Event()
        # the peer's message, with the ids this server gave it
        self.history = [{**message, "taskId": self.id, "contextId": context_id}]

    def current_state(self) -> str:
        if self.state == p.SUBMITTED and getattr(self.job, "state", None) == "running":
            return p.WORKING
        return self.state

    def finish(self, state: str, *, artifact: dict | None = None, error: dict | None = None):
        """Move to the terminal ``state`` (once; a later call is ignored)."""
        if self.state in p.TERMINAL_STATES:
            return False
        self.state = state
        self.timestamp = p.now()
        if artifact is not None:
            self.artifacts = [artifact]
        if error is not None:
            self.status_message = self._agent_message(error)
        self.done.set()
        return True

    def _agent_message(self, error: dict) -> dict:
        word = {p.REJECTED: "rejected", p.CANCELED: "cancelled"}.get(self.state, "failed")
        text = f"{word}: {error['code']}: {error['message']}"
        return {
            "messageId": p.new_id(),
            "contextId": self.context_id,
            "taskId": self.id,
            "role": p.ROLE_AGENT,
            "parts": [_part("text", text, "summary"), grounded_part((), (), error)],
            "extensions": [p.GROUNDED_FACTS_URI],
        }

    def to_dict(self, *, history_length: int | None = None, artifacts: bool = True) -> dict:
        status: dict = {"state": self.current_state(), "timestamp": self.timestamp}
        if self.status_message is not None:
            status["message"] = self.status_message
        out: dict = {"id": self.id, "contextId": self.context_id, "status": status}
        if artifacts and self.artifacts:
            out["artifacts"] = list(self.artifacts)
        if history_length is None:
            history = list(self.history)
        else:
            history = self.history[-history_length:] if history_length else []
        if history:
            out["history"] = history
        out["metadata"] = {
            "camber/skill": self.skill.id,
            "camber/capability": self.skill.capability,
        }
        return out


def artifact_for(skill: p.Skill, result: Any, version: str) -> dict:
    """The artifact of a completed task: the summary (``NOTICE:`` lines first, as ``camber mcp``
    writes them), the result data, and the grounded facts."""
    banners = [str(b) for b in (result.banners or ())]
    summary = str(result.text or "")
    if banners:
        notices = "\n".join(f"NOTICE: {b}" for b in banners)
        summary = f"{notices}\n\n{summary}" if summary else notices
    facts = _json_safe(list(result.facts or []))
    return {
        "artifactId": p.new_id(),
        "name": skill.id,
        "description": f"{skill.name}: the result of capability {skill.capability}",
        "parts": [
            _part("text", summary, "summary"),
            _part("data", _json_safe(result.data), "result"),
            grounded_part(facts, banners),
        ],
        "metadata": {
            "camber/version": str(version),
            "camber/skill": skill.id,
            "camber/capability": skill.capability,
            "camber/truncated": bool(result.truncated),
        },
        "extensions": [p.GROUNDED_FACTS_URI],
    }


def error_of(outcome: BaseException) -> dict:
    """``{"code", "message", "data"?}`` of a capability error (recognised by shape), else an
    ``internal`` error that says nothing of the exception."""
    code, message = getattr(outcome, "code", None), getattr(outcome, "message", None)
    if isinstance(code, str) and isinstance(message, str):
        return error_info(code, message, getattr(outcome, "data", None))
    return error_info("internal", _ERROR_TEXT)


class TaskStore:
    """The tasks of one server, newest last; at most :data:`MAX_TASKS` finished ones are kept."""

    def __init__(self, runner: Any, version: str, *, max_tasks: int = MAX_TASKS):
        self.runner = runner
        self.version = version
        self.max_tasks = int(max_tasks)
        self.lock = threading.Lock()
        self._tasks: collections.OrderedDict = collections.OrderedDict()

    # ------------------------------------------------------------------ lookups

    def get(self, task_id: Any) -> Task:
        with self.lock:
            task = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            raise p.RpcError(p.TASK_NOT_FOUND, "Task not found")
        return task

    def snapshot(self, task: Task, **kw) -> dict:
        with self.lock:
            return task.to_dict(**kw)

    def all(self) -> list:
        with self.lock:
            return list(self._tasks.values())

    # ------------------------------------------------------------------ changes

    def _add(self, task: Task) -> None:
        with self.lock:
            self._tasks[task.id] = task
            finished = [t.id for t in self._tasks.values() if t.state in p.TERMINAL_STATES]
            for tid in finished[: max(0, len(self._tasks) - self.max_tasks)]:
                del self._tasks[tid]

    def reject(self, task: Task, error: dict) -> Task:
        """Add ``task`` already refused (it never runs)."""
        task.finish(p.REJECTED, error=dict(error))
        self._add(task)
        log.info("task %s (%s): rejected, %s", task.id, task.skill.id, error["code"])
        return task

    def start(self, task: Task, context: Any) -> Task:
        """Add ``task`` and hand its call to the runner; rejected as ``busy`` when it is full."""
        self._add(task)
        call = SkillCall(task.skill.capability, task.arguments, context)
        with self.lock:
            job = self.runner.submit(call, lambda job: self._done(task, job))
            if job is None:
                busy = self.runner.busy_error()
                task.finish(p.REJECTED, error=error_of(busy))
            else:
                task.job = job
        if job is None:
            log.info("task %s (%s): rejected, busy", task.id, task.skill.id)
        return task

    def _done(self, task: Task, job: Any) -> None:
        outcome = job.outcome
        took = time.monotonic() - task.created
        with self.lock:
            if isinstance(outcome, BaseException):
                err = error_of(outcome)
                state = p.REJECTED if err["code"] in REJECTED_CODES else p.FAILED
                task.finish(state, error=err)
                what = f"{'rejected' if state == p.REJECTED else 'failed'}, {err['code']}"
            else:
                try:
                    artifact = artifact_for(task.skill, outcome, self.version)
                except Exception:  # noqa: BLE001 - a result that is no JSON: internal
                    log.exception("task %s: its result could not be sent", task.id)
                    task.finish(p.FAILED, error=error_info("internal", _ERROR_TEXT))
                    what = "failed, internal"
                else:
                    task.finish(p.COMPLETED, artifact=artifact)
                    what = "completed"
        log.info("task %s (%s): %s in %.2fs", task.id, task.skill.id, what, took)

    def cancel(self, task: Task) -> None:
        """Cancel ``task``; ``TaskNotCancelableError`` once it has finished."""
        with self.lock:
            if task.state in p.TERMINAL_STATES:
                raise p.RpcError(p.TASK_NOT_CANCELABLE, "Task cannot be canceled: it has finished")
            job = task.job
        stopped = job is not None and self.runner.cancel(job)
        with self.lock:
            if stopped or job is None:
                task.finish(
                    p.CANCELED,
                    error=error_info("cancelled", "the task was cancelled at the caller's request"),
                )
                log.info("task %s (%s): cancelled", task.id, task.skill.id)
                return
        task.done.wait(5.0)  # its outcome is being delivered right now
        with self.lock:
            if task.state in p.TERMINAL_STATES:
                raise p.RpcError(p.TASK_NOT_CANCELABLE, "Task cannot be canceled: it has finished")

    def cancel_all(self) -> int:
        """Mark every unfinished task cancelled (the server is stopping); how many there were."""
        n = 0
        with self.lock:
            for task in self._tasks.values():
                if task.finish(
                    p.CANCELED, error=error_info("cancelled", "the server stopped before it ended")
                ):
                    n += 1
        return n


class _LocalJob:
    def __init__(self, call: SkillCall, on_done):
        self.call, self.on_done = call, on_done
        self.state = "queued"
        self.outcome: Any = None


class LocalRunner:
    """Run each call on a thread of this process (no worker processes): the
    :class:`camber.mcp.WorkerPool` interface (``submit``, ``cancel``, ``busy_error``,
    ``close``). A cancelled call cannot be stopped, only forgotten: its outcome is dropped."""

    def __init__(self, registry: Any, *, max_running: int = 4):
        self.registry = registry
        self.max_running = int(max_running)
        self._lock = threading.Lock()
        self._running = 0

    def submit(self, call: SkillCall, on_done) -> _LocalJob | None:
        with self._lock:
            if self._running >= self.max_running:
                return None
            self._running += 1
            job = _LocalJob(call, on_done)
        threading.Thread(target=self._run, args=(job,), daemon=True, name="a2a-local").start()
        return job

    def _run(self, job: _LocalJob) -> None:
        with self._lock:
            if job.state == "cancelled":
                self._running -= 1
                return
            job.state = "running"
        try:
            outcome: Any = self.registry.call(job.call.name, job.call.arguments, job.call.context)
        except Exception as exc:  # noqa: BLE001 - carried to the task
            outcome = exc
        with self._lock:
            self._running -= 1
            deliver = job.state == "running"
            if deliver:
                job.state, job.outcome = "done", outcome
        if deliver:
            job.on_done(job)

    def cancel(self, job: _LocalJob) -> bool:
        with self._lock:
            if job.state in ("queued", "running"):
                job.state = "cancelled"
                return True
            return False

    def busy_error(self) -> Exception:
        from ..mcp._workers import RemoteError

        return RemoteError(
            "busy",
            f"the server is already running {self.max_running} task(s); retry shortly",
            {"retryable": True},
        )

    def close(self, timeout: float = 0.0) -> int:
        return 0
