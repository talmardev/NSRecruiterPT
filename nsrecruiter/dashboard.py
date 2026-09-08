"""Dashboard ao vivo em Rich (Live + Layout), atualizado a cada segundo.

Sem emojis, cores sobrias, alinhamento consistente. Todos os numeros que mudam
de valor usam largura fixa (zero-padding) para nao "saltarem" visualmente.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter, TelegramRateLimiter
from nsrecruiter.collector import force_refresh
from nsrecruiter.config import Config
from nsrecruiter.keyboard import read_keys
from nsrecruiter.logging_setup import LogEntry
from nsrecruiter.models import AppState, TargetStatus
from nsrecruiter.runtime_status import RuntimeStatus

logger = logging.getLogger("nsrecruiter.dashboard")

_REFRESH_INTERVAL_SECONDS = 1.0

_STATE_LABELS = {
    AppState.SENDING: "A ENVIAR",
    AppState.WAITING: "EM ESPERA",
    AppState.PAUSED: "PAUSADO",
    AppState.BLOCKED: "BLOQUEADO",
    AppState.RATE_LIMITED: "LIMITADO",
}
_STATE_COLORS = {
    AppState.SENDING: "green",
    AppState.WAITING: "grey70",
    AppState.PAUSED: "yellow",
    AppState.BLOCKED: "orange3",
    AppState.RATE_LIMITED: "red",
}
_LEVEL_COLORS = {
    "DEBUG": "grey50",
    "INFO": "grey93",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold red reverse",
}
_LABEL_WIDTH = 9  # a maior etiqueta de estado ("BLOQUEADO"/"EM ESPERA") tem 9 letras


@dataclass
class DashboardState:
    region: str
    nation: str
    masked_client_key: str
    dry_run: bool

    app_state: AppState
    uptime_seconds: float

    seconds_until_next_send: float
    send_interval_seconds: float

    general_remaining: int | None
    general_limit: int | None
    general_wait_seconds: float

    blocked_or_limited_seconds: float
    blocked_or_limited_reason: str | None

    sent_today: int
    sent_total: int
    attempts_total: int
    queue_size: int
    rejected_total: int
    sent_last_hour: int
    projection_24h: int
    failure_breakdown: list[tuple[str, int]]
    rejection_breakdown: list[tuple[str, int]]

    log_entries: list[LogEntry]


def _format_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _limit_color(remaining: int | None, limit: int | None) -> str:
    if remaining is None or not limit:
        return "grey70"
    fraction = remaining / limit
    if fraction > 0.5:
        return "green"
    if fraction > 0.2:
        return "yellow"
    return "red"


_BAR_WIDTH = 40
_BAR_FILLED_CHAR = "#"
_BAR_EMPTY_CHAR = "-"


def _make_bar(fraction: float, style: str) -> Text:
    """Barra em caracteres, nao rich.progress_bar.ProgressBar: precisa de ser legivel mesmo
    sem cor (ficheiro de log, terminal sem TTY a serio) e de largura sempre previsivel."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * _BAR_WIDTH)
    bar = Text()
    bar.append(_BAR_FILLED_CHAR * filled, style=style)
    bar.append(_BAR_EMPTY_CHAR * (_BAR_WIDTH - filled), style="grey35")
    return bar


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def compute_app_state(runtime_status: RuntimeStatus, general_limiter: GeneralRateLimiter) -> AppState:
    if runtime_status.paused:
        return AppState.PAUSED
    if general_limiter.wait_time() > 0:
        return AppState.RATE_LIMITED
    if runtime_status.is_blocked_externally():
        return AppState.BLOCKED
    if runtime_status.currently_dispatching:
        return AppState.SENDING
    return AppState.WAITING


