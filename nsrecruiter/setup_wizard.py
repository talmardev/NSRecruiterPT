"""Assistente de configuracao interativo (primeira execucao, ou `--setup` a pedido).

Corre antes do dashboard: o Live do Rich e o Prompt interativo nao convivem bem
no mesmo ecra, por isso este e um ecra sequencial simples que termina, grava o
.env, e so depois é que o dashboard arranca.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from nsrecruiter.security import mask_secret

console = Console()


@dataclass
class _Field:
    key: str
    label: str
    explanation: str
    secret: bool = False
    optional: bool = False


_FIELDS = [
    _Field(
        "NS_REGION", "Nome da regiao",
        "O nome exato da tua regiao no NationStates (por exemplo, Portugal).",
    ),
    _Field(
        "NS_NATION", "Nacao responsavel",
        "A nacao que gere este script -- normalmente a tua, com autoridade de "
        "Communications na regiao. Entra no User-Agent enviado em todos os pedidos.",
    ),
    _Field(
        "NS_CONTACT", "Contacto (opcional)",
        "Um email ou outra forma de te contactarem se algo correr mal com o script. "
        "Podes deixar em branco -- a nacao acima ja chega para a API nao devolver 403.",
        optional=True,
    ),
    _Field(
        "NS_CLIENT_KEY", "Client Key",
        "Gerada por um oficial com autoridade de Communications na pagina de Regional "
        "Control da regiao. Uma unica chave por regiao, partilhada por todos os scripts "
        "que a usem -- por isso o rate limit tambem e partilhado.",
        secret=True,
    ),
    _Field(
        "NS_TELEGRAM_ID", "TGID",
        "O ID do telegrama-modelo. Obtens isto (junto com a Secret Key) ao enviares esse "
        "telegrama para a nacao tag:api, marcado como 'recruitment' ao compor.",
    ),
    _Field(
        "NS_SECRET_KEY", "Secret Key",
        "A segunda parte do que recebes ao enviar o telegrama para tag:api. "
        "NUNCA a partilhes com ninguem -- quem a tiver pode enviar o teu telegrama a quem quiser.",
        secret=True,
    ),
]


def _read_existing_values(env_path: Path) -> dict[str, str]:
    """Le os valores ja presentes no .env, sem tocar em os.environ do processo atual."""
    if not env_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            values[key] = value
    return values


def _prompt_field(field: _Field, existing_value: str | None) -> str:
    console.print()
    console.print(Panel(field.explanation, title=field.label, border_style="grey50"))

    if field.secret:
        if existing_value:
            console.print(f"Valor atual: [dim]{mask_secret(existing_value)}[/dim] (Enter para manter)")
        while True:
            typed = Prompt.ask(field.label, password=True, default="", show_default=False).strip()
            if typed:
                return typed
            if existing_value:
                return existing_value
            console.print("[red]Este campo e obrigatorio.[/red]")

    default = existing_value or ""
    while True:
        typed = Prompt.ask(field.label, default=default).strip()
        if typed or field.optional:
            return typed
        console.print("[red]Este campo e obrigatorio.[/red]")


def _print_summary(collected: dict[str, str]) -> None:
    table = Table(title="Confirma os valores", show_header=False, border_style="grey50")
    table.add_column(style="bold")
    table.add_column()
    for field in _FIELDS:
        value = collected[field.key]
        if not value:
            display = "(em branco)"
        elif field.secret:
            display = mask_secret(value)
        else:
            display = value
        table.add_row(field.label, display)
    console.print()
    console.print(table)


def _format_env_value(value: str) -> str:
    if value == "" or "#" in value or value != value.strip():
        return f'"{value}"'
    return value


def _write_env_file(env_path: Path, values: dict[str, str]) -> None:
    example_path = env_path.parent / ".env.example"
    base_path = env_path if env_path.is_file() else example_path

    if not base_path.is_file():
        lines = [f"{key}={_format_env_value(value)}" for key, value in values.items()]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    output_lines: list[str] = []
    written_keys: set[str] = set()
    for raw_line in base_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                output_lines.append(f"{key}={_format_env_value(values[key])}")
                written_keys.add(key)
                continue
        output_lines.append(raw_line)

    for key, value in values.items():
        if key not in written_keys:
            output_lines.append(f"{key}={_format_env_value(value)}")

    env_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def run_setup_wizard(env_path: Path) -> bool:
    """Corre o assistente interativo. Devolve True se a configuracao foi gravada."""
    existing = _read_existing_values(env_path)

    console.print(
        Panel(
            "Vamos configurar o NSRecruiter. Nada disto sai do teu computador -- "
            f"fica guardado em [bold]{env_path}[/bold], que nunca deve ser commitado "
            "nem partilhado.",
            title="Configuracao do NSRecruiter",
            border_style="grey50",
        )
    )

    collected: dict[str, str] = {}
    for field in _FIELDS:
        collected[field.key] = _prompt_field(field, existing.get(field.key))

    _print_summary(collected)
    console.print()
    if not Confirm.ask("Gravar esta configuracao?", default=True):
        console.print("[yellow]Configuracao cancelada -- nada foi alterado.[/yellow]")
        return False

    _write_env_file(env_path, collected)
    console.print(f"[green]Configuracao gravada em {env_path}.[/green]")
    return True
