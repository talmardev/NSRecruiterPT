"""Testes de parsing dos shards publicos (XML de exemplo, sem rede real)."""
from __future__ import annotations

import pytest

from nsrecruiter.api.shards import (
    ShardParseError,
    flag_matches_presets,
    parse_flag_url,
    parse_new_nations,
    parse_region_nations,
    parse_tgcanrecruit,
)


def test_parse_tgcanrecruit_true() -> None:
    xml = '<NATION id="alvo"><TGCANRECRUIT from="minharegiao">1</TGCANRECRUIT></NATION>'
    assert parse_tgcanrecruit(xml) is True


def test_parse_tgcanrecruit_false() -> None:
    xml = '<NATION id="alvo"><TGCANRECRUIT from="minharegiao">0</TGCANRECRUIT></NATION>'
    assert parse_tgcanrecruit(xml) is False


def test_parse_tgcanrecruit_missing_field_raises() -> None:
    with pytest.raises(ShardParseError):
        parse_tgcanrecruit('<NATION id="alvo"></NATION>')


def test_parse_tgcanrecruit_invalid_xml_raises() -> None:
    with pytest.raises(ShardParseError):
        parse_tgcanrecruit("isto nao e xml")


def test_parse_region_nations_splits_and_normalizes() -> None:
    xml = '<REGION id="minharegiao"><NATIONS>testlandia:Outra Nacao:terceira</NATIONS></REGION>'
    assert parse_region_nations(xml) == {"testlandia", "outra_nacao", "terceira"}


def test_parse_region_nations_empty() -> None:
    xml = '<REGION id="minharegiao"><NATIONS></NATIONS></REGION>'
    assert parse_region_nations(xml) == set()


def test_parse_new_nations_splits_on_comma() -> None:
    xml = "<WORLD><NEWNATIONS>testlandia,outra_nacao,terceira</NEWNATIONS></WORLD>"
    assert parse_new_nations(xml) == ["testlandia", "outra_nacao", "terceira"]


def test_parse_flag_url_returns_text() -> None:
    xml = '<NATION id="alvo"><FLAG>https://www.nationstates.net/images/flags/Portugal.svg</FLAG></NATION>'
    assert parse_flag_url(xml) == "https://www.nationstates.net/images/flags/Portugal.svg"


def test_parse_flag_url_missing_field_raises() -> None:
    with pytest.raises(ShardParseError):
        parse_flag_url('<NATION id="alvo"></NATION>')


def test_flag_matches_presets_exact_name() -> None:
    url = "https://www.nationstates.net/images/flags/Portugal.svg"
    assert flag_matches_presets(url, ["Portugal"]) is True


def test_flag_matches_presets_ignores_case_and_separators() -> None:
    url = "https://www.nationstates.net/images/flags/CapeVerde.svg"
    assert flag_matches_presets(url, ["cape-verde"]) is True


def test_flag_matches_presets_false_for_upload() -> None:
    url = "https://www.nationstates.net/images/flags/uploads/testlandia__853435.png"
    assert flag_matches_presets(url, ["Portugal"]) is False


def test_flag_matches_presets_false_when_no_names_configured() -> None:
    url = "https://www.nationstates.net/images/flags/Portugal.svg"
    assert flag_matches_presets(url, []) is False