def gather_state(
    connection: sqlite3.Connection,
    config: Config,
    general_limiter: GeneralRateLimiter,
    tg_limiter: TelegramRateLimiter,
    runtime_status: RuntimeStatus,
    log_entries: list[LogEntry],
    start_time: float,
    dry_run: bool,
) -> DashboardState:
    sent_today = db.count_sent_since(connection, _today_start_iso())
    sent_total = db.count_sent_total(connection)
    attempts_total = db.count_send_attempts_total(connection)
    queue_size = db.count_targets_by_status(connection, TargetStatus.QUEUED)
    rejected_total = db.count_targets_by_status(connection, TargetStatus.REJECTED)
    sent_last_hour = db.count_sent_since(connection, _hours_ago_iso(1))

    success_rate = (sent_total / attempts_total) if attempts_total else None
    theoretical_max_24h = int(86400 / config.send_interval_seconds)
    projection_24h = (
        round(theoretical_max_24h * success_rate) if success_rate is not None else theoretical_max_24h
    )

    app_state = compute_app_state(runtime_status, general_limiter)
    blocked_seconds = 0.0
    blocked_reason = None
    if app_state is AppState.RATE_LIMITED:
        blocked_seconds = general_limiter.wait_time()
        blocked_reason = "limite geral"
    elif app_state is AppState.BLOCKED:
        blocked_seconds = tg_limiter.seconds_until_next_send()
        blocked_reason = "uso externo da chave"

    return DashboardState(
        region=config.region,
        nation=config.nation,
        masked_client_key=config.masked_summary()["client_key"],
        dry_run=dry_run,
        app_state=app_state,
        uptime_seconds=time.monotonic() - start_time,
        seconds_until_next_send=tg_limiter.seconds_until_next_send(),
        send_interval_seconds=config.send_interval_seconds,
        general_remaining=general_limiter.remaining,
        general_limit=general_limiter.limit,
        general_wait_seconds=general_limiter.wait_time(),
        blocked_or_limited_seconds=blocked_seconds,
        blocked_or_limited_reason=blocked_reason,
        sent_today=sent_today,
        sent_total=sent_total,
        attempts_total=attempts_total,
        queue_size=queue_size,
        rejected_total=rejected_total,
        sent_last_hour=sent_last_hour,
        projection_24h=projection_24h,
        failure_breakdown=[(row["reason"], row["n"]) for row in db.failure_reason_breakdown(connection)],
        rejection_breakdown=[(row["reason"], row["n"]) for row in db.rejection_reason_breakdown(connection)],
        log_entries=list(log_entries),
    )


def _render_header(state: DashboardState) -> Panel:
    label = _STATE_LABELS[state.app_state].ljust(_LABEL_WIDTH)
    color = _STATE_COLORS[state.app_state]

    line1 = Text()
    line1.append("Regiao: ", style="bold")
    line1.append(f"{state.region}    ")
    line1.append("Nacao: ", style="bold")
    line1.append(state.nation)
    if state.dry_run:
        line1.append("    [MODO DRY-RUN]", style="bold cyan")

    line2 = Text()
    line2.append("Estado: ", style="bold")
    line2.append(label, style=f"bold {color}")
    line2.append(f"   Uptime: {_format_duration(state.uptime_seconds)}   Chave: {state.masked_client_key}")

    return Panel(Group(line1, line2), title="NSRecruiter", border_style="grey50")


def _render_limits(state: DashboardState) -> Panel:
    # As barras ficam em linha propria (nao lado a lado com o texto): a meio painel
    # partilhado com "Estatisticas" nao sobra largura para uma barra legivel ao lado do rotulo.
    interval = max(state.send_interval_seconds, 1.0)
    cooldown_fraction = max(0.0, interval - state.seconds_until_next_send) / interval
    cooldown_bar = _make_bar(cooldown_fraction, style="cyan" if cooldown_fraction < 1.0 else "green")

    limit = state.general_limit or 50
    remaining = state.general_remaining if state.general_remaining is not None else limit
    color = _limit_color(state.general_remaining, state.general_limit)
    general_bar = _make_bar(remaining / limit if limit else 0.0, style=color)
    remaining_text = f"{remaining}/{limit}" if state.general_remaining is not None else "sem dados ainda"

    if state.blocked_or_limited_reason:
        retry_line = Text(
            f"Retry-After ativo: EM ESPERA ({state.blocked_or_limited_reason}): "
            f"{_format_mmss(state.blocked_or_limited_seconds)}",
            style="bold yellow",
        )
    else:
        retry_line = Text("Retry-After ativo: nenhuma", style="dim")

    group = Group(
        Text(f"Proximo envio em: {_format_mmss(state.seconds_until_next_send)}", style="bold"),
        cooldown_bar,
        Text(""),
        Text(f"Limite geral: {remaining_text}", style="bold"),
        general_bar,
        Text(""),
        retry_line,
    )
    return Panel(group, title="Limites", border_style="grey50")


