"""Consultas de so-leitura sobre a base de dados do NSRecruiter, para o dashboard web.

Cada funcao abre e fecha a sua propria ligacao SQLite em modo read-only: o dashboard
web e um leitor independente do processo principal (o URI 'mode=ro' garante que nunca
escreve na base de dados), e por isso pode correr mesmo que o NSRecruiter em si nao
esteja a correr. Nesse caso so nao tera "ao vivo" (ver `_estado_ao_vivo`).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nsrecruiter.config import Config
from nsrecruiter.models import TargetStatus
from nsrecruiter.utils import utc_now_iso

# Se o processo principal nao atualizar 'estado_ao_vivo' (kv_state) ha mais tempo do
# que isto, consideramos que nao esta a correr. O dashboard ao vivo escreve a cada
# tick (1s normalmente, 0.1s em modo de navegacao), por isso qualquer coisa acima de
# alguns segundos ja significa mesmo que parou.
_SEGUNDOS_ATE_DESATUALIZAR = 8.0

_JANELA_HORARIA_EM_HORAS = 48
_JANELA_DIARIA_EM_DIAS = 30

# Traduz para o esquema de saida os valores de 'rejection_category' ja gravados na
# base de dados por codigo existente (validator.py/queue_expiry.py); esses valores
# gravados ('heuristic'/'expired') nao mudam so por causa desta API.
_ROTULO_CATEGORIA = {"heuristic": "heuristica", "expired": "expirado"}


def _ligar_so_leitura(caminho_bd: Path) -> sqlite3.Connection | None:
    if not caminho_bd.exists():
        return None
    uri = caminho_bd.resolve().as_uri() + "?mode=ro"
    ligacao = sqlite3.connect(uri, uri=True)
    ligacao.row_factory = sqlite3.Row
    return ligacao


def _inicio_de_hoje_iso() -> str:
    agora = datetime.now(timezone.utc)
    return agora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _horas_atras_iso(horas: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat(timespec="seconds")


def _escalar(ligacao: sqlite3.Connection, sql: str, parametros: tuple = ()) -> int:
    linha = ligacao.execute(sql, parametros).fetchone()
    return linha[0] or 0


def _contagens(ligacao: sqlite3.Connection) -> dict:
    enviados_hoje = _escalar(
        ligacao,
        "SELECT COUNT(*) FROM send_history WHERE success = 1 AND attempted_at >= ?",
        (_inicio_de_hoje_iso(),),
    )
    enviados_total = _escalar(ligacao, "SELECT COUNT(*) FROM send_history WHERE success = 1")
    tentativas_total = _escalar(ligacao, "SELECT COUNT(*) FROM send_history")
    enviados_ultima_hora = _escalar(
        ligacao,
        "SELECT COUNT(*) FROM send_history WHERE success = 1 AND attempted_at >= ?",
        (_horas_atras_iso(1),),
    )
    tamanho_fila = _escalar(ligacao, "SELECT COUNT(*) FROM targets WHERE status = ?", (TargetStatus.QUEUED.value,))
    rejeitados_total = _escalar(
        ligacao, "SELECT COUNT(*) FROM targets WHERE status = ?", (TargetStatus.REJECTED.value,)
    )
    rejeitados_padrao_total = _escalar(
        ligacao,
        "SELECT COUNT(*) FROM targets WHERE status = ? AND rejection_category = 'heuristic'",
        (TargetStatus.REJECTED.value,),
    )
    expirados_total = _escalar(
        ligacao,
        "SELECT COUNT(*) FROM targets WHERE status = ? AND rejection_category = 'expired'",
        (TargetStatus.REJECTED.value,),
    )
    rejeitados_diretos_total = max(0, rejeitados_total - rejeitados_padrao_total - expirados_total)

    taxa_sucesso = (enviados_total / tentativas_total) if tentativas_total else None
    return {
        "enviados_hoje": enviados_hoje,
        "enviados_total": enviados_total,
        "tentativas_total": tentativas_total,
        "enviados_ultima_hora": enviados_ultima_hora,
        "tamanho_fila": tamanho_fila,
        "rejeitados_total": rejeitados_total,
        "rejeitados_padrao_total": rejeitados_padrao_total,
        "expirados_total": expirados_total,
        "rejeitados_diretos_total": rejeitados_diretos_total,
        "taxa_sucesso": taxa_sucesso,
    }


def _projecao_24h(configuracao: Config, taxa_sucesso: float | None) -> int:
    maximo_teorico_24h = int(86400 / configuracao.send_interval_seconds)
    if taxa_sucesso is None:
        return maximo_teorico_24h
    return round(maximo_teorico_24h * taxa_sucesso)


def _funil(ligacao: sqlite3.Connection) -> dict:
    linhas = ligacao.execute("SELECT status, COUNT(*) AS n FROM targets GROUP BY status").fetchall()
    contagens = {linha["status"]: linha["n"] for linha in linhas}
    return {status.value: contagens.get(status.value, 0) for status in TargetStatus}


def _motivos_rejeicao(ligacao: sqlite3.Connection) -> list[dict]:
    linhas = ligacao.execute(
        "SELECT COALESCE(status_reason, 'desconhecido') AS motivo, COUNT(*) AS n FROM targets "
        "WHERE status = ? GROUP BY motivo ORDER BY n DESC LIMIT 8",
        (TargetStatus.REJECTED.value,),
    ).fetchall()
    return [{"motivo": linha["motivo"], "quantidade": linha["n"]} for linha in linhas]


def _categorias_rejeicao(ligacao: sqlite3.Connection) -> list[dict]:
    linhas = ligacao.execute(
        "SELECT COALESCE(rejection_category, 'direta') AS categoria, COUNT(*) AS n FROM targets "
        "WHERE status = ? GROUP BY categoria",
        (TargetStatus.REJECTED.value,),
    ).fetchall()
    return [
        {"categoria": _ROTULO_CATEGORIA.get(linha["categoria"], linha["categoria"]), "quantidade": linha["n"]}
        for linha in linhas
    ]


def _motivos_falha(ligacao: sqlite3.Connection) -> list[dict]:
    linhas = ligacao.execute(
        "SELECT COALESCE(error_reason, 'desconhecido') AS motivo, COUNT(*) AS n "
        "FROM send_history WHERE success = 0 GROUP BY motivo ORDER BY n DESC LIMIT 8"
    ).fetchall()
    return [{"motivo": linha["motivo"], "quantidade": linha["n"]} for linha in linhas]


def _serie_horaria(ligacao: sqlite3.Connection) -> list[dict]:
    desde = (datetime.now(timezone.utc) - timedelta(hours=_JANELA_HORARIA_EM_HORAS)).isoformat(timespec="seconds")
    linhas_envios = ligacao.execute(
        "SELECT strftime('%Y-%m-%dT%H:00:00Z', attempted_at) AS intervalo, "
        "SUM(success) AS enviados, SUM(1 - success) AS falhados "
        "FROM send_history WHERE attempted_at >= ? GROUP BY intervalo",
        (desde,),
    ).fetchall()
    por_intervalo = {linha["intervalo"]: (linha["enviados"] or 0, linha["falhados"] or 0) for linha in linhas_envios}

    linhas_descobertos = ligacao.execute(
        "SELECT strftime('%Y-%m-%dT%H:00:00Z', discovered_at) AS intervalo, COUNT(*) AS n "
        "FROM targets WHERE discovered_at >= ? GROUP BY intervalo",
        (desde,),
    ).fetchall()
    descobertos_por_intervalo = {linha["intervalo"]: linha["n"] for linha in linhas_descobertos}

    agora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    serie = []
    for deslocamento in range(_JANELA_HORARIA_EM_HORAS - 1, -1, -1):
        rotulo = (agora - timedelta(hours=deslocamento)).strftime("%Y-%m-%dT%H:00:00Z")
        enviados, falhados = por_intervalo.get(rotulo, (0, 0))
        serie.append(
            {
                "intervalo": rotulo,
                "enviados": enviados,
                "falhados": falhados,
                "descobertos": descobertos_por_intervalo.get(rotulo, 0),
            }
        )
    return serie


def _serie_diaria(ligacao: sqlite3.Connection) -> list[dict]:
    desde = (datetime.now(timezone.utc) - timedelta(days=_JANELA_DIARIA_EM_DIAS)).isoformat(timespec="seconds")
    linhas_envios = ligacao.execute(
        "SELECT strftime('%Y-%m-%d', attempted_at) AS intervalo, "
        "SUM(success) AS enviados, SUM(1 - success) AS falhados "
        "FROM send_history WHERE attempted_at >= ? GROUP BY intervalo",
        (desde,),
    ).fetchall()
    por_intervalo = {linha["intervalo"]: (linha["enviados"] or 0, linha["falhados"] or 0) for linha in linhas_envios}

    linhas_descobertos = ligacao.execute(
        "SELECT strftime('%Y-%m-%d', discovered_at) AS intervalo, COUNT(*) AS n "
        "FROM targets WHERE discovered_at >= ? GROUP BY intervalo",
        (desde,),
    ).fetchall()
    descobertos_por_intervalo = {linha["intervalo"]: linha["n"] for linha in linhas_descobertos}

    hoje = datetime.now(timezone.utc).date()
    serie = []
    for deslocamento in range(_JANELA_DIARIA_EM_DIAS - 1, -1, -1):
        rotulo = (hoje - timedelta(days=deslocamento)).isoformat()
        enviados, falhados = por_intervalo.get(rotulo, (0, 0))
        serie.append(
            {
                "intervalo": rotulo,
                "enviados": enviados,
                "falhados": falhados,
                "descobertos": descobertos_por_intervalo.get(rotulo, 0),
            }
        )
    return serie


def _serie_diaria_completa(ligacao: sqlite3.Connection) -> list[dict]:
    mais_antigo_envio = ligacao.execute("SELECT MIN(attempted_at) AS valor FROM send_history").fetchone()["valor"]
    mais_antigo_descoberto = ligacao.execute("SELECT MIN(discovered_at) AS valor FROM targets").fetchone()["valor"]
    candidatos = [valor for valor in (mais_antigo_envio, mais_antigo_descoberto) if valor]
    if not candidatos:
        return []
    data_inicio = datetime.fromisoformat(min(candidatos)).astimezone(timezone.utc).date()
    hoje = datetime.now(timezone.utc).date()

    linhas_envios = ligacao.execute(
        "SELECT strftime('%Y-%m-%d', attempted_at) AS intervalo, "
        "SUM(success) AS enviados, SUM(1 - success) AS falhados FROM send_history GROUP BY intervalo"
    ).fetchall()
    por_intervalo = {linha["intervalo"]: (linha["enviados"] or 0, linha["falhados"] or 0) for linha in linhas_envios}

    linhas_descobertos = ligacao.execute(
        "SELECT strftime('%Y-%m-%d', discovered_at) AS intervalo, COUNT(*) AS n FROM targets GROUP BY intervalo"
    ).fetchall()
    descobertos_por_intervalo = {linha["intervalo"]: linha["n"] for linha in linhas_descobertos}

    serie = []
    dia = data_inicio
    while dia <= hoje:
        rotulo = dia.isoformat()
        enviados, falhados = por_intervalo.get(rotulo, (0, 0))
        serie.append(
            {
                "intervalo": rotulo,
                "enviados": enviados,
                "falhados": falhados,
                "descobertos": descobertos_por_intervalo.get(rotulo, 0),
            }
        )
        dia += timedelta(days=1)
    return serie


def _estado_ao_vivo(ligacao: sqlite3.Connection) -> dict:
    linha = ligacao.execute("SELECT value FROM kv_state WHERE key = 'estado_ao_vivo'").fetchone()
    if linha is None:
        return {"ligado": False}
    try:
        dados = json.loads(linha["value"])
    except (ValueError, TypeError):
        return {"ligado": False}
    idade = time.time() - float(dados.get("atualizado_em_unix", 0))
    dados["ligado"] = idade < _SEGUNDOS_ATE_DESATUALIZAR
    return dados


def construir_fotografia(configuracao: Config) -> dict:
    """Fotografia completa do estado atual, pronta a serializar em JSON para a API web."""
    ligacao = _ligar_so_leitura(configuracao.db_path)
    if ligacao is None:
        return {
            "tem_dados": False,
            "gerado_em": utc_now_iso(),
            "info": {"regiao": configuracao.region, "nacao": configuracao.nation},
            "ao_vivo": {"ligado": False},
        }
    try:
        contagens = _contagens(ligacao)
        contagens["projecao_24h"] = _projecao_24h(configuracao, contagens["taxa_sucesso"])
        return {
            "tem_dados": True,
            "gerado_em": utc_now_iso(),
            "info": {"regiao": configuracao.region, "nacao": configuracao.nation},
            "contagens": contagens,
            "funil": _funil(ligacao),
            "motivos_rejeicao": _motivos_rejeicao(ligacao),
            "categorias_rejeicao": _categorias_rejeicao(ligacao),
            "motivos_falha": _motivos_falha(ligacao),
            "serie_horaria": _serie_horaria(ligacao),
            "serie_diaria": _serie_diaria(ligacao),
            "serie_completa": _serie_diaria_completa(ligacao),
            "ao_vivo": _estado_ao_vivo(ligacao),
        }
    finally:
        ligacao.close()
