"""Carregador minimalista de ficheiros .env, sem dependencias externas."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Le um .env e define variaveis de ambiente que ainda nao existam ou estejam vazias."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Vazia conta como em falta: um NS_REGION= do .env.example nao pode tapar o valor
        # que o assistente grava a seguir, no mesmo processo.
        if not os.environ.get(key):
            os.environ[key] = value
