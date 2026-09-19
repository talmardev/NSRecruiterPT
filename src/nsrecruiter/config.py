"""Configuracao da aplicacao, carregada a partir de variaveis de ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nsrecruiter.dotenv_loader import load_dotenv
from nsrecruiter.security import mask_secret

APP_NAME = "NSRecruiter"
APP_VERSION = "0.1.0"
MIN_SEND_INTERVAL_SECONDS = 180.0
DEFAULT_SEND_INTERVAL_SECONDS = 182.0

# Opcoes oferecidas no assistente de configuracao para o intervalo de backup.
INTERVALOS_BACKUP_PERMITIDOS_HORAS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 12.0, 24.0)
INTERVALO_BACKUP_OMISSAO_HORAS = 24.0

_REQUIRED_VARS = (
    "NS_REGION",
    "NS_NATION",
    "NS_CLIENT_KEY",
    "NS_TELEGRAM_ID",
    "NS_SECRET_KEY",
)


class ConfigError(Exception):
    """Configuracao em falta ou invalida."""


@dataclass(frozen=True, repr=False)
class Config:
    region: str
    nation: str
    contact: str | None
    client_key: str
    telegram_id: str
    secret_key: str
    send_interval_seconds: float
    db_path: Path
    lockfile_path: Path
    log_dir: Path
    log_level: str
    priority_flag_countries: tuple[str, ...] = ()
    pasta_backup: Path | None = None
    intervalo_backup_horas: float | None = None

    @property
    def user_agent(self) -> str:
        contact_part = f"; contact:{self.contact}" if self.contact else ""
        return f"{APP_NAME}/{APP_VERSION} (nation:{self.nation}{contact_part})"

    def masked_summary(self) -> dict[str, str]:
        """Valores seguros para mostrar em logs ou no dashboard."""
        return {
            "region": self.region,
            "nation": self.nation,
            "client_key": mask_secret(self.client_key),
            "telegram_id": mask_secret(self.telegram_id),
            "secret_key": mask_secret(self.secret_key),
        }

    def __repr__(self) -> str:
        # Nunca usar o repr automatico do dataclass: apareceria em tracebacks
        # nao tratados e exporia client_key/secret_key em texto simples.
        masked = self.masked_summary()
        return (
            f"Config(region={masked['region']!r}, nation={masked['nation']!r}, "
            f"client_key={masked['client_key']!r}, telegram_id={masked['telegram_id']!r}, "
            f"secret_key={masked['secret_key']!r}, "
            f"send_interval_seconds={self.send_interval_seconds!r})"
        )


def find_env_file(start: Path | None = None) -> Path:
    return (start or Path.cwd()) / ".env"


def load_config(env_path: Path | None = None) -> Config:
    """Carrega e valida a configuracao. Lanca ConfigError com mensagem clara se faltar algo."""
    load_dotenv(env_path or find_env_file())

    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name, "").strip()]
    if missing:
        lista = ", ".join(missing)
        raise ConfigError(
            f"Faltam variaveis obrigatorias no .env: {lista}. "
            "Copia .env.example para .env e preenche estes valores."
        )

    send_interval_raw = os.environ.get("SEND_INTERVAL_SECONDS", str(DEFAULT_SEND_INTERVAL_SECONDS))
    try:
        send_interval_seconds = float(send_interval_raw)
    except ValueError as exc:
        raise ConfigError(
            f"SEND_INTERVAL_SECONDS tem de ser um numero (valor atual: '{send_interval_raw}')."
        ) from exc

    if send_interval_seconds < MIN_SEND_INTERVAL_SECONDS:
        raise ConfigError(
            f"SEND_INTERVAL_SECONDS nao pode ser inferior a {MIN_SEND_INTERVAL_SECONDS:.0f}s: "
            "e o limite da plataforma para telegramas de recrutamento. O valor recomendado e 182."
        )

    priority_flags_raw = os.environ.get("PRIORITY_FLAG_COUNTRIES", "")
    priority_flag_countries = tuple(
        name.strip() for name in priority_flags_raw.split(",") if name.strip()
    )

    pasta_backup_bruta = (os.environ.get("PASTA_BACKUP") or "").strip()
    pasta_backup = Path(pasta_backup_bruta) if pasta_backup_bruta else None

    intervalo_backup_horas: float | None = None
    if pasta_backup is not None:
        intervalo_backup_bruto = (os.environ.get("INTERVALO_BACKUP_HORAS") or "").strip()
        if not intervalo_backup_bruto:
            intervalo_backup_horas = INTERVALO_BACKUP_OMISSAO_HORAS
        else:
            try:
                intervalo_backup_valor = float(intervalo_backup_bruto)
            except ValueError as exc:
                raise ConfigError(
                    f"INTERVALO_BACKUP_HORAS tem de ser um numero (valor atual: '{intervalo_backup_bruto}')."
                ) from exc
            if intervalo_backup_valor not in INTERVALOS_BACKUP_PERMITIDOS_HORAS:
                opcoes = ", ".join(str(int(h)) for h in INTERVALOS_BACKUP_PERMITIDOS_HORAS)
                raise ConfigError(
                    f"INTERVALO_BACKUP_HORAS tem de ser um destes valores: {opcoes} "
                    f"(valor atual: '{intervalo_backup_bruto}')."
                )
            intervalo_backup_horas = intervalo_backup_valor

    return Config(
        region=os.environ["NS_REGION"].strip(),
        nation=os.environ["NS_NATION"].strip(),
        contact=(os.environ.get("NS_CONTACT") or "").strip() or None,
        client_key=os.environ["NS_CLIENT_KEY"].strip(),
        telegram_id=os.environ["NS_TELEGRAM_ID"].strip(),
        secret_key=os.environ["NS_SECRET_KEY"].strip(),
        send_interval_seconds=send_interval_seconds,
        db_path=Path(os.environ.get("DB_PATH", "data/nsrecruiter.db")),
        lockfile_path=Path(os.environ.get("LOCKFILE_PATH", "data/nsrecruiter.lock")),
        log_dir=Path(os.environ.get("LOG_DIR", "data/logs")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        priority_flag_countries=priority_flag_countries,
        pasta_backup=pasta_backup,
        intervalo_backup_horas=intervalo_backup_horas,
    )
