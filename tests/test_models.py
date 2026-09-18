"""Testes dos tipos e utilitarios de dominio partilhados."""
from __future__ import annotations

from nsrecruiter.models import extract_name_base, normalize_nation, significant_name_tokens


def test_normalize_nation_lowercases_and_replaces_spaces() -> None:
    assert normalize_nation("  New Libertalia Kingdom  ") == "new_libertalia_kingdom"


def test_extract_name_base_strips_trailing_digits() -> None:
    assert extract_name_base("yamagoochie0065") == "yamagoochie"
    assert extract_name_base("yamagoochie65") == "yamagoochie"
    assert extract_name_base("yamagoochie0066") == "yamagoochie"


def test_extract_name_base_leaves_names_without_trailing_digits_unchanged() -> None:
    assert extract_name_base("new_libertalia_kingdom") == "new_libertalia_kingdom"


def test_extract_name_base_never_returns_empty_string() -> None:
    assert extract_name_base("12345") == "12345"


def test_extract_name_base_remove_direcao_cardinal_no_inicio() -> None:
    assert extract_name_base("north_atlantis") == "atlantis"
    assert extract_name_base("west_atlantis") == "atlantis"
    assert extract_name_base("south_atlantis") == "atlantis"
    assert extract_name_base("east_atlantis") == "atlantis"


def test_extract_name_base_remove_direcao_cardinal_no_fim() -> None:
    assert extract_name_base("atlantis_north") == "atlantis"


def test_extract_name_base_remove_direcao_e_digitos_finais_em_conjunto() -> None:
    assert extract_name_base("north_atlantis65") == "atlantis"


def test_extract_name_base_mantem_palavra_de_direcao_sozinha() -> None:
    # Nao ha nada para alem da direcao: esvaziar a base tornaria o MIN_NAME_BASE_LENGTH
    # inutil (qualquer nome curto colidiria com "").
    assert extract_name_base("north") == "north"


def test_extract_name_base_nao_remove_direcao_dentro_de_nome_mais_longo() -> None:
    # "Northland" nao tem "_" a separar: e um token so, nao uma direcao isolada.
    assert extract_name_base("northland") == "northland"


def test_significant_name_tokens_drops_digits_and_short_words() -> None:
    assert significant_name_tokens("2018_azerbaijan_grand_prix") == {"azerbaijan", "grand", "prix"}


def test_significant_name_tokens_finds_the_shared_words_across_variants() -> None:
    tokens_a = significant_name_tokens("2018_azerbaijan_grand_prix")
    tokens_b = significant_name_tokens("2018_german_grand_prix")
    assert tokens_a & tokens_b == {"grand", "prix"}


def test_significant_name_tokens_treats_underscoreless_name_as_one_token() -> None:
    assert significant_name_tokens("yamagoochie0065") == {"yamagoochie0065"}


def test_significant_name_tokens_empty_when_all_words_too_short() -> None:
    assert significant_name_tokens("a_of_the") == set()
