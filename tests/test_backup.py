"""Testes do backup periodico da base de dados para uma pasta externa."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from nsrecruiter import db
from nsrecruiter.backup import correr_backups_periodicos, criar_backup
from nsrecruiter.models import TargetSource


def _ligacao(caminho_bd: Path) -> sqlite3.Connection:
    ligacao = db.connect(caminho_bd)
    db.init_schema(ligacao)
    return ligacao


def test_criar_backup_grava_uma_copia_restauravel(tmp_path: Path) -> None:
    caminho_bd = tmp_path / "nsrecruiter.db"
    ligacao = _ligacao(caminho_bd)
    db.insert_discovered_target(
        ligacao, "testlandia", "Testlandia", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )

    destino = criar_backup(ligacao, caminho_bd, tmp_path / "backups")

    ligacao_copia = sqlite3.connect(destino)
    try:
        linha = ligacao_copia.execute("SELECT nation_id FROM targets").fetchone()
        assert linha == ("testlandia",)
    finally:
        ligacao_copia.close()
    ligacao.close()


def test_criar_backup_cria_pasta_de_backup_em_falta(tmp_path: Path) -> None:
    caminho_bd = tmp_path / "nsrecruiter.db"
    ligacao = _ligacao(caminho_bd)
    pasta_backup = tmp_path / "drive_externa" / "backups"

    destino = criar_backup(ligacao, caminho_bd, pasta_backup)

    assert pasta_backup.is_dir()
    assert destino.is_file()
    ligacao.close()


def test_criar_backup_nome_ficheiro_reutiliza_nome_e_extensao_da_bd(tmp_path: Path) -> None:
    caminho_bd = tmp_path / "nsrecruiter.db"
    ligacao = _ligacao(caminho_bd)

    destino = criar_backup(ligacao, caminho_bd, tmp_path / "backups")

    assert destino.name.startswith("nsrecruiter_")
    assert destino.suffix == ".db"
    ligacao.close()


def test_correr_backups_periodicos_faz_uma_copia_logo_ao_arrancar(tmp_path: Path) -> None:
    caminho_bd = tmp_path / "nsrecruiter.db"
    ligacao = _ligacao(caminho_bd)
    pasta_backup = tmp_path / "backups"

    async def cenario() -> list[Path]:
        tarefa = asyncio.create_task(
            correr_backups_periodicos(ligacao, caminho_bd, pasta_backup, intervalo_horas=24.0)
        )
        try:
            prazo = time.monotonic() + 5.0
            while time.monotonic() < prazo:
                if pasta_backup.is_dir() and any(pasta_backup.iterdir()):
                    return list(pasta_backup.iterdir())
                await asyncio.sleep(0.02)
            return list(pasta_backup.iterdir()) if pasta_backup.is_dir() else []
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)

    ficheiros = asyncio.run(cenario())
    ligacao.close()
    assert len(ficheiros) == 1, "devia ter feito um backup logo ao arrancar, sem esperar pelo intervalo de 24h"


def test_correr_backups_periodicos_regista_e_sobrevive_a_pasta_de_backup_impossivel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caminho_bd = tmp_path / "nsrecruiter.db"
    ligacao = _ligacao(caminho_bd)

    # Um ficheiro no lugar onde a pasta de backup devia estar: mkdir(parents=True,
    # exist_ok=True) so ignora o "ja existe" quando ja e uma pasta, aqui levanta OSError.
    caminho_bloqueado = tmp_path / "bloqueado"
    caminho_bloqueado.write_text("nao e uma pasta", encoding="utf-8")

    async def cenario() -> None:
        tarefa = asyncio.create_task(
            correr_backups_periodicos(ligacao, caminho_bd, caminho_bloqueado, intervalo_horas=24.0)
        )
        try:
            await asyncio.sleep(0.2)
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)

    with caplog.at_level("ERROR", logger="nsrecruiter.backup"):
        asyncio.run(cenario())
    ligacao.close()

    assert "Falha ao criar backup" in caplog.text
