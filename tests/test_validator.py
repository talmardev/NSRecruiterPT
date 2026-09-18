"""Testes do validador: bloqueio por nome e prioridade na fila por bandeira preset."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter
from nsrecruiter.config import Config
from nsrecruiter.models import TargetSource
from nsrecruiter.validator import run_validator_loop


def _config(tmp_path: Path, priority_flag_countries: tuple[str, ...] = ()) -> Config:
    return Config(
        region="minharegiao",
        nation="Teste",
        contact=None,
        client_key="x",
        telegram_id="y",
        secret_key="z",
        send_interval_seconds=182.0,
        db_path=tmp_path / "test.db",
        lockfile_path=tmp_path / "test.lock",
        log_dir=tmp_path / "logs",
        log_level="INFO",
        priority_flag_countries=priority_flag_countries,
    )


def _minutes_ago_iso(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


async def _run_until_status(connection, nation_id: str, status: str = "queued", timeout_seconds: float = 5.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        row = connection.execute(
            "SELECT * FROM targets WHERE nation_id = ?", (nation_id,)
        ).fetchone()
        if row is not None and row["status"] == status:
            return row
        await asyncio.sleep(0.02)
    return None


def _run_validator_scenario(tmp_path: Path, flag_xml: bytes, priority_flag_countries: tuple[str, ...]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=flag_xml)

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        db.insert_discovered_target(
            connection, "alvo", "Alvo", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path, priority_flag_countries=priority_flag_countries)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            return await _run_until_status(connection, "alvo", "queued")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    "nation_id", ["estado_facist", "nacao_facista", "republica_fascista", "quarto_reich_nazi"]
)
def test_validator_rejects_blocked_name_without_calling_api(tmp_path: Path, nation_id: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("nao deveria chamar a API para um nome bloqueado")

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        db.insert_discovered_target(
            connection, nation_id, nation_id, TargetSource.SSE, "2026-09-08T00:00:00+00:00"
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            return await _run_until_status(connection, nation_id, "rejected")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    row = asyncio.run(scenario())
    assert row is not None, "o alvo nunca chegou a 'rejected'"
    assert row["status_reason"] == "nome bloqueado (padrao fascista/nazi)"


def test_validator_rejects_probable_alt_but_lets_the_first_of_the_burst_through(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if dict(request.url.params).get("nation") == "yamagoochie65":
            pytest.fail("nao deveria chamar a API para o provavel alt")
        return httpx.Response(
            200, content=b'<NATION id="yamagoochie0065"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>'
        )

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # Mesma rajada do relato original: duas variantes do mesmo nome, segundos
        # depois uma da outra. A primeira tem de passar normalmente; so a segunda
        # (e seguintes) e que deve ser rejeitada como provavel alt.
        db.insert_discovered_target(
            connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
        )
        db.insert_discovered_target(
            connection, "yamagoochie65", "Yamagoochie65", TargetSource.SSE, "2026-09-08T00:00:10+00:00"
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            first = await _run_until_status(connection, "yamagoochie0065", "queued")
            second = await _run_until_status(connection, "yamagoochie65", "rejected")
            return first, second
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    first, second = asyncio.run(scenario())
    assert first is not None, "o primeiro da rajada nunca chegou a 'queued'"
    assert second is not None, "o segundo da rajada nunca chegou a 'rejected'"
    assert "provavel alt" in second["status_reason"]


def test_validator_rejeita_rajada_no_mesmo_segundo_apesar_do_discovered_at_identico(tmp_path: Path) -> None:
    """Relato original: uma rajada de nomes com sufixo numerico sequencial (ex:
    alvo177..alvo180) descoberta tao depressa que cai no mesmo segundo (resolucao do
    timestamp) passava toda ao filtro; nenhum contava o outro como "anterior"."""
    def responder(pedido: httpx.Request) -> httpx.Response:
        if dict(pedido.url.params).get("nation") == "alvo178":
            pytest.fail("nao deveria chamar a API para o provavel alt")
        return httpx.Response(200, content=b'<NATION id="alvo177"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')

    async def cenario():
        ligacao = db.connect(tmp_path / "test.db")
        db.init_schema(ligacao)
        mesmo_instante = "2026-09-08T00:00:00+00:00"
        db.insert_discovered_target(ligacao, "alvo177", "Alvo177", TargetSource.SSE, mesmo_instante)
        db.insert_discovered_target(ligacao, "alvo178", "Alvo178", TargetSource.SSE, mesmo_instante)

        cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(responder))
        cliente_api = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=cliente_http)
        configuracao = _config(tmp_path)

        tarefa = asyncio.create_task(run_validator_loop(ligacao, cliente_api, configuracao, frozenset()))
        try:
            primeiro = await _run_until_status(ligacao, "alvo177", "queued")
            segundo = await _run_until_status(ligacao, "alvo178", "rejected")
            return primeiro, segundo
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)
            await cliente_http.aclose()
            ligacao.close()

    primeiro, segundo = asyncio.run(cenario())
    assert primeiro is not None, "o primeiro da rajada nunca chegou a 'queued'"
    assert segundo is not None, "o segundo da rajada nunca chegou a 'rejected' (bug do empate de timestamp)"
    assert "provavel alt" in segundo["status_reason"]


def test_validator_rejeita_base_partilhada_que_so_difere_na_direcao_cardinal(tmp_path: Path) -> None:
    """Relato original: "North Atlantis" / "West Atlantis" / "South Atlantis" so
    partilham uma palavra ("atlantis"), o que fica abaixo do limiar de 2 palavras do
    filtro de lote gerado. Tem de ser apanhado pela base de nome, nao pelas tokens."""
    def responder(pedido: httpx.Request) -> httpx.Response:
        nacao = dict(pedido.url.params).get("nation")
        if nacao != "north_atlantis":
            pytest.fail(f"nao deveria chamar a API para o provavel alt ({nacao})")
        return httpx.Response(200, content=b'<NATION id="north_atlantis"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')

    async def cenario():
        ligacao = db.connect(tmp_path / "test.db")
        db.init_schema(ligacao)
        db.insert_discovered_target(
            ligacao, "north_atlantis", "North Atlantis", TargetSource.SSE, _minutes_ago_iso(10)
        )
        db.insert_discovered_target(
            ligacao, "west_atlantis", "West Atlantis", TargetSource.SSE, _minutes_ago_iso(5)
        )
        db.insert_discovered_target(
            ligacao, "south_atlantis", "South Atlantis", TargetSource.SSE, _minutes_ago_iso(0)
        )

        cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(responder))
        cliente_api = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=cliente_http)
        configuracao = _config(tmp_path)

        tarefa = asyncio.create_task(run_validator_loop(ligacao, cliente_api, configuracao, frozenset()))
        try:
            primeiro = await _run_until_status(ligacao, "north_atlantis", "queued")
            segundo = await _run_until_status(ligacao, "west_atlantis", "rejected")
            terceiro = await _run_until_status(ligacao, "south_atlantis", "rejected")
            return primeiro, segundo, terceiro
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)
            await cliente_http.aclose()
            ligacao.close()

    primeiro, segundo, terceiro = asyncio.run(cenario())
    assert primeiro is not None, "o primeiro da rajada nunca chegou a 'queued'"
    assert segundo is not None, "West Atlantis nunca chegou a 'rejected'"
    assert terceiro is not None, "South Atlantis nunca chegou a 'rejected'"
    assert "provavel alt" in segundo["status_reason"]
    assert "provavel alt" in terceiro["status_reason"]


def test_validator_does_not_reject_same_base_outside_lookback_window(tmp_path: Path) -> None:
    xml = b'<NATION id="yamagoochie65"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml)

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # O irmao antigo ja foi processado e enviado ha muito tempo (fora da janela de
        # deteção): so a nova descoberta (yamagoochie65) fica pendente neste teste.
        db.insert_discovered_target(
            connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, "2020-01-01T00:00:00+00:00"
        )
        db.mark_target_sent(connection, "yamagoochie0065", "2020-01-01T00:10:00+00:00")
        db.insert_discovered_target(
            connection, "yamagoochie65", "Yamagoochie65", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            return await _run_until_status(connection, "yamagoochie65", "queued")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    row = asyncio.run(scenario())
    assert row is not None, "o alvo nunca chegou a 'queued' (a janela de 30 dias devia ignorar o mais antigo)"


def test_validator_rejects_generated_batch_sharing_two_words_but_lets_first_through(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        nation = dict(request.url.params).get("nation")
        if nation == "2018_german_grand_prix":
            pytest.fail("nao deveria chamar a API para o provavel lote gerado")
        return httpx.Response(
            200, content=f'<NATION id="{nation}"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>'.encode()
        )

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # Mesmo padrao do relato: nomes de corridas de F1, sem sufixo numerico repetido
        # (o filtro de "alt" nao apanha isto), mas a partilhar "grand"+"prix".
        db.insert_discovered_target(
            connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix",
            TargetSource.SSE, _minutes_ago_iso(25),
        )
        db.insert_discovered_target(
            connection, "2018_german_grand_prix", "2018 German Grand Prix",
            TargetSource.SSE, _minutes_ago_iso(0),
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            first = await _run_until_status(connection, "2018_azerbaijan_grand_prix", "queued")
            second = await _run_until_status(connection, "2018_german_grand_prix", "rejected")
            return first, second
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    first, second = asyncio.run(scenario())
    assert first is not None, "o primeiro da leva nunca chegou a 'queued'"
    assert second is not None, "o segundo da leva nunca chegou a 'rejected'"
    assert "provavel lote gerado" in second["status_reason"]


def test_validator_does_not_reject_nation_sharing_only_one_common_word(tmp_path: Path) -> None:
    xml = b'<NATION id="a_nondescript_forest"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml)

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # Partilha so uma palavra ("forest") com um nome real tipico; nao deve chegar
        # a rejeitar, para nao punir um jogador real por coincidencia de uma palavra so.
        db.insert_discovered_target(
            connection, "a_quiet_forest", "A Quiet Forest", TargetSource.SSE, _minutes_ago_iso(5)
        )
        db.insert_discovered_target(
            connection, "a_nondescript_forest", "A Nondescript Forest", TargetSource.SSE, _minutes_ago_iso(0)
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            return await _run_until_status(connection, "a_nondescript_forest", "queued")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    row = asyncio.run(scenario())
    assert row is not None, "nao devia ter sido rejeitada por partilhar so uma palavra comum"


def test_validator_marks_priority_when_flag_matches_preset(tmp_path: Path) -> None:
    xml = (
        b'<NATION id="alvo"><FLAG>https://www.nationstates.net/images/flags/Portugal.svg</FLAG>'
        b"<TGCANRECRUIT>1</TGCANRECRUIT></NATION>"
    )
    row = _run_validator_scenario(tmp_path, xml, priority_flag_countries=("Portugal",))
    assert row is not None, "o alvo nunca chegou a 'queued'"
    assert row["priority"] == 1


def test_validator_no_priority_when_flag_is_a_custom_upload(tmp_path: Path) -> None:
    xml = (
        b'<NATION id="alvo"><FLAG>https://www.nationstates.net/images/flags/uploads/alvo__1.png</FLAG>'
        b"<TGCANRECRUIT>1</TGCANRECRUIT></NATION>"
    )
    row = _run_validator_scenario(tmp_path, xml, priority_flag_countries=("Portugal",))
    assert row is not None, "o alvo nunca chegou a 'queued'"
    assert row["priority"] == 0


def test_validator_no_priority_when_feature_disabled(tmp_path: Path) -> None:
    xml = b'<NATION id="alvo"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>'
    row = _run_validator_scenario(tmp_path, xml, priority_flag_countries=())
    assert row is not None, "o alvo nunca chegou a 'queued'"
    assert row["priority"] == 0


def test_validator_rejects_immediately_on_404_without_retrying(tmp_path: Path) -> None:
    """Um 404 (nacao ja nao existe) e permanente, ao contrario de XML invalido ou erro
    de rede, nao deve gastar as tentativas com backoff antes de rejeitar."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(404, content=b'<h1>Unknown nation: "alvo".</h1>')

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        db.insert_discovered_target(
            connection, "alvo", "Alvo", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
        )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path)

        task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))
        try:
            return await _run_until_status(connection, "alvo", "rejected")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    row = asyncio.run(scenario())
    assert row is not None, "o alvo nunca foi rejeitado apos o 404"
    assert request_count == 1, f"nao deveria repetir um 404 (permanente), teve {request_count} pedidos"
