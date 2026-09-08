"""Shards publicos do api.cgi usados pelo coletor e pelo validador."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from nsrecruiter.api.client import NsApiClient
from nsrecruiter.models import normalize_nation


class ShardParseError(Exception):
    """A resposta da API veio num formato inesperado."""


def parse_tgcanrecruit(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ShardParseError(f"XML invalido em tgcanrecruit: {exc}") from exc
    element = root.find("TGCANRECRUIT")
    if element is None or element.text is None:
        raise ShardParseError("Resposta tgcanrecruit sem o campo esperado.")
    return element.text.strip() == "1"


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


async def fetch_region_nations(client: NsApiClient, region: str) -> set[str]:
    response = await client.request({"region": region, "q": "nations"})
    return parse_region_nations(response.text)


async def fetch_new_nations(client: NsApiClient) -> list[str]:
    response = await client.request({"q": "newnations"})
    return parse_new_nations(response.text)
