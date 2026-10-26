"""Testes da reparacao da base de dados SQLite (nsrecruiter.reparar_bd)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nsrecruiter import db, reparar_bd
from nsrecruiter.lockfile import LockFile
from nsrecruiter.models import TargetSource

_AGORA = "2026-09-24T10:00:00+00:00"
# Alvos que chegam para o indice de palavras ter varias paginas, como numa base real.
_TOTAL_ALVOS = 800
_PALAVRAS_POR_ALVO = 3


def _criar_bd_povoada(caminho: Path) -> None:
    ligacao = db.connect(caminho)
    db.init_schema(ligacao)
    ligacao.execute("BEGIN")
    for indice in range(_TOTAL_ALVOS):
        nome = f"alpha{indice:03d}_bravo{indice:03d}_charlie"
        db.insert_discovered_target(ligacao, nome, nome, TargetSource.SSE, _AGORA)
    ligacao.execute("COMMIT")
    db.insert_send_history(ligacao, "alpha000_bravo000_charlie", _AGORA, True, 200, None, None)
    db.set_kv(ligacao, "sse_last_event_id", "123", _AGORA)
    # Tudo no ficheiro principal e sem -wal: assim so se estraga o que o teste quer.
    ligacao.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ligacao.close()


def _estragar_pagina_raiz(caminho: Path, nome_objeto: str) -> None:
    """Estraga o tipo da pagina raiz de uma tabela ou indice (simula corrupcao no disco)."""
    ligacao = sqlite3.connect(caminho)
    (raiz,) = ligacao.execute("SELECT rootpage FROM sqlite_master WHERE name = ?", (nome_objeto,)).fetchone()
    (tamanho_pagina,) = ligacao.execute("PRAGMA page_size").fetchone()
    ligacao.close()
    with open(caminho, "r+b") as ficheiro:
        ficheiro.seek((raiz - 1) * tamanho_pagina)
        ficheiro.write(b"\xff")


def _query_do_validador(caminho: Path) -> int:
    """A query que rebentou no arranque: so o alvo alpha001_bravo001_charlie partilha 2+ palavras."""
    ligacao = db.connect(caminho)
    try:
        return db.count_targets_sharing_tokens_since(
            ligacao,
            {"alpha001", "bravo001", "charlie"},
            "outro_alvo",
            "2026-01-01T00:00:00+00:00",
            "2027-01-01T00:00:00+00:00",
            2,
        )
    finally:
        ligacao.close()


def _configurar_ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # sem .env por perto
    variaveis = {
        "NS_REGION": "Portugal",
        "NS_NATION": "Testlandia",
        "NS_CLIENT_KEY": "clientkey1234567890",
        "NS_TELEGRAM_ID": "123456789",
        "NS_SECRET_KEY": "secretkeyabcdef1234567890",
        "DB_PATH": str(tmp_path / "nsrecruiter.db"),
        "LOCKFILE_PATH": str(tmp_path / "nsrecruiter.lock"),
    }
    for nome, valor in variaveis.items():
        monkeypatch.setenv(nome, valor)


def test_base_integra_nao_e_alterada_nem_deixa_copias(tmp_path: Path) -> None:
    caminho = tmp_path / "nsrecruiter.db"
    _criar_bd_povoada(caminho)
    antes = caminho.read_bytes()

    resultado = reparar_bd.reparar(caminho)

    assert resultado.problemas == ()
    assert resultado.pasta_copia is None
    assert caminho.read_bytes() == antes
    assert not list(tmp_path.glob("reparacao_*"))
    assert _query_do_validador(caminho) == 1


def test_reconstroi_base_com_o_indice_de_palavras_corrompido(tmp_path: Path) -> None:
    caminho = tmp_path / "nsrecruiter.db"
    _criar_bd_povoada(caminho)
    _estragar_pagina_raiz(caminho, "idx_target_name_tokens_token")
    corrompida = caminho.read_bytes()
    with pytest.raises(sqlite3.DatabaseError, match="malformed"):
        _query_do_validador(caminho)

    resultado = reparar_bd.reparar(caminho)

    assert resultado.problemas
    assert resultado.linhas_copiadas == {"targets": _TOTAL_ALVOS, "send_history": 1, "kv_state": 1}
    assert _query_do_validador(caminho) == 1
    ligacao = sqlite3.connect(caminho)
    try:
        assert ligacao.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        contagem = ligacao.execute("SELECT COUNT(*) FROM target_name_tokens").fetchone()[0]
        assert contagem == _TOTAL_ALVOS * _PALAVRAS_POR_ALVO
        valor = ligacao.execute("SELECT value FROM kv_state WHERE key = 'sse_last_event_id'").fetchone()[0]
        assert valor == "123"
    finally:
        ligacao.close()
    assert resultado.pasta_copia is not None
    assert (resultado.pasta_copia / caminho.name).read_bytes() == corrompida
    assert not caminho.with_name(caminho.name + ".nova").exists()


def test_reconstruida_continua_a_numerar_o_historico_de_envios(tmp_path: Path) -> None:
    caminho = tmp_path / "nsrecruiter.db"
    _criar_bd_povoada(caminho)
    _estragar_pagina_raiz(caminho, "idx_target_name_tokens_token")
    reparar_bd.reparar(caminho)

    ligacao = db.connect(caminho)
    try:
        db.insert_send_history(ligacao, "alpha001_bravo001_charlie", _AGORA, False, 429, 5.0, "limite")
        ids = [linha["id"] for linha in ligacao.execute("SELECT id FROM send_history ORDER BY id")]
    finally:
        ligacao.close()
    assert ids == [1, 2]


def test_aborta_sem_substituir_nada_quando_a_tabela_targets_esta_ilegivel(tmp_path: Path) -> None:
    caminho = tmp_path / "nsrecruiter.db"
    _criar_bd_povoada(caminho)
    _estragar_pagina_raiz(caminho, "targets")
    corrompida = caminho.read_bytes()

    with pytest.raises(reparar_bd.ReparacaoImpossivelError, match="copia dos ficheiros originais"):
        reparar_bd.reparar(caminho)

    assert caminho.read_bytes() == corrompida
    assert not caminho.with_name(caminho.name + ".nova").exists()
    (pasta_copia,) = tmp_path.glob("reparacao_*")
    assert (pasta_copia / caminho.name).read_bytes() == corrompida


def test_principal_recusa_quando_a_aplicacao_esta_a_correr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configurar_ambiente(monkeypatch, tmp_path)
    _criar_bd_povoada(tmp_path / "nsrecruiter.db")

    with LockFile(tmp_path / "nsrecruiter.lock"):
        codigo = reparar_bd.principal()

    assert codigo == reparar_bd.EXIT_LOCK_ERROR
    assert "outra instancia" in capsys.readouterr().err
    assert not list(tmp_path.glob("reparacao_*"))


def test_principal_repara_e_liberta_o_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configurar_ambiente(monkeypatch, tmp_path)
    caminho = tmp_path / "nsrecruiter.db"
    _criar_bd_povoada(caminho)
    _estragar_pagina_raiz(caminho, "idx_target_name_tokens_token")

    codigo = reparar_bd.principal()

    assert codigo == reparar_bd.EXIT_OK
    assert "Copia de seguranca" in capsys.readouterr().out
    assert not (tmp_path / "nsrecruiter.lock").exists()
    assert _query_do_validador(caminho) == 1


def test_principal_falha_com_mensagem_clara_quando_nao_ha_base_de_dados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configurar_ambiente(monkeypatch, tmp_path)

    codigo = reparar_bd.principal()

    assert codigo == reparar_bd.EXIT_FALHOU
    assert "Nao existe base de dados" in capsys.readouterr().err
    assert not (tmp_path / "nsrecruiter.db").exists()
