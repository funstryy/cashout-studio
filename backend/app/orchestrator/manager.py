from __future__ import annotations

import asyncio
import logging

from ..config import CONCURRENT_ENGINES, MODELS
from .process import ManagedProcess
from .state import ModelRuntimeState, ModelStatus, OrchestratorState

logger = logging.getLogger("orchestrator")


class OrchestratorManager:
    """Owns the on/off state of both models.

    A single asyncio.Lock serializes switch_to()/stop_*() calls so that rapid
    clicks in the UI can't start the same model twice or interleave a stop
    with a start. Whether starting one engine stops the other depends on
    CONCURRENT_ENGINES: they only need to take turns when they compete for
    the same device.
    """

    def __init__(self) -> None:
        self.state = OrchestratorState(models={mid: ModelRuntimeState(mid) for mid in MODELS})
        self._processes: dict[str, list[ManagedProcess]] = {}
        self._lock = asyncio.Lock()

    def status_snapshot(self) -> dict:
        return {
            "active_model": self.state.active_model,
            "models": {
                mid: {
                    "id": mid,
                    "label": MODELS[mid].label,
                    "status": rs.status.value,
                    "error": rs.error_message,
                }
                for mid, rs in self.state.models.items()
            },
        }

    async def switch_to(self, model_id: str) -> None:
        if model_id not in MODELS:
            raise ValueError(f"unknown model '{model_id}'")
        async with self._lock:
            if self.state.models[model_id].status == ModelStatus.RUNNING:
                # Already up - selecting it just makes it the one the UI
                # treats as current.
                self.state.active_model = model_id
                return
            if not CONCURRENT_ENGINES:
                for other_id, rs in self.state.models.items():
                    if other_id != model_id and rs.status == ModelStatus.RUNNING:
                        await self._stop_model(other_id)
            await self._start_model(model_id)

    async def stop_one(self, model_id: str) -> None:
        if model_id not in MODELS:
            raise ValueError(f"unknown model '{model_id}'")
        async with self._lock:
            if self.state.models[model_id].status != ModelStatus.STOPPED:
                await self._stop_model(model_id)

    async def stop_all(self) -> None:
        """Every running engine, for shutdown.

        stop_active() only ever stopped one, which was the whole story when
        one was the maximum. With both able to run, using it at shutdown
        would leave the other engine's process tree orphaned.
        """
        async with self._lock:
            for model_id, rs in list(self.state.models.items()):
                if rs.status in (ModelStatus.RUNNING, ModelStatus.STARTING):
                    await self._stop_model(model_id)

    async def _start_model(self, model_id: str) -> None:
        definition = MODELS[model_id]
        rs = self.state.models[model_id]
        rs.status = ModelStatus.STARTING
        rs.error_message = None
        started: list[ManagedProcess] = []
        try:
            for spec in definition.processes:
                proc = ManagedProcess(spec)
                proc.start()
                started.append(proc)
                await proc.wait_healthy()
            self._processes[model_id] = started
            rs.status = ModelStatus.RUNNING
            self.state.active_model = model_id
        except Exception as exc:  # noqa: BLE001 - any startup failure must surface to the UI
            logger.exception("failed to start model %s", model_id)
            for proc in reversed(started):
                await proc.stop()
            rs.status = ModelStatus.ERROR
            rs.error_message = str(exc)
            raise

    async def _stop_model(self, model_id: str) -> None:
        rs = self.state.models[model_id]
        rs.status = ModelStatus.STOPPING
        procs = self._processes.pop(model_id, [])
        # Reverse start order: the dependent process (e.g. YuE2's web UI)
        # stops before the process it depends on (the inference server).
        for proc in reversed(procs):
            await proc.stop()
        rs.status = ModelStatus.STOPPED
        rs.error_message = None
        if self.state.active_model == model_id:
            self.state.active_model = None


manager = OrchestratorManager()
