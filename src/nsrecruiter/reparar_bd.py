"""Reparacao da base de dados: `python -m nsrecruiter.reparar_bd`.

Para quando a base de dados fica danificada (corte de energia, paragem brusca, falhas no
cartao SD) e a aplicacao rebenta ao ler dela. Guarda primeiro uma copia dos ficheiros,
verifica a integridade e, havendo problemas, reconstroi a base num ficheiro novo com o
que se le da antiga. Ou a reconstrucao fica completa, ou nada e substituido.

A tabela target_name_tokens nao e copiada: deriva de targets e o init_schema volta a
preenche-la.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nsrecruiter import db
from nsrecruiter.config import ConfigError, load_config
from nsrecruiter.lockfile import InstanceAlreadyRunningError, LockFile

EXIT_OK = 0
EXIT_FALHOU = 1
EXIT_LOCK_ERROR = 2

# Tabelas cujos dados nao se conseguem recalcular.
_TABELAS_A_COPIAR = ("targets", "send_history", "kv_state")

_MAX_PROBLEMAS_MOSTRADOS = 8


class ReparacaoImpossivelError(Exception):
    """Nao foi possivel reconstruir a base de dados; nada foi apagado nem substituido."""


@dataclass(frozen=True)
class ResultadoReparacao:
    problemas: tuple[str, ...]  # linhas do integrity_check; vazio se a base estava integra
    pasta_copia: Path | None  # copia dos ficheiros originais; None se nao foi preciso reparar
    linhas_copiadas: dict[str, int]  # por tabela; vazio se nao foi preciso reparar


def _copiar_para_seguranca(caminho_bd: Path) -> Path:
    """Copia a base de dados e o ficheiro -wal (escritas ainda nao integradas) para uma
    pasta nova ao lado dela. Corre antes de qualquer abertura da base, porque abri-la pode
    integrar o -wal e alterar os ficheiros. Esta copia nunca mais e aberta."""
    marca = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pasta = caminho_bd.parent / f"reparacao_{marca}"
    pasta.mkdir(parents=True)
    for sufixo in ("", "-wal"):
        origem = Path(f"{caminho_bd}{sufixo}")
        if origem.is_file():
            shutil.copy2(origem, pasta / origem.name)
    return pasta


def _problemas_de_integridade(caminho_bd: Path) -> list[str]:
    """Linhas do `PRAGMA integrity_check`; lista vazia se a base esta integra."""
    try:
        ligacao = sqlite3.connect(caminho_bd)
        try:
            respostas = [str(linha[0]) for linha in ligacao.execute("PRAGMA integrity_check(20)")]
        finally:
            ligacao.close()
    except sqlite3.DatabaseError as exc:
        return [str(exc)]
    linhas = [linha for resposta in respostas for linha in resposta.splitlines() if linha.strip()]
    return [] if linhas == ["ok"] else linhas


def _colunas_comuns(ligacao: sqlite3.Connection, tabela: str) -> list[str]:
    """Colunas presentes na base nova e na antiga (esta pode ser de uma versao anterior do
    esquema, com colunas de menos)."""
    novas = [linha["name"] for linha in ligacao.execute(f"PRAGMA main.table_info({tabela})")]
    antigas = {linha["name"] for linha in ligacao.execute(f"PRAGMA velho.table_info({tabela})")}
    return [coluna for coluna in novas if coluna in antigas]


def _copiar_dados(origem: Path, destino: Path) -> dict[str, int]:
    novo = sqlite3.connect(destino)
    novo.row_factory = sqlite3.Row
    try:
        db.init_schema(novo)
        novo.commit()  # o ATTACH nao corre dentro de uma transacao
        novo.execute("ATTACH DATABASE ? AS velho", (str(origem),))
        linhas_copiadas: dict[str, int] = {}
        for tabela in _TABELAS_A_COPIAR:
            colunas = _colunas_comuns(novo, tabela)
            if not colunas:
                continue
            lista = ", ".join(colunas)
            # ORDER BY rowid: mantem a ordem de insercao, de que depende o desempate por
            # rowid em db.count_targets_*_since.
            cursor = novo.execute(
                f"INSERT INTO main.{tabela} ({lista}) SELECT {lista} FROM velho.{tabela} ORDER BY rowid"
            )
            linhas_copiadas[tabela] = cursor.rowcount
        novo.commit()
        novo.execute("DETACH DATABASE velho")
        db.init_schema(novo)  # repoe target_name_tokens a partir dos alvos copiados
        novo.commit()
        return linhas_copiadas
    finally:
        novo.close()


def _reconstruir(origem: Path, destino: Path) -> dict[str, int]:
    """Cria `destino` com o esquema atual e copia para la o que se le de `origem`. Se alguma
    tabela nao puder ser lida por inteiro, apaga `destino` e levanta ReparacaoImpossivelError."""
    destino.unlink(missing_ok=True)
    try:
        linhas_copiadas = _copiar_dados(origem, destino)
    except sqlite3.DatabaseError as exc:
        destino.unlink(missing_ok=True)
        raise ReparacaoImpossivelError(f"Nao foi possivel ler tudo da base de dados ({exc}).") from exc

    problemas = _problemas_de_integridade(destino)
    if problemas:
        destino.unlink(missing_ok=True)
        raise ReparacaoImpossivelError(
            "A base reconstruida ainda tem problemas: " + "; ".join(problemas[:3]) + "."
        )
    return linhas_copiadas


def reparar(caminho_bd: Path) -> ResultadoReparacao:
    """Verifica `caminho_bd` e, se estiver danificada, reconstroi-a no mesmo sitio. Assume
    que mais nenhum processo a esta a usar (`principal` trata disso com o lockfile)."""
    if not caminho_bd.is_file():
        raise FileNotFoundError(f"Nao existe base de dados em {caminho_bd}.")

    pasta_copia = _copiar_para_seguranca(caminho_bd)
    problemas = _problemas_de_integridade(caminho_bd)
    if not problemas:
        shutil.rmtree(pasta_copia)
        return ResultadoReparacao(problemas=(), pasta_copia=None, linhas_copiadas={})

    reconstruida = caminho_bd.with_name(caminho_bd.name + ".nova")
    try:
        linhas_copiadas = _reconstruir(caminho_bd, reconstruida)
    except ReparacaoImpossivelError as exc:
        raise ReparacaoImpossivelError(
            f"{exc} Nada foi apagado ou substituido; copia dos ficheiros originais em {pasta_copia}."
        ) from exc

    # Um -wal ou -shm que sobre da base antiga nao pode ficar ao lado da nova.
    for sufixo in ("-wal", "-shm"):
        Path(f"{caminho_bd}{sufixo}").unlink(missing_ok=True)
    os.replace(reconstruida, caminho_bd)
    return ResultadoReparacao(
        problemas=tuple(problemas), pasta_copia=pasta_copia, linhas_copiadas=linhas_copiadas
    )


def _correr(caminho_bd: Path) -> int:
    print(f"A verificar {caminho_bd} (pode demorar um pouco num cartao SD)...")
    try:
        resultado = reparar(caminho_bd)
    except ReparacaoImpossivelError as exc:
        print(f"Falhou: {exc}", file=sys.stderr)
        print("Alternativas:", file=sys.stderr)
        print("  1. Restaurar o backup mais recente da PASTA_BACKUP do .env, se existir.", file=sys.stderr)
        print("  2. Salvar o que der com o sqlite3 (sudo apt install sqlite3):", file=sys.stderr)
        print(f'     sqlite3 {caminho_bd} ".recover" | sqlite3 {caminho_bd}.recuperada', file=sys.stderr)
        return EXIT_FALHOU
    except OSError as exc:
        print(f"Falhou: {exc}", file=sys.stderr)
        return EXIT_FALHOU

    if not resultado.problemas:
        print("A base de dados esta integra: nada foi alterado.")
        print("Se a aplicacao continua a falhar, o erro vem de outro lado.")
        return EXIT_OK

    print("Problemas encontrados na base de dados:")
    for linha in resultado.problemas[:_MAX_PROBLEMAS_MOSTRADOS]:
        print(f"  {linha}")
    if len(resultado.problemas) > _MAX_PROBLEMAS_MOSTRADOS:
        print("  (...)")
    print("Base de dados reconstruida. Linhas copiadas:")
    for tabela, quantas in resultado.linhas_copiadas.items():
        print(f"  {tabela}: {quantas}")
    print(f"Copia de seguranca dos ficheiros originais em: {resultado.pasta_copia}")
    print("Ja podes arrancar a aplicacao outra vez.")
    return EXIT_OK


def principal() -> int:
    try:
        configuracao = load_config()
    except ConfigError as exc:
        print(f"Configuracao invalida: {exc}", file=sys.stderr)
        return EXIT_FALHOU

    try:
        with LockFile(configuracao.lockfile_path):
            return _correr(configuracao.db_path)
    except InstanceAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_LOCK_ERROR


if __name__ == "__main__":
    sys.exit(principal())
