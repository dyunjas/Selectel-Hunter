"""UI observations and notifications, inside the existing queue transaction."""
import json
from sqlalchemy import select
from .models import JobUiState, NotificationOutbox


async def ui_state(session, job):
    state = await session.get(JobUiState, job.id)
    if state is None:
        state = JobUiState(job_id=job.id, active_seconds=0, timing_complete=False, event_sequence=0)
        session.add(state)
        await session.flush()
    return state


async def event(session, job, state, kind, at):
    state.event_sequence += 1
    payload = json.dumps({"event": kind, "sequence": state.event_sequence, "at": at.isoformat()})
    row = await session.scalar(select(NotificationOutbox).where(NotificationOutbox.job_id == job.id, NotificationOutbox.event_type == kind))
    if row is None:
        session.add(NotificationOutbox(job_id=job.id, event_type=kind, payload=payload, due_at=at))
    else:
        row.payload, row.delivered_at, row.due_at, row.attempts = payload, None, at, 0


def stop_clock(state, at):
    if state.active_since:
        state.active_seconds += max(0, (at - state.active_since).total_seconds())
        state.active_since = None


async def transition(session, job, previous, at, new=False):
    state = await ui_state(session, job)
    if new:
        state.timing_complete, state.active_since = True, at
    if previous == job.status and not new:
        return
    if job.status in {"paused", "cancelled", "failed", "completed"}:
        stop_clock(state, at)
    if job.status in {"paused", "cancelled"}:
        state.paused_at, state.pause_reason = at, "user"
    if job.status == "pending":
        state.active_since = state.active_since or at
        state.paused_at = state.pause_reason = None
    kinds = {"paused": "PAUSED", "cancelled": "STOPPED", "pending": "STARTED" if new else "RESUMED"}
    if job.status in kinds:
        await event(session, job, state, kinds[job.status], at)


async def problem(session, job, error, at):
    state = await ui_state(session, job)
    kind = error.split(":", 1)[0]
    if kind == "NO_FREE_IP":
        await healthy(session, job, at, operation_success=False)
        return
    # One message per continuous outage, even across process restarts.
    if state.problem_type is None or (kind in {"AUTH_ERROR", "PERMISSION_ERROR", "QUOTA_EXCEEDED"} and state.problem_type != kind):
        await event(session, job, state, kind[:80], at)
    state.problem_type = kind[:80]


async def healthy(session, job, at, operation_success=True):
    state = await ui_state(session, job)
    if operation_success:
        state.last_success_at = at
    if state.problem_type:
        state.problem_type = None
        await event(session, job, state, "RECOVERED", at)
