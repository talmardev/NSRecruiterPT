"""Estado partilhado entre o emissor e o dashboard: pausa, paragem limpa e deteção
de bloqueio por uso externo da chave."""
from __future__ import annotations

import asyncio
import time


class RuntimeStatus:
    def __init__(self) -> None:
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.shutdown_requested = asyncio.Event()
        self.currently_dispatching = False
        self._blocked_externally_until = 0.0

    @property
    def paused(self) -> bool:
        return not self.resume_event.is_set()

    def toggle_pause(self) -> None:
        if self.resume_event.is_set():
            self.resume_event.clear()
        else:
            self.resume_event.set()

    def request_shutdown(self) -> None:
        self.shutdown_requested.set()

    def mark_blocked_externally(self, retry_after: float) -> None:
        self._blocked_externally_until = time.monotonic() + retry_after

    def is_blocked_externally(self) -> bool:
        return time.monotonic() < self._blocked_externally_until

    async def wait_while_paused(self) -> None:
        """Bloqueia enquanto pausado, mas liberta assim que sair da pausa OU for pedida paragem."""
        if self.resume_event.is_set():
            return
        resume_wait = asyncio.ensure_future(self.resume_event.wait())
        shutdown_wait = asyncio.ensure_future(self.shutdown_requested.wait())
        try:
            await asyncio.wait({resume_wait, shutdown_wait}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            resume_wait.cancel()
            shutdown_wait.cancel()

    async def sleep_or_shutdown(self, seconds: float) -> None:
        """Espera `seconds`, mas acorda mais cedo se for pedida paragem."""
        try:
            await asyncio.wait_for(self.shutdown_requested.wait(), timeout=seconds)
        except TimeoutError:
            pass
