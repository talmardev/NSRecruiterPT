"""Servidor HTTP local (so leitura) que serve o dashboard web: ficheiros estaticos
mais um endpoint JSON com a fotografia atual das estatisticas. Pode ser arrancado de
duas formas. Ver `criar_servidor`/`iniciar_em_thread` (a partir do dashboard do
terminal, tecla 'w') e `nsrecruiter.web.comando` (comando standalone `ns-recruiter-web`).
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from nsrecruiter.config import Config
from nsrecruiter.web import consultas

logger = logging.getLogger("nsrecruiter.web")

PORTA_OMISSAO = 8765

_DIRETORIA_ESTATICA = Path(__file__).resolve().parent / "static"

# Mapa fechado de rotas -> ficheiro estatico: nunca resolve caminhos a partir do URL
# pedido, para nao abrir a porta a leitura arbitraria de ficheiros do disco.
_ROTAS_ESTATICAS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/estilo.css": ("estilo.css", "text/css; charset=utf-8"),
    "/aplicacao.js": ("aplicacao.js", "application/javascript; charset=utf-8"),
    "/vendor/chart.umd.min.js": ("vendor/chart.umd.min.js", "application/javascript; charset=utf-8"),
}


def _criar_gestor(configuracao: Config) -> type[BaseHTTPRequestHandler]:
    class Gestor(BaseHTTPRequestHandler):
        server_version = "NSRecruiterWeb/1.0"

        def log_message(self, formato: str, *argumentos: object) -> None:
            logger.debug("%s - %s", self.address_string(), formato % argumentos)

        def do_GET(self) -> None:  # noqa: N802 (nome exigido por BaseHTTPRequestHandler)
            caminho = self.path.split("?", 1)[0]
            if caminho == "/api/fotografia":
                self._enviar_json(consultas.construir_fotografia(configuracao))
                return
            rota = _ROTAS_ESTATICAS.get(caminho)
            if rota is None:
                self.send_error(404, "Nao encontrado")
                return
            self._enviar_ficheiro_estatico(*rota)

        def _enviar_json(self, dados: dict) -> None:
            corpo = json.dumps(dados).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _enviar_ficheiro_estatico(self, caminho_relativo: str, tipo_conteudo: str) -> None:
            try:
                corpo = (_DIRETORIA_ESTATICA / caminho_relativo).read_bytes()
            except OSError:
                self.send_error(404, "Nao encontrado")
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo_conteudo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    return Gestor


def criar_servidor(
    configuracao: Config, anfitriao: str = "127.0.0.1", porta: int = PORTA_OMISSAO
) -> tuple[ThreadingHTTPServer, str]:
    """Cria o servidor (sem o arrancar). Se `porta` estiver ocupada, escolhe uma livre."""
    gestor = _criar_gestor(configuracao)
    try:
        servidor = ThreadingHTTPServer((anfitriao, porta), gestor)
    except OSError:
        servidor = ThreadingHTTPServer((anfitriao, 0), gestor)
    url = f"http://{anfitriao}:{servidor.server_port}/"
    return servidor, url


def iniciar_em_thread(
    configuracao: Config, anfitriao: str = "127.0.0.1", porta: int = PORTA_OMISSAO
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Arranca o servidor numa thread daemon, usado pelo atalho 'w' do dashboard do
    terminal, para nao bloquear o resto da aplicacao. A thread morre sozinha quando o
    processo principal termina, por isso nao precisa de paragem explicita."""
    servidor, url = criar_servidor(configuracao, anfitriao, porta)
    thread = threading.Thread(target=servidor.serve_forever, name="nsrecruiter-web", daemon=True)
    thread.start()
    return servidor, thread, url
