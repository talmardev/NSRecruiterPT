"""Testes das consultas de so-leitura do dashboard web (SQLite real em ficheiro temporario)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from nsrecruiter import db
from nsrecruiter.config import Config
from nsrecruiter.models import TargetSource
from nsrecruiter.web import consultas


def _configuracao(tmp_path: Path) -> Config:
    return Config(
        region="Portugal",
        nation="New Libertalia Kingdom",
        contact=None,
        client_key="clientkey1234567890",
        telegram_id="123456789",
        secret_key="secretkeyabcdef1234567890",
        send_interval_seconds=182.0,
        db_path=tmp_path / "test.db",
        lockfile_path=tmp_path / "test.lock",
        log_dir=tmp_path / "logs",
        log_level="INFO",
    )


def _ligacao(configuracao: Config):
    ligacao = db.connect(configuracao.db_path)
    db.init_schema(ligacao)
    return ligacao


def test_construir_fotografia_sem_bd_reporta_sem_dados(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    fotografia = consultas.construir_fotografia(configuracao)
    assert fotografia["tem_dados"] is False
    assert fotografia["ao_vivo"] == {"ligado": False}
    assert fotografia["info"] == {"regiao": "Portugal", "nacao": "New Libertalia Kingdom"}


def test_construir_fotografia_sem_targets_da_serie_completa_vazia(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    _ligacao(configuracao).close()
    fotografia = consultas.construir_fotografia(configuracao)
    assert fotografia["serie_completa"] == []


def test_construir_fotografia_reflete_conteudo_da_bd(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    ligacao = _ligacao(configuracao)
    agora = "2026-09-08T12:00:00+00:00"

    db.insert_discovered_target(ligacao, "sent_nation", "Sent Nation", TargetSource.SSE, agora)
    db.mark_target_queued(ligacao, "sent_nation", agora)
    db.mark_target_sent(ligacao, "sent_nation", agora)
    db.insert_send_history(ligacao, "sent_nation", agora, success=True, http_status=200, retry_after=None, error_reason=None)

    db.insert_discovered_target(ligacao, "failed_nation", "Failed Nation", TargetSource.SSE, agora)
    db.insert_send_history(
        ligacao, "failed_nation", agora, success=False, http_status=409, retry_after=None, error_reason="conflito"
    )

    db.insert_discovered_target(ligacao, "queued_nation", "Queued Nation", TargetSource.SSE, agora)
    db.mark_target_queued(ligacao, "queued_nation", agora)

    db.insert_discovered_target(ligacao, "rejected_nation", "Rejected Nation", TargetSource.SSE, agora)
    db.mark_target_rejected(ligacao, "rejected_nation", "ja esta na regiao", agora)

    db.insert_discovered_target(ligacao, "heuristic_nation", "Heuristic Nation", TargetSource.SSE, agora)
    db.mark_target_rejected(ligacao, "heuristic_nation", "provavel alt", agora, heuristic=True, detail="alt")
    ligacao.close()

    fotografia = consultas.construir_fotografia(configuracao)

    assert fotografia["tem_dados"] is True
    contagens = fotografia["contagens"]
    assert contagens["enviados_total"] == 1
    assert contagens["tentativas_total"] == 2
    assert contagens["tamanho_fila"] == 1
    assert contagens["rejeitados_total"] == 2
    assert contagens["rejeitados_padrao_total"] == 1
    assert contagens["rejeitados_diretos_total"] == 1
    assert contagens["expirados_total"] == 0

    funil = fotografia["funil"]
    assert funil["sent"] == 1
    assert funil["queued"] == 1
    assert funil["rejected"] == 2

    motivos = {linha["motivo"]: linha["quantidade"] for linha in fotografia["motivos_rejeicao"]}
    assert motivos["ja esta na regiao"] == 1
    assert motivos["provavel alt"] == 1

    categorias = {linha["categoria"]: linha["quantidade"] for linha in fotografia["categorias_rejeicao"]}
    assert categorias["direta"] == 1
    assert categorias["heuristica"] == 1

    falhas = {linha["motivo"]: linha["quantidade"] for linha in fotografia["motivos_falha"]}
    assert falhas["conflito"] == 1

    assert len(fotografia["serie_horaria"]) == 48
    assert len(fotografia["serie_diaria"]) == 30

    por_intervalo = {ponto["intervalo"]: ponto for ponto in fotografia["serie_completa"]}
    assert fotografia["serie_completa"][0]["intervalo"] == "2026-09-08"
    assert por_intervalo["2026-09-08"]["enviados"] == 1
    assert por_intervalo["2026-09-08"]["falhados"] == 1
    assert sum(ponto["enviados"] for ponto in fotografia["serie_completa"]) == 1


def test_construir_fotografia_serie_temporal_coloca_envio_no_intervalo_certo(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    ligacao = _ligacao(configuracao)
    agora_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    db.insert_discovered_target(ligacao, "n", "N", TargetSource.SSE, agora_iso)
    db.insert_send_history(ligacao, "n", agora_iso, success=True, http_status=200, retry_after=None, error_reason=None)
    ligacao.close()

    fotografia = consultas.construir_fotografia(configuracao)
    assert sum(ponto["enviados"] for ponto in fotografia["serie_horaria"]) == 1
    assert sum(ponto["enviados"] for ponto in fotografia["serie_diaria"]) == 1
    assert sum(ponto["enviados"] for ponto in fotografia["serie_completa"]) == 1
    assert fotografia["serie_horaria"][-1]["enviados"] == 1
    assert fotografia["serie_diaria"][-1]["enviados"] == 1
    assert fotografia["serie_completa"][-1]["enviados"] == 1


def test_construir_fotografia_estado_ao_vivo_ligado_quando_recente(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    ligacao = _ligacao(configuracao)
    dados = {
        "estado_app": "sending",
        "segundos_atividade": 120.0,
        "segundos_ate_proximo_envio": 30.0,
        "intervalo_envio_segundos": 182.0,
        "restante_geral": 40,
        "limite_geral": 50,
        "segundos_espera_geral": 0.0,
        "motivo_bloqueio": None,
        "segundos_bloqueio": 0.0,
        "modo_simulacao": False,
        "atualizado_em_unix": time.time(),
    }
    db.set_kv(ligacao, "estado_ao_vivo", json.dumps(dados), "2026-09-08T12:00:00+00:00")
    ligacao.close()

    fotografia = consultas.construir_fotografia(configuracao)
    assert fotografia["ao_vivo"]["ligado"] is True
    assert fotografia["ao_vivo"]["estado_app"] == "sending"


def test_construir_fotografia_estado_ao_vivo_desligado_quando_antigo(tmp_path: Path) -> None:
    configuracao = _configuracao(tmp_path)
    ligacao = _ligacao(configuracao)
    dados = {"estado_app": "sending", "atualizado_em_unix": time.time() - 60}
    db.set_kv(ligacao, "estado_ao_vivo", json.dumps(dados), "2026-09-08T12:00:00+00:00")
    ligacao.close()

    fotografia = consultas.construir_fotografia(configuracao)
    assert fotografia["ao_vivo"]["ligado"] is False
