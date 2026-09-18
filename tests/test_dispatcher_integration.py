"""Testes do emissor de ponta a ponta, com toda a rede mockada (nunca a API real)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from nsrecruiter import db
from nsrecruiter import dispatcher as dispatcher_module
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter, TelegramRateLimiter
from nsrecruiter.config import Config
from nsrecruiter.dispatcher import run_dispatcher
from nsrecruiter.models import TargetSource
from nsrecruiter.runtime_status import RuntimeStatus
from nsrecruiter.utils import utc_now_iso


def _config(tmp_path: Path, send_interval_seconds: float) -> Config:
    return Config(
        region="minharegiao",
        nation="Teste",
        contact=None,
        client_key="clientkey",
        telegram_id="123",
        secret_key="secretkey",
        send_interval_seconds=send_interval_seconds,
        db_path=tmp_path / "test.db",
        lockfile_path=tmp_path / "test.lock",
        log_dir=tmp_path / "logs",
        log_level="INFO",
    )


def _seed_one_queued_target(connection, nation_id: str = "alvo") -> None:
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, now)
    db.mark_target_queued(connection, nation_id, now)


async def _run_until(condition, timeout_seconds: float = 5.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(step)
    return False


def test_dispatcher_sends_and_marks_target_sent(tmp_path: Path) -> None:
    sent_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "q=tgcanrecruit" in str(request.url):
            return httpx.Response(200, content=b'<NATION id="alvo"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')
        if dict(request.url.params).get("a") == "sendTG":
            sent_requests.append(request)
            return httpx.Response(200, content=b"")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            reached = await _run_until(
                lambda: connection.execute(
                    "SELECT status FROM targets WHERE nation_id = 'alvo'"
                ).fetchone()["status"]
                == "sent"
            )
            assert reached, "o alvo nunca chegou a 'sent'"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

        assert len(sent_requests) == 1

    asyncio.run(scenario())


def test_dispatcher_dry_run_never_calls_sendtg(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "q=tgcanrecruit" in str(request.url):
            return httpx.Response(200, content=b'<NATION id="alvo"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')
        if dict(request.url.params).get("a") == "sendTG":
            pytest.fail("dry-run nao pode chamar a=sendTG")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=True)
        )
        try:
            # Em dry-run o alvo nunca muda de estado na base de dados: so o log e que
            # mostra o que teria acontecido. Confirma-se aqui que fica mesmo "queued".
            await asyncio.sleep(0.3)
            row = connection.execute("SELECT status FROM targets WHERE nation_id = 'alvo'").fetchone()
            assert row["status"] == "queued"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    asyncio.run(scenario())


def test_dispatcher_does_not_send_while_paused_then_sends_after_resume(tmp_path: Path) -> None:
    send_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal send_count
        if "q=tgcanrecruit" in str(request.url):
            return httpx.Response(200, content=b'<NATION id="alvo"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')
        if dict(request.url.params).get("a") == "sendTG":
            send_count += 1
            return httpx.Response(200, content=b"")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()
        runtime_status.toggle_pause()
        assert runtime_status.paused

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            await asyncio.sleep(0.3)
            assert send_count == 0, "nao deveria enviar nada enquanto pausado"

            runtime_status.toggle_pause()
            assert not runtime_status.paused
            reached = await _run_until(
                lambda: connection.execute(
                    "SELECT status FROM targets WHERE nation_id = 'alvo'"
                ).fetchone()["status"]
                == "sent"
            )
            assert reached, "o alvo nunca chegou a 'sent' depois de retomar"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    asyncio.run(scenario())


def test_dispatcher_stops_after_shutdown_requested_without_starting_new_send(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "q=tgcanrecruit" in str(request.url):
            return httpx.Response(200, content=b'<NATION id="alvo"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')
        if dict(request.url.params).get("a") == "sendTG":
            return httpx.Response(200, content=b"")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # Cooldown longo: o alvo fica queued mas o emissor nunca chega a tentar enviar.
        tg_limiter = TelegramRateLimiter(interval_seconds=60.0, clock=time.monotonic)
        tg_limiter.mark_sent()
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path, send_interval_seconds=60.0)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            await asyncio.sleep(0.1)
            runtime_status.request_shutdown()
            await asyncio.wait_for(task, timeout=5.0)
            row = connection.execute("SELECT status FROM targets WHERE nation_id = 'alvo'").fetchone()
            assert row["status"] == "queued", "nao deveria ter tentado enviar depois do pedido de paragem"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    asyncio.run(scenario())


def test_dispatcher_rejects_target_that_fails_final_revalidation(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "q=tgcanrecruit" in str(request.url):
            return httpx.Response(200, content=b'<NATION id="alvo"><TGCANRECRUIT>0</TGCANRECRUIT></NATION>')
        if dict(request.url.params).get("a") == "sendTG":
            pytest.fail("nao deveria enviar para um alvo que reprovou a revalidacao")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            reached = await _run_until(
                lambda: connection.execute(
                    "SELECT status FROM targets WHERE nation_id = 'alvo'"
                ).fetchone()["status"]
                == "rejected"
            )
            assert reached, "o alvo nunca chegou a 'rejected'"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

    asyncio.run(scenario())


def test_dispatcher_backs_off_and_gives_up_on_persistent_parse_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uma resposta XML sempre invalida (ex: pagina de erro em vez de XML) nao pode
    prender o emissor a martelar o mesmo alvo sem pausa: isso esgota o limite geral
    partilhado e bloqueia ate os envios a outras nacoes. Tem de recuar com backoff e
    desistir (rejeitar) ao fim de _MAX_REVALIDATION_ATTEMPTS, libertando a fila."""
    monkeypatch.setattr(dispatcher_module, "_BACKOFF_SCHEDULE_SECONDS", (0.01, 0.01, 0.01))

    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        if "q=tgcanrecruit" in str(request.url):
            request_count += 1
            return httpx.Response(200, content=b'<NATION id="alvo"></NATION>\njunk trailing content\n')
        if dict(request.url.params).get("a") == "sendTG":
            pytest.fail("nao deveria enviar para um alvo que nunca passou a revalidacao")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            reached = await _run_until(
                lambda: connection.execute(
                    "SELECT status FROM targets WHERE nation_id = 'alvo'"
                ).fetchone()["status"]
                == "rejected"
            )
            assert reached, "o alvo nunca foi rejeitado apos esgotar as tentativas"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

        assert request_count == 3, f"esperava exatamente {3} tentativas antes de desistir, teve {request_count}"

    asyncio.run(scenario())


