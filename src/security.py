"""Mascaramento de segredos para logs, dashboard e mensagens de erro."""
from __future__ import annotations

_VISIBLE_CHARS = 4


def mask_secret(value: str) -> str:
    """Mostra so os extremos de um segredo, no formato 'abcd...7890'."""
    if len(value) <= _VISIBLE_CHARS * 2:
        return "*" * len(value)
    # ASCII simples de proposito: consolas Windows com codepage legado (e ficheiros de
    # log abertos fora de um editor UTF-8) trocam reticencias unicode por um caractere
    # de substituicao ilegivel -- reticencias '...' nunca tem esse risco.
    return f"{value[:_VISIBLE_CHARS]}...{value[-_VISIBLE_CHARS:]}"
