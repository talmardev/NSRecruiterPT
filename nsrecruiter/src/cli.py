"""Interface de linha de comandos do NSRecruiter."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import replace

from rich.prompt import Confirm

from nsrecruiter.config import Config, ConfigError, find_env_file, load_config
from nsrecruiter.db import connect, init_schema
from nsrecruiter.lockfile import InstanceAlreadyRunningError, LockFile
from nsrecruiter.logging_setup import new_log_buffer, setup_logging, setup_logging_for_dashboard
from nsrecruiter.runtime import run_app
from nsrecruiter.security import mask_secret
from nsrecruiter.setup_wizard import run_setup_wizard

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_LOCK_ERROR = 2

logger = logging.getLogger("nsrecruiter.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nsrecruiter",
        description="Cliente de recrutamento para a Telegrams API do NationStates.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valida a configuracao e o lockfile, e sai sem enviar telegramas. Nunca interativo.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Corre o assistente de configuracao (region, nacao, chaves) e depois arranca.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Corre o coletor, o validador e o emissor a serio, mas nunca chama a=sendTG.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Regista tambem mensagens DEBUG, no ficheiro de log e no painel de log.",
    )
    return parser


def _secrets_from_config(config: Config) -> dict[str, str]:
    return {
        config.client_key: mask_secret(config.client_key),
        config.secret_key: mask_secret(config.secret_key),
        config.telegram_id: mask_secret(config.telegram_id),
    }


def _load_config_offering_wizard_if_needed() -> Config | None:
    """Tenta carregar a config; se faltar algo e isto for um terminal interativo,
    oferece o assistente antes de desistir. Devolve None se nao ficou configurado."""
    try:
        return load_config()
    except ConfigError as exc:
        print(f"Configuracao incompleta: {exc}\n", file=sys.stderr)
        if not sys.stdin.isatty():
            return None
        if not Confirm.ask("Queres configurar agora?", default=True):
            return None
        run_setup_wizard(find_env_file())
        try:
            return load_config()
        except ConfigError as exc2:
            print(f"Configuracao ainda invalida: {exc2}", file=sys.stderr)
            return None


def run_check() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuracao invalida: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        with LockFile(config.lockfile_path):
            setup_logging(config.log_dir, config.log_level, _secrets_from_config(config))
            connection = connect(config.db_path)
            try:
                init_schema(connection)
            finally:
                connection.close()

            logger.info("Configuracao valida.")
            for field_name, value in config.masked_summary().items():
                logger.info("  %s = %s", field_name, value)
            logger.info("Base de dados pronta em %s", config.db_path)
            logger.info("Intervalo de envio configurado: %.0fs", config.send_interval_seconds)
            if config.pasta_backup is not None:
                logger.info(
                    "Backup automatico: a cada %gh, em %s.", config.intervalo_backup_horas, config.pasta_backup
                )
            else:
                logger.info("Backup automatico: desativado.")
    except InstanceAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_LOCK_ERROR

    return EXIT_OK


def run_main(dry_run: bool = False, verbose: bool = False, force_setup: bool = False) -> int:
    if force_setup:
        run_setup_wizard(find_env_file())

    config = _load_config_offering_wizard_if_needed()
    if config is None:
        return EXIT_CONFIG_ERROR

    if verbose:
        config = replace(config, log_level="DEBUG")

    try:
        with LockFile(config.lockfile_path):
            log_buffer = new_log_buffer()
            setup_logging_for_dashboard(config.log_dir, config.log_level, _secrets_from_config(config), log_buffer)
            logger.info("NSRecruiter a arrancar. Regiao: %s. Nacao: %s.", config.region, config.nation)
            try:
                asyncio.run(run_app(config, log_buffer, dry_run=dry_run))
            except KeyboardInterrupt:
                logger.info("Interrompido pelo utilizador (Ctrl+C).")
    except InstanceAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_LOCK_ERROR

    print("NSRecruiter terminado.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    return run_main(dry_run=args.dry_run, verbose=args.verbose, force_setup=args.setup)


if __name__ == "__main__":
    sys.exit(main())
