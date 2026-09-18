"""Testes do encaminhamento da CLI entre config em falta e o assistente de configuracao."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nsrecruiter import cli


def test_missing_config_in_non_interactive_context_does_not_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("nao deveria tentar correr o assistente sem terminal interativo")

    monkeypatch.setattr(cli, "run_setup_wizard", _fail_if_called)

    assert cli._load_config_offering_wizard_if_needed() is None


def test_missing_config_interactive_but_declined_does_not_run_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "Confirm", type("_C", (), {"ask": staticmethod(lambda *a, **k: False)}))

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("nao deveria correr o assistente se o utilizador disser que nao")

    monkeypatch.setattr(cli, "run_setup_wizard", _fail_if_called)

    assert cli._load_config_offering_wizard_if_needed() is None


def test_missing_config_interactive_and_accepted_runs_wizard_then_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "Confirm", type("_C", (), {"ask": staticmethod(lambda *a, **k: True)}))

    required_env = {
        "NS_REGION": "Portugal",
        "NS_NATION": "New Libertalia Kingdom",
        "NS_CLIENT_KEY": "clientkey1234567890",
        "NS_TELEGRAM_ID": "123456789",
        "NS_SECRET_KEY": "secretkeyabcdef1234567890",
    }

    def _fake_wizard(env_path: Path) -> bool:
        env_path.write_text(
            "\n".join(f"{key}={value}" for key, value in required_env.items()) + "\n", encoding="utf-8"
        )
        return True

    monkeypatch.setattr(cli, "run_setup_wizard", _fake_wizard)

    try:
        config = cli._load_config_offering_wizard_if_needed()
        assert config is not None
        assert config.region == "Portugal"
        assert config.nation == "New Libertalia Kingdom"
    finally:
        # load_config() -> load_dotenv() usa os.environ.setdefault, que o monkeypatch nao
        # rastreia (nao foi ele quem fez a mutacao). Usar monkeypatch.delenv aqui seria
        # pior do que nao limpar nada: ele fica agendado para REPOR o valor "apagado" no
        # teardown, recriando exatamente a fuga que se quer evitar. Por isso os.environ
        # diretamente, sem passar pelo monkeypatch.
        for key in required_env:
            os.environ.pop(key, None)