def _render_stats(state: DashboardState) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")

    success_rate_text = (
        f"{(state.sent_total / state.attempts_total) * 100:.0f}%" if state.attempts_total else "sem dados"
    )

    table.add_row("Enviados hoje / total:", f"{state.sent_today} / {state.sent_total}")
    table.add_row("Taxa de sucesso:", success_rate_text)
    table.add_row("Tamanho da fila:", str(state.queue_size))
    table.add_row("Rejeitados (tgcanrecruit):", str(state.rejected_total))
    table.add_row("Ritmo (ultima hora):", f"{state.sent_last_hour}/h")
    table.add_row("Projecao proximas 24h:", f"~{state.projection_24h}")

    if state.failure_breakdown:
        table.add_row("", "")
        table.add_row("Falhas por categoria:", "")
        for reason, count in state.failure_breakdown[:4]:
            table.add_row(f"  {reason[:38]}", str(count))

    return Panel(table, title="Estatisticas", border_style="grey50")


def _render_log(state: DashboardState) -> Panel:
    lines: list[Text] = []
    for entry in state.log_entries:
        style = _LEVEL_COLORS.get(entry.level, "grey93")
        text = Text()
        text.append(f"{entry.timestamp} ", style="grey50")
        text.append(f"{entry.level:<8} ", style=style)
        text.append(entry.message, style=style)
        lines.append(text)
    if not lines:
        lines.append(Text("(sem atividade ainda)", style="dim"))
    return Panel(Group(*lines), title="Log", border_style="grey50")


def _render_footer() -> Panel:
    text = Text()
    text.append(" p ", style="bold black on grey70")
    text.append(" pausar/retomar     ")
    text.append(" r ", style="bold black on grey70")
    text.append(" atualizar fila agora     ")
    text.append(" q ", style="bold black on grey70")
    text.append(" sair (termina o envio em curso)")
    return Panel(text, border_style="grey50")


def build_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(name="log", size=17),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(Layout(name="limits"), Layout(name="stats"))
    return layout


def render(layout: Layout, state: DashboardState) -> None:
    layout["header"].update(_render_header(state))
    layout["limits"].update(_render_limits(state))
    layout["stats"].update(_render_stats(state))
    layout["log"].update(_render_log(state))
    layout["footer"].update(_render_footer())


async def run_dashboard(
    connection: sqlite3.Connection,
    api_client: NsApiClient,
    config: Config,
    general_limiter: GeneralRateLimiter,
    tg_limiter: TelegramRateLimiter,
    runtime_status: RuntimeStatus,
    log_buffer: "list[LogEntry]",
    start_time: float,
    dry_run: bool = False,
) -> None:
    layout = build_layout()

    def on_key(char: str) -> None:
        lowered = char.lower()
        if lowered == "q":
            logger.info("Saida pedida (tecla q); a terminar o envio em curso, se houver algum.")
            runtime_status.request_shutdown()
        elif lowered == "p":
            runtime_status.toggle_pause()
            logger.info("Pausado (tecla p)." if runtime_status.paused else "Retomado (tecla p).")
        elif lowered == "r":
            asyncio.create_task(force_refresh(connection, api_client))

    keyboard_task = asyncio.create_task(read_keys(on_key))
    try:
        with Live(layout, screen=True, auto_refresh=False) as live:
            while not runtime_status.shutdown_requested.is_set():
                state = gather_state(
                    connection, config, general_limiter, tg_limiter, runtime_status, log_buffer, start_time, dry_run
                )
                render(layout, state)
                live.refresh()
                await runtime_status.sleep_or_shutdown(_REFRESH_INTERVAL_SECONDS)
    finally:
        keyboard_task.cancel()
        await asyncio.gather(keyboard_task, return_exceptions=True)
