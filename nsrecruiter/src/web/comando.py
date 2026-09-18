"""Comando standalone `ns-recruiter-web`: serve o dashboard web a partir da base de
dados existente (`data/nsrecruiter.db`), sem exigir que o NSRecruiter principal esteja
a correr. So leitura, nunca escreve nada."""
from __future__ import annotations

import argparse
import sys
import webbrowser

from nsrecruiter.config import ConfigError, load_config
from nsrecruiter.web.servidor import PORTA_OMISSAO, criar_servidor


def construir_analisador() -> argparse.ArgumentParser:
    analisador = argparse.ArgumentParser(
        prog="ns-recruiter-web",
        description="Dashboard web (so leitura) com as estatisticas do NSRecruiter.",
    )
    analisador.add_argument(
        "--host", dest="anfitriao", default="127.0.0.1", help="Interface a escutar (default: 127.0.0.1)."
    )
    analisador.add_argument(
        "--port",
        dest="porta",
        type=int,
        default=PORTA_OMISSAO,
        help=f"Porto a escutar (default: {PORTA_OMISSAO}; se estiver ocupado, escolhe um livre).",
    )
    analisador.add_argument(
        "--no-browser", dest="sem_browser", action="store_true", help="Nao abrir o browser automaticamente."
    )
    return analisador


def principal(argv: list[str] | None = None) -> int:
    analisador = construir_analisador()
    argumentos = analisador.parse_args(argv)

    try:
        configuracao = load_config()
    except ConfigError as exc:
        print(f"Configuracao invalida: {exc}", file=sys.stderr)
        print("Corre `python -m nsrecruiter --setup` primeiro.", file=sys.stderr)
        return 1

    servidor, url = criar_servidor(configuracao, anfitriao=argumentos.anfitriao, porta=argumentos.porta)
    print(f"Dashboard web do NSRecruiter em {url}")
    print("(so leitura; funciona com ou sem o NSRecruiter principal a correr)")
    print("Ctrl+C para terminar.")
    if not argumentos.sem_browser:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    print("Dashboard web terminado.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
