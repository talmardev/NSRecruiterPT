"""Lockfile de instancia unica (impede duas instancias em simultaneo).

O que conta e um lock do sistema operativo sobre o ficheiro, nao o PID la escrito: o
sistema liberta-o sozinho quando o processo morre (crash, corte de energia). Um PID
gravado podia, depois de um reboot, pertencer a outro programa qualquer e impedir o
arranque. O PID fica no ficheiro so para consulta.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO

# No Windows o lock impede a leitura dos bytes trancados: tranca-se um byte longe do
# conteudo para o PID continuar legivel.
_LOCK_OFFSET = 1 << 20


class InstanceAlreadyRunningError(Exception):
    """Ja existe outra instancia a correr, segundo o lockfile."""


def _lock_or_raise(handle: IO[str]) -> None:
    """Lock exclusivo sem esperar. Lanca OSError se outro processo ja o tiver."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class LockFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.fdopen(os.open(self._path, os.O_RDWR | os.O_CREAT), "r+", encoding="utf-8")
        try:
            _lock_or_raise(handle)
        except OSError as exc:
            handle.close()
            raise InstanceAlreadyRunningError(
                "Ja existe outra instancia do NSRecruiter em execucao. Termina-a antes de "
                "iniciar uma nova: as regras da plataforma exigem uma unica instancia por chave."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        # O ficheiro fica no disco de proposito: apaga-lo abria uma corrida com outro
        # processo que o tivesse acabado de abrir. Fechar ja liberta o lock.
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> LockFile:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
