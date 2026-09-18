"""Tipos partilhados pelo dominio da aplicacao."""
from __future__ import annotations

import re
from enum import Enum


class TargetStatus(str, Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    VALIDATING = "validating"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"


class TargetSource(str, Enum):
    SSE = "sse"
    POLL = "poll"
    MANUAL = "manual"


class AppState(str, Enum):
    SENDING = "sending"
    WAITING = "waiting"
    PAUSED = "paused"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"


def normalize_nation(name: str) -> str:
    """Forma canonica de um nome de nacao, usada como chave (minusculas, underscores)."""
    return name.strip().lower().replace(" ", "_")


# Modificadores de direcao cardinal no inicio ou fim do nome. Decidido com o
# utilizador depois de uma leva "North X" / "West X" / "South X" passar ao filtro
# por so diferirem nessa palavra (menos do que o minimo de 2 palavras partilhadas
# exigido pelo lote gerado, MIN_SHARED_NAME_TOKENS abaixo).
_PALAVRAS_DIRECAO = frozenset({"north", "south", "east", "west"})


def extract_name_base(nation_id: str) -> str:
    """Nome sem o sufixo numerico final nem um modificador de direcao cardinal no
    inicio ou fim (usado para detetar provaveis alts, ex: 'yamagoochie0065' e
    'yamagoochie65' partilham a base 'yamagoochie'; 'north_atlantis' e
    'south_atlantis' partilham a base 'atlantis'). So remove um nivel de cada lado:
    nunca esvazia um nome cujas palavras sejam todas direcoes (ex: nao mexe em
    'north' sozinho)."""
    stripped = re.sub(r"\d+$", "", nation_id)
    partes = stripped.split("_")
    if len(partes) > 1 and partes[0] in _PALAVRAS_DIRECAO:
        partes = partes[1:]
    if len(partes) > 1 and partes[-1] in _PALAVRAS_DIRECAO:
        partes = partes[:-1]
    base = "_".join(partes)
    return base or stripped or nation_id


# Partilhado entre a deteçao automatica (validator.py, janela de 30 dias) e a analise
# manual da fila (dashboard.py, sem limite de tempo): bases muito curtas ficam de
# fora para nao gerar falsos positivos em nomes genericos.
MIN_NAME_BASE_LENGTH = 3

# Partilhado entre a deteçao automatica (validator.py, janela de 1h) e a analise
# manual da fila (dashboard.py, sem limite de tempo): o mesmo limiar qualitativo
# para o que conta como sobreposicao suspeita de palavras.
MIN_SHARED_NAME_TOKENS = 2

_MIN_SIGNIFICANT_TOKEN_LENGTH = 4


def significant_name_tokens(nation_id: str) -> set[str]:
    """Palavras do nome (separadas por '_') com potencial para identificar um lote
    gerado a partir de uma lista externa (ex: '2018_azerbaijan_grand_prix' e
    '2018_german_grand_prix' partilham 'grand' e 'prix'). Ignora numeros e palavras
    curtas de mais para serem distintivas."""
    return {
        token
        for token in nation_id.split("_")
        if len(token) >= _MIN_SIGNIFICANT_TOKEN_LENGTH and not token.isdigit()
    }
