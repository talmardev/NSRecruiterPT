"""Configuracao do sistema de registo (logging), com mascaramento de segredos.

Duas variantes de saida no ecra: consola simples (usada por --check, onde nao ha
nenhum ecra a proteger) ou memoria intermedia para o dashboard Rich (usada pela
execucao normal, onde escrever diretamente na consola corromperia o Live). O
ficheiro de log e sempre igual: fica tudo registado, mesmo o que o dashboard nao
tem espaco para mostrar.
"""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_NOISY_THIRD_PARTY_LOGGERS = ("httpx", "httpcore")
LOG_BUFFER_SIZE = 15


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str


class SecretMaskingFilter(logging.Filter):
    """Substitui qualquer segredo em bruto pela sua versao mascarada, mesmo vindo de terceiros."""

    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._replacements = {raw: masked for raw, masked in secrets.items() if raw}

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for raw, masked in self._replacements.items():
            if raw in message:
                message = message.replace(raw, masked)
        record.msg = message
        record.args = ()
        return True


class DashboardLogHandler(logging.Handler):
    """Em vez de escrever na consola, guarda as ultimas entradas para o painel de log."""

    def __init__(self, buffer: "deque[LogEntry]") -> None:
        super().__init__()
        self._buffer = buffer
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(
            LogEntry(
                timestamp=datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                level=record.levelname,
                message=self.format(record),
            )
        )


def new_log_buffer() -> "deque[LogEntry]":
    return deque(maxlen=LOG_BUFFER_SIZE)


def _install_common_handlers(root: logging.Logger, log_dir: Path, masking_filter: SecretMaskingFilter) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "nsrecruiter.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.addFilter(masking_filter)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(file_handler)

    # httpx/httpcore conseguem imprimir o URL completo do pedido (client=/key= no sendTG).
    for logger_name in _NOISY_THIRD_PARTY_LOGGERS:
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.setLevel(logging.WARNING)
        third_party_logger.addFilter(masking_filter)


def setup_logging(log_dir: Path, level: str, secrets: dict[str, str]) -> None:
    """Modo simples: consola + ficheiro. Usado por --check e por --setup isolado."""
    masking_filter = SecretMaskingFilter(secrets)
    root = logging.getLogger("nsrecruiter")
    root.setLevel(level)
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.addFilter(masking_filter)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S"))
    root.addHandler(console_handler)

    _install_common_handlers(root, log_dir, masking_filter)


def setup_logging_for_dashboard(
    log_dir: Path, level: str, secrets: dict[str, str], buffer: "deque[LogEntry]"
) -> None:
    """Modo dashboard: nada vai diretamente para a consola -- o Live tem de ser o unico a
    desenhar o ecra. As mesmas mensagens continuam todas a ir para o ficheiro de log."""
    masking_filter = SecretMaskingFilter(secrets)
    root = logging.getLogger("nsrecruiter")
    root.setLevel(level)
    root.handlers.clear()

    dashboard_handler = DashboardLogHandler(buffer)
    dashboard_handler.addFilter(masking_filter)
    root.addHandler(dashboard_handler)

    _install_common_handlers(root, log_dir, masking_filter)
