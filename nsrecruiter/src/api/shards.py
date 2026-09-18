"""Shards publicos do api.cgi usados pelo coletor e pelo validador."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NsApiError
from nsrecruiter.models import normalize_nation


class ShardParseError(NsApiError):
    """A resposta da API veio num formato inesperado: tratada como as outras
    falhas de API (log e nova tentativa depois), nao deve derrubar a aplicacao."""


def parse_tgcanrecruit(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ShardParseError(f"XML invalido em tgcanrecruit: {exc}") from exc
    element = root.find("TGCANRECRUIT")
    if element is None or element.text is None:
        raise ShardParseError("Resposta tgcanrecruit sem o campo esperado.")
    return element.text.strip() == "1"


def parse_flag_url(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ShardParseError(f"XML invalido em flag: {exc}") from exc
    element = root.find("FLAG")
    if element is None or element.text is None:
        raise ShardParseError("Resposta flag sem o campo esperado.")
    return element.text.strip()


def _normalize_flag_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def flag_matches_presets(flag_url: str, preset_names: Iterable[str]) -> bool:
    """True se o URL da bandeira for exatamente um dos presets indicados (por nome de
    pais), ignorando maiusculas/minusculas, espacos, underscores e hifens. Uma bandeira
    carregada (upload) nunca bate certo, porque o nome do ficheiro nao e o do pais."""
    stem = flag_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    normalized_stem = _normalize_flag_name(stem)
    return any(_normalize_flag_name(name) == normalized_stem for name in preset_names)


def parse_region_nations(xml_text: str) -> set[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ShardParseError(f"XML invalido em region nations: {exc}") from exc
    element = root.find("NATIONS")
    if element is None or not element.text:
        return set()
    return {normalize_nation(name) for name in element.text.split(":") if name.strip()}


def parse_new_nations(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ShardParseError(f"XML invalido em newnations: {exc}") from exc
    element = root.find("NEWNATIONS")
    if element is None or not element.text:
        return []
    return [name.strip() for name in element.text.split(",") if name.strip()]


async def fetch_tgcanrecruit(client: NsApiClient, nation_id: str, from_region: str) -> bool:
    response = await client.request({"nation": nation_id, "q": "tgcanrecruit", "from": from_region})
    return parse_tgcanrecruit(response.text)


async def fetch_tgcanrecruit_and_flag(
    client: NsApiClient, nation_id: str, from_region: str
) -> tuple[bool, str]:
    """Mesma validacao de sempre, mas pedindo tambem a bandeira atual na mesma chamada:
    as shards vao "boleia" uma na outra, sem gastar pedidos extra do limite geral."""
    response = await client.request(
        {"nation": nation_id, "q": "tgcanrecruit+flag", "from": from_region}
    )
    return parse_tgcanrecruit(response.text), parse_flag_url(response.text)


async def fetch_flag(client: NsApiClient, nation_id: str) -> str:
    """So a bandeira, sem tgcanrecruit: para reavaliar prioridade sem gastar uma
    validacao de recrutamento (usado no refresh manual, tecla 'r')."""
    response = await client.request({"nation": nation_id, "q": "flag"})
    return parse_flag_url(response.text)


async def fetch_region_nations(client: NsApiClient, region: str) -> set[str]:
    response = await client.request({"region": region, "q": "nations"})
    return parse_region_nations(response.text)


async def fetch_new_nations(client: NsApiClient) -> list[str]:
    response = await client.request({"q": "newnations"})
    return parse_new_nations(response.text)
