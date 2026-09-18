"""Testes de integracao do servidor web: pede as rotas reais por HTTP num porto livre."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from nsrecruiter.config import Config
from nsrecruiter.web.servidor import criar_servidor, iniciar_em_thread


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


def _pedir(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resposta:
            return resposta.status, resposta.read(), resposta.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def test_servidor_serve_pagina_index(tmp_path: Path) -> None:
    servidor, _thread, url = iniciar_em_thread(_configuracao(tmp_path), porta=0)
    try:
        estado, corpo, tipo_conteudo = _pedir(url)
        assert estado == 200
        assert "text/html" in tipo_conteudo
        assert b"NSRecruiter" in corpo
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_servidor_serve_ficheiros_estaticos(tmp_path: Path) -> None:
    servidor, _thread, url = iniciar_em_thread(_configuracao(tmp_path), porta=0)
    try:
        estado, corpo, tipo_conteudo = _pedir(url + "aplicacao.js")
        assert estado == 200
        assert "javascript" in tipo_conteudo
        assert b"obterFotografia" in corpo

        estado, _, tipo_conteudo = _pedir(url + "vendor/chart.umd.min.js")
        assert estado == 200
        assert "javascript" in tipo_conteudo
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_servidor_serve_fotografia_json_mesmo_sem_bd(tmp_path: Path) -> None:
    servidor, _thread, url = iniciar_em_thread(_configuracao(tmp_path), porta=0)
    try:
        estado, corpo, tipo_conteudo = _pedir(url + "api/fotografia")
        assert estado == 200
        assert "application/json" in tipo_conteudo
        dados = json.loads(corpo)
        assert dados["tem_dados"] is False
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_servidor_rejeita_caminhos_desconhecidos(tmp_path: Path) -> None:
    servidor, _thread, url = iniciar_em_thread(_configuracao(tmp_path), porta=0)
    try:
        estado, _, _ = _pedir(url + "../etc/passwd")
        assert estado == 404
        estado, _, _ = _pedir(url + "nao-existe")
        assert estado == 404
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_criar_servidor_devolve_url_correspondente_a_porta(tmp_path: Path) -> None:
    servidor, url = criar_servidor(_configuracao(tmp_path), anfitriao="127.0.0.1", porta=0)
    try:
        assert url == f"http://127.0.0.1:{servidor.server_port}/"
        assert servidor.server_port != 0
    finally:
        servidor.server_close()
