"""Testes da extracao de nome de nacao a partir do texto de um evento de fundacao."""
from __future__ import annotations

from nsrecruiter.collector import extract_founded_nation


def test_extract_founded_nation_from_typical_event() -> None:
    data = "@@testlandia@@ was founded in %%testregion%%."
    assert extract_founded_nation(data) == "testlandia"


def test_extract_founded_nation_returns_none_when_absent() -> None:
    assert extract_founded_nation("mensagem sem marcacao de nacao") is None
