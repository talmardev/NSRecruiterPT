"""Backup periodico: copia a base de dados para uma pasta externa escolhida no setup.

Usa a API de backup online do SQLite (Connection.backup), nao uma copia de ficheiro:
a ligacao principal corre em modo WAL (PRAGMA journal_mode=WAL em db.connect), por isso
copiar o .db diretamente podia deixar de fora escritas ainda so no ficheiro -wal.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("nsrecruiter.backup")

_FORMATO_DATA_NOME_FICHEIRO = "%Y%m%d_%H%M%S"


def _nome_ficheiro_backup(caminho_bd: Path, agora: datetime) -> str:
    return f"{caminho_bd.stem}_{agora.strftime(_FORMATO_DATA_NOME_FICHEIRO)}{caminho_bd.suffix}"


def criar_backup(ligacao: sqlite3.Connection, caminho_bd: Path, pasta_backup: Path) -> Path:
    """Uma copia de seguranca pontual. Devolve o caminho do ficheiro gravado."""
    pasta_backup.mkdir(parents=True, exist_ok=True)
    destino = pasta_backup / _nome_ficheiro_backup(caminho_bd, datetime.now(timezone.utc))
    ligacao_destino = sqlite3.connect(destino)
    try:
        ligacao.backup(ligacao_destino)
    finally:
        ligacao_destino.close()
    return destino


async def correr_backups_periodicos(
    ligacao: sqlite3.Connection, caminho_bd: Path, pasta_backup: Path, intervalo_horas: float
) -> None:
    """Ciclo de fundo: uma copia logo ao arrancar, depois uma a cada `intervalo_horas`,
    para sempre. Uma falha (pasta externa desligada, disco cheio, etc.) fica so registada;
    nunca deve derrubar o resto da aplicacao."""
    intervalo_segundos = intervalo_horas * 3600.0
    while True:
        try:
            destino = criar_backup(ligacao, caminho_bd, pasta_backup)
            logger.info("Backup da base de dados concluido: %s", destino)
        except (OSError, sqlite3.Error):
            logger.exception("Falha ao criar backup da base de dados em %s.", pasta_backup)
        await asyncio.sleep(intervalo_segundos)