def test_dispatcher_rejects_immediately_on_404_without_retrying(tmp_path: Path) -> None:
    """Um 404 (nacao deixou de existir entretanto) e permanente, ao contrario de XML
    invalido ou erro de rede, nao deve gastar as tentativas com backoff antes de rejeitar."""
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        if "q=tgcanrecruit" in str(request.url):
            request_count += 1
            return httpx.Response(404, content=b'<h1>Unknown nation: "alvo".</h1>')
        if dict(request.url.params).get("a") == "sendTG":
            pytest.fail("nao deveria enviar para um alvo que nao existe")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_one_queued_target(connection)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.05, clock=time.monotonic)
        config = _config(tmp_path, send_interval_seconds=0.05)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            reached = await _run_until(
                lambda: connection.execute(
                    "SELECT status FROM targets WHERE nation_id = 'alvo'"
                ).fetchone()["status"]
                == "rejected"
            )
            assert reached, "o alvo nunca foi rejeitado apos o 404"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

        assert request_count == 1, f"nao deveria repetir um 404 (permanente), teve {request_count} pedidos"

    asyncio.run(scenario())


def test_dispatcher_respects_a_pin_made_during_the_cooldown_wait(tmp_path: Path) -> None:
    """O alvo tem de ser escolhido depois de esperar o cooldown, nao antes: senao um
    pin feito enquanto o emissor esta a espera (ate send_interval_seconds) e ignorado no
    envio prestes a acontecer, e so faz efeito no seguinte (bug reportado pelo utilizador:
    'afixei uma nacao no topo e o script enviou o telegrama a outra')."""
    sent_to: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "q=tgcanrecruit" in url:
            return httpx.Response(200, content=b"<NATION><TGCANRECRUIT>1</TGCANRECRUIT></NATION>")
        if dict(request.url.params).get("a") == "sendTG":
            sent_to.append(dict(request.url.params)["to"])
            return httpx.Response(200, content=b"")
        return httpx.Response(200, content=b"<UNKNOWN/>")

    async def scenario() -> None:
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        # "primeiro" tem queued_at mais antigo; sem pin, seria sempre o proximo por FIFO.
        db.insert_discovered_target(connection, "primeiro", "primeiro", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
        db.mark_target_queued(connection, "primeiro", "2026-09-08T00:00:00+00:00")
        db.insert_discovered_target(connection, "segundo", "segundo", TargetSource.SSE, "2026-09-08T00:00:01+00:00")
        db.mark_target_queued(connection, "segundo", "2026-09-08T00:00:01+00:00")

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        tg_limiter = TelegramRateLimiter(interval_seconds=0.3, clock=time.monotonic)
        tg_limiter.mark_sent()  # forca esperar o cooldown completo antes do 1o envio
        config = _config(tmp_path, send_interval_seconds=0.3)
        runtime_status = RuntimeStatus()

        task = asyncio.create_task(
            run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run=False)
        )
        try:
            # Da tempo ao emissor de entrar em _wait_for_cooldown antes de fixar "segundo".
            await asyncio.sleep(0.1)
            db.toggle_target_pin(connection, "segundo", utc_now_iso())

            reached = await _run_until(lambda: len(sent_to) >= 1)
            assert reached, "nenhum envio aconteceu"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await http_client.aclose()
            connection.close()

        assert sent_to == ["segundo"], f"devia ter enviado ao alvo fixado durante a espera, enviou a {sent_to}"

    asyncio.run(scenario())
