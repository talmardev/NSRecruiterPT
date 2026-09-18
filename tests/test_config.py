"""Testes de validacao da configuracao (sem tocar no sistema de ficheiros real)."""
from __future__ import annotations

from pathlib import Path

import pytest

from nsrecruiter.config import ConfigError, load_config

REQUIRED_ENV = {
    "NS_REGION": "Testlandia Region",
    "NS_NATION": "Testlandia",
    "NS_CLIENT_KEY": "clientkey1234",
    "NS_TELEGRAM_ID": "123456789",
    "NS_SECRET_KEY": "secretkeyabcdef",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_load_config_succeeds_with_all_required_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    config = load_config(env_path=tmp_path / "no_such_file.env")
    assert config.region == "Testlandia Region"
    assert config.send_interval_seconds == 182.0


def test_load_config_fails_with_clear_message_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NS_REGION", "Testlandia Region")
    with pytest.raises(ConfigError) as excinfo:
        load_config(env_path=tmp_path / "no_such_file.env")
    message = str(excinfo.value)
    assert "NS_CLIENT_KEY" in message
    assert "NS_NATION" in message


def test_load_config_rejects_interval_below_platform_minimum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SEND_INTERVAL_SECONDS", "60")
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "no_such_file.env")


def test_load_config_backup_desativado_por_omissao(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_required_env(monkeypatch)
    config = load_config(env_path=tmp_path / "no_such_file.env")
    assert config.pasta_backup is None
    assert config.intervalo_backup_horas is None


def test_load_config_pasta_backup_sozinha_usa_intervalo_de_24h_por_omissao(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PASTA_BACKUP", str(tmp_path / "backups"))
    config = load_config(env_path=tmp_path / "no_such_file.env")
    assert config.pasta_backup == tmp_path / "backups"
    assert config.intervalo_backup_horas == 24.0


def test_load_config_aceita_um_intervalo_de_backup_permitido(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PASTA_BACKUP", str(tmp_path / "backups"))
    monkeypatch.setenv("INTERVALO_BACKUP_HORAS", "6")
    config = load_config(env_path=tmp_path / "no_such_file.env")
    assert config.intervalo_backup_horas == 6.0


def test_load_config_rejeita_intervalo_de_backup_fora_do_conjunto_permitido(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PASTA_BACKUP", str(tmp_path / "backups"))
    monkeypatch.setenv("INTERVALO_BACKUP_HORAS", "7")
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "no_such_file.env")


def test_load_config_rejeita_intervalo_de_backup_nao_numerico(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PASTA_BACKUP", str(tmp_path / "backups"))
    monkeypatch.setenv("INTERVALO_BACKUP_HORAS", "muitas")
    with pytest.raises(ConfigError):
        load_config(env_path=tmp_path / "no_such_file.env")


def test_load_config_ignora_intervalo_de_backup_quando_pasta_de_backup_esta_vazia(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # INTERVALO_BACKUP_HORAS invalido/orfao no .env nao deve bloquear o arranque se
    # os backups nem estiverem ativos (PASTA_BACKUP vazia).
    _set_required_env(monkeypatch)
    monkeypatch.setenv("INTERVALO_BACKUP_HORAS", "7")
    config = load_config(env_path=tmp_path / "no_such_file.env")
    assert config.pasta_backup is None
    assert config.intervalo_backup_horas is None


def test_masked_summary_never_exposes_full_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    config = load_config(env_path=tmp_path / "no_such_file.env")
    summary = config.masked_summary()
    assert REQUIRED_ENV["NS_SECRET_KEY"] not in summary["secret_key"]
    assert REQUIRED_ENV["NS_CLIENT_KEY"] not in summary["client_key"]


def test_repr_never_exposes_full_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_required_env(monkeypatch)
    config = load_config(env_path=tmp_path / "no_such_file.env")
    text = repr(config)
    assert REQUIRED_ENV["NS_SECRET_KEY"] not in text
    assert REQUIRED_ENV["NS_CLIENT_KEY"] not in text
