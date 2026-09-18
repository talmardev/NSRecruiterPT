"""Arranque do NSRecruiter em qualquer maquina.

Um ambiente virtual nunca e portavel: o `.venv` guarda o caminho absoluto do
interpretador que o criou (em `pyvenv.cfg`) e copias do executavel desse
Python. Levado para outro PC, ou para outro utilizador do mesmo PC, fica a
apontar para um caminho que ali nao existe e recusa arrancar, com um erro do
genero `did not find executable at ...`.

Este script corre com qualquer Python do sistema, confirma se o `.venv` serve
mesmo nesta maquina, refaz o que for preciso e so depois corre a aplicacao:

    arrancar.cmd --check            (Windows)
    python3 arrancar.py --check     (Linux/macOS)

Os argumentos sao passados tal e qual a `python -m nsrecruiter`, com duas
excecoes consumidas aqui:

    --refazer          deita fora o `.venv` e constroi-o do zero.
    --guardar-rodas    atualiza a pasta `rodas/` (as wheels das dependencias,
                       que viajam com o projeto) e sai sem correr a aplicacao.

Havendo `rodas/`, a instalacao e feita a partir dali e nao toca na rede; a
rede fica so como recurso de reserva, se faltar la alguma coisa.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

VERSAO_MINIMA = (3, 11)

RAIZ = Path(__file__).resolve().parent
AMBIENTE = RAIZ / ".venv"
PYPROJECT = RAIZ / "pyproject.toml"
# Wheels das dependencias, guardadas dentro do projeto para instalar sem rede.
RODAS = RAIZ / "rodas"
# Impressao do pyproject.toml usada na ultima instalacao, para detetar que as
# dependencias mudaram entretanto (noutro PC, por exemplo).
MARCA_DE_INSTALACAO = AMBIENTE / "nsrecruiter-instalado.txt"


def anunciar(mensagem: str) -> None:
    print("[arrancar] " + mensagem)


def sair_com_erro(mensagem: str) -> NoReturn:
    print("[arrancar] " + mensagem, file=sys.stderr)
    raise SystemExit(1)


def correr(comando: list[str]) -> int:
    return subprocess.run(comando, cwd=str(RAIZ)).returncode


def correr_ou_sair(comando: list[str]) -> None:
    if correr(comando) != 0:
        sair_com_erro("Falhou: " + " ".join(comando))


def python_do_ambiente() -> Path:
    # Windows poe os executaveis em Scripts/, os restantes sistemas em bin/.
    if os.name == "nt":
        return AMBIENTE / "Scripts" / "python.exe"
    return AMBIENTE / "bin" / "python"


def ambiente_serve_nesta_maquina(python: Path) -> bool:
    """Unico teste que interessa: o python do `.venv` arranca mesmo aqui?"""
    if not python.exists():
        return False
    try:
        resultado = subprocess.run(
            [str(python), "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return resultado.returncode == 0


def apagar_ambiente() -> None:
    if not AMBIENTE.exists():
        return
    # Defesa contra apagar a pasta errada: sem pyvenv.cfg nao e um ambiente.
    if not (AMBIENTE / "pyvenv.cfg").exists():
        sair_com_erro(
            "A pasta .venv existe mas nao tem pyvenv.cfg, por isso nao parece "
            "um ambiente virtual. Verifica-a a mao antes de voltar a correr."
        )
    try:
        shutil.rmtree(AMBIENTE)
    except OSError as erro:
        sair_com_erro(
            "Nao consegui apagar o .venv (" + str(erro) + "). Fecha o que "
            "estiver a usa-lo e tenta outra vez."
        )


def criar_ambiente() -> None:
    anunciar("A criar o .venv com " + sys.executable)
    correr_ou_sair([sys.executable, "-m", "venv", str(AMBIENTE)])


def impressao_do_pyproject() -> str:
    return hashlib.sha256(PYPROJECT.read_bytes()).hexdigest()


def dependencias_por_instalar(python: Path) -> bool:
    if not MARCA_DE_INSTALACAO.exists():
        return True
    if MARCA_DE_INSTALACAO.read_text(encoding="utf-8").strip() != impressao_do_pyproject():
        return True
    resultado = subprocess.run(
        [str(python), "-c", "import nsrecruiter"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return resultado.returncode != 0


def requisitos_do_projeto() -> list[str]:
    """Dependencias e backend de build, lidos do proprio pyproject.toml."""
    import tomllib  # stdlib a partir do 3.11, que ja e o minimo exigido aqui

    dados = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    projeto = dados.get("project", {})
    requisitos = list(projeto.get("dependencies", []))
    for extras in projeto.get("optional-dependencies", {}).values():
        requisitos.extend(extras)
    # O pip constroi o projeto num ambiente isolado a parte: sem o backend
    # guardado tambem, a instalacao editavel sem rede falha a meio.
    requisitos.extend(dados.get("build-system", {}).get("requires", []))
    return requisitos


def ha_rodas_guardadas() -> bool:
    return RODAS.is_dir() and any(RODAS.glob("*.whl"))


def guardar_rodas(python: Path) -> bool:
    """Traz para `rodas/` as wheels de tudo o que o projeto precisa."""
    RODAS.mkdir(exist_ok=True)
    comando = [
        str(python), "-m", "pip", "download",
        "--only-binary", ":all:",
        "--dest", str(RODAS),
    ]
    return correr(comando + requisitos_do_projeto()) == 0


def instalar_dependencias(python: Path) -> None:
    instalacao = [str(python), "-m", "pip", "install", "-e", ".[dev]"]
    instalado_sem_rede = False

    if ha_rodas_guardadas():
        anunciar("A instalar a partir de rodas/, sem tocar na rede.")
        instalado_sem_rede = correr(
            instalacao + ["--no-index", "--find-links", str(RODAS)]
        ) == 0
        if not instalado_sem_rede:
            anunciar("As rodas guardadas nao chegaram. A tentar pela rede.")

    if not instalado_sem_rede:
        anunciar("A instalar as dependencias (demora um pouco a primeira vez).")
        correr_ou_sair(instalacao)
        # Atualiza as rodas so para quem ja as tem: nao impomos a pasta a quem
        # nunca a quis, mas tambem nao a deixamos ficar para tras.
        if RODAS.is_dir() and not guardar_rodas(python):
            anunciar("Aviso: nao consegui atualizar as rodas guardadas.")

    MARCA_DE_INSTALACAO.write_text(impressao_do_pyproject(), encoding="utf-8")


def principal(argumentos: list[str]) -> int:
    consumidos = ("--refazer", "--guardar-rodas")
    refazer = "--refazer" in argumentos
    guardar = "--guardar-rodas" in argumentos
    argumentos = [a for a in argumentos if a not in consumidos]

    if sys.version_info < VERSAO_MINIMA:
        sair_com_erro(
            "Este Python e o %d.%d; o NSRecruiter precisa do %d.%d ou mais "
            "recente. Instala-o e corre outra vez (no Windows, com `py -3`)."
            % (sys.version_info[0], sys.version_info[1], *VERSAO_MINIMA)
        )

    python = python_do_ambiente()

    if refazer and AMBIENTE.exists():
        anunciar("Pedido --refazer: a deitar fora o ambiente atual.")
        apagar_ambiente()

    if not ambiente_serve_nesta_maquina(python):
        if AMBIENTE.exists():
            anunciar(
                "O .venv que esta aqui foi criado noutra maquina, ou o Python "
                "que o criou desapareceu. A refaze-lo."
            )
            apagar_ambiente()
        criar_ambiente()

    if dependencias_por_instalar(python):
        instalar_dependencias(python)

    if guardar:
        anunciar("A guardar as wheels das dependencias em rodas/.")
        if not guardar_rodas(python):
            sair_com_erro("Nao consegui guardar as rodas. A rede esta em baixo?")
        anunciar(
            "Rodas guardadas. Leva a pasta rodas/ contigo e o proximo PC "
            "instala sem precisar de internet."
        )
        return 0

    try:
        return subprocess.run(
            [str(python), "-m", "nsrecruiter", *argumentos], cwd=str(RAIZ)
        ).returncode
    except KeyboardInterrupt:
        # A aplicacao ja trata do Ctrl+C sozinha; aqui so nao estorvamos.
        return 130


if __name__ == "__main__":
    raise SystemExit(principal(sys.argv[1:]))
