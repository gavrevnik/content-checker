from __future__ import annotations

import threading
import time
from typing import Any


_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 100
_MAX_AGE_SECONDS = 60 * 60


def _job_id(value: object) -> str:
    return str(value or "").strip()[:128]


def _prune() -> None:
    cutoff = time.time() - _MAX_AGE_SECONDS
    stale = [job_id for job_id, state in _jobs.items() if state["updated_at"] < cutoff]
    for job_id in stale:
        _jobs.pop(job_id, None)
    if len(_jobs) > _MAX_JOBS:
        oldest = sorted(_jobs, key=lambda job_id: _jobs[job_id]["updated_at"])
        for job_id in oldest[:len(_jobs) - _MAX_JOBS]:
            _jobs.pop(job_id, None)


def _stage(stage_id: str, label: str, total: int, unit: str) -> dict[str, Any]:
    return {
        "id": stage_id, "label": label, "processed": 0,
        "total": max(0, int(total)), "unit": unit, "complete": False,
    }


def start(
    job_id: object,
    total: int,
    *,
    stage_id: str = "people",
    label: str = "Персоны",
    unit: str = "персон",
) -> None:
    key = _job_id(job_id)
    if not key:
        return
    with _lock:
        _prune()
        previous = _jobs.get(key, {})
        cancel_requested = bool(previous.get("cancel_requested") and not previous.get("complete"))
        initial = _stage(stage_id, label, total, unit)
        _jobs[key] = {
            "found": True, "processed": 0, "total": initial["total"],
            "complete": False, "active_stage": stage_id,
            "stages": {stage_id: initial}, "warnings": [],
            "cancel_requested": cancel_requested, "cancelled": False,
            "updated_at": time.time(),
        }


def set_stage(job_id: object, stage_id: str, label: str, total: int, unit: str) -> None:
    key = _job_id(job_id)
    if not key or not stage_id:
        return
    with _lock:
        state = _jobs.get(key)
        if not state:
            return
        state["stages"][stage_id] = _stage(stage_id, label, total, unit)
        state["active_stage"] = stage_id
        state["processed"] = 0
        state["total"] = max(0, int(total))
        state["updated_at"] = time.time()


def advance(job_id: object, stage_id: str | None = None, amount: int = 1) -> None:
    key = _job_id(job_id)
    if not key:
        return
    with _lock:
        state = _jobs.get(key)
        if not state:
            return
        target_id = stage_id or state["active_stage"]
        stage = state["stages"].get(target_id)
        if not stage:
            return
        stage["processed"] = min(stage["total"], stage["processed"] + max(0, int(amount)))
        if stage["processed"] >= stage["total"]:
            stage["complete"] = True
        if target_id == state["active_stage"]:
            state["processed"] = stage["processed"]
            state["total"] = stage["total"]
        state["updated_at"] = time.time()


def finish_stage(job_id: object, stage_id: str) -> None:
    key = _job_id(job_id)
    if not key:
        return
    with _lock:
        state = _jobs.get(key)
        if not state or stage_id not in state["stages"]:
            return
        if state.get("cancel_requested"):
            state["updated_at"] = time.time()
            return
        stage = state["stages"][stage_id]
        stage["processed"] = stage["total"]
        stage["complete"] = True
        if stage_id == state["active_stage"]:
            state["processed"] = stage["processed"]
            state["total"] = stage["total"]
        state["updated_at"] = time.time()


def add_warning(job_id: object, provider: str, message: str) -> None:
    key = _job_id(job_id)
    warning = {"provider": str(provider or "api"), "message": str(message or "").strip()}
    if not key or not warning["message"]:
        return
    with _lock:
        state = _jobs.get(key)
        if not state:
            return
        if warning not in state["warnings"]:
            state["warnings"].append(warning)
        state["updated_at"] = time.time()


def finish(job_id: object) -> None:
    key = _job_id(job_id)
    if not key:
        return
    with _lock:
        state = _jobs.get(key)
        if not state:
            return
        cancelled = bool(state.get("cancel_requested"))
        if not cancelled:
            for stage in state["stages"].values():
                stage["processed"] = stage["total"]
                stage["complete"] = True
        active = state["stages"].get(state["active_stage"])
        if active:
            state["processed"] = active["processed"]
            state["total"] = active["total"]
        state["complete"] = True
        state["cancelled"] = cancelled
        state["updated_at"] = time.time()


def cancel(job_id: object) -> bool:
    key = _job_id(job_id)
    if not key:
        return False
    with _lock:
        state = _jobs.get(key)
        if not state:
            initial = _stage("pending", "Подготовка", 1, "шагов")
            state = {
                "found": True, "processed": 0, "total": 1, "complete": False,
                "active_stage": "pending", "stages": {"pending": initial}, "warnings": [],
                "cancelled": False, "updated_at": time.time(),
            }
            _jobs[key] = state
        state["cancel_requested"] = True
        state["updated_at"] = time.time()
    return True


def is_cancelled(job_id: object) -> bool:
    key = _job_id(job_id)
    if not key:
        return False
    with _lock:
        return bool(_jobs.get(key, {}).get("cancel_requested"))


def get(job_id: object) -> dict[str, Any]:
    key = _job_id(job_id)
    with _lock:
        state = _jobs.get(key)
        if not state:
            return {
                "found": False, "processed": 0, "total": 0, "complete": False,
                "stages": [], "warnings": [], "cancel_requested": False, "cancelled": False,
            }
        return {
            **{field: state[field] for field in ("found", "processed", "total", "complete")},
            "stages": [dict(stage) for stage in state["stages"].values()],
            "warnings": [dict(warning) for warning in state["warnings"]],
            "cancel_requested": bool(state.get("cancel_requested")),
            "cancelled": bool(state.get("cancelled")),
        }
