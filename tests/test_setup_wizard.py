"""Testes do assistente de configuracao: logica de ficheiros (.env) e o fluxo
interativo completo, com Prompt/Confirm simulados em vez de stdin real."""
from __future__ import annotations

from pathlib import Path

import pytest

from nsrecruiter import setup_wizard
from nsrecruiter.setup_wizard import _format_env_value, _read_existing_values, _write_env_file, run_setup_wizard


def test_read_existing_values_parses_and_strips_quotes(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comentario\nNS_REGION=Portugal\nNS_NATION=\"New Libertalia Kingdom\"\n\nNS_CONTACT=\n",
        encoding="utf-8",
    )
    values = _read_existing_values(env_path)
    assert values == {"NS_REGION": "Portugal", "NS_NATION": "New Libertalia Kingdom"}


def test_read_existing_values_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _read_existing_values(tmp_path / "nao_existe.env") == {}


def test_format_env_value_quotes_when_needed() -> None:
    assert _format_env_value("Portugal") == "Portugal"
    assert _format_env_value("") == '""'
    assert _format_env_value("valor#comentario") == '"valor#comentario"'
    assert _format_env_value(" com espacos ") == '" com espacos "'


def test_write_env_file_creates_fresh_file_when_no_template_exists(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, {"NS_REGION": "Portugal", "NS_NATION": "New Libertalia Kingdom"})
    content = env_path.read_text(encoding="utf-8")
    assert "NS_REGION=Portugal" in content
    assert "NS_NATION=New Libertalia Kingdom" in content


def test_write_env_file_uses_example_as_template_preserving_comments(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "# explicacao da regiao\nNS_REGION=\n# outra explicacao\nNS_NATION=\nSEND_INTERVAL_SECONDS=182\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"

    _write_env_file(env_path, {"NS_REGION": "Portugal", "NS_NATION": "New Libertalia Kingdom"})

    content = env_path.read_text(encoding="utf-8")
    assert "# explicacao da regiao" in content
    assert "NS_REGION=Portugal" in content
    assert "NS_NATION=New Libertalia Kingdom" in content
    assert "SEND_INTERVAL_SECONDS=182" in content


def test_write_env_file_reruns_preserve_untouched_customizations(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NS_REGION=Portugal\nNS_NATION=Old Name\nSEND_INTERVAL_SECONDS=300\nLOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )

    _write_env_file(env_path, {"NS_REGION": "Portugal", "NS_NATION": "New Libertalia Kingdom"})

    content = env_path.read_text(encoding="utf-8")
    assert "NS_NATION=New Libertalia Kingdom" in content
    assert "SEND_INTERVAL_SECONDS=300" in content, "customizacao manual nao deveria ser perdida"
    assert "LOG_LEVEL=DEBUG" in content, "customizacao manual nao deveria ser perdida"


def test_write_env_file_never_writes_raw_secret_next_to_masked_summary(tmp_path: Path) -> None:
    # Confirma apenas que o valor gravado e o real (nao mascarado); a mascara e so para ecra/logs.
    env_path = tmp_path / ".env"
    _write_env_file(env_path, {"NS_CLIENT_KEY": "abcd1234efgh5678"})
    content = env_path.read_text(encoding="utf-8")
    assert "NS_CLIENT_KEY=abcd1234efgh5678" in content


_ANSWERS_IN_FIELD_ORDER = [
    "Portugal",  # NS_REGION
    "New Libertalia Kingdom",  # NS_NATION
    "",  # NS_CONTACT (opcional)
    "clientkey123",  # NS_CLIENT_KEY
    "999888777",  # NS_TELEGRAM_ID
    "secretkeyabc",  # NS_SECRET_KEY
    "",  # PRIORITY_FLAG_COUNTRIES (opcional)
    "",  # PASTA_BACKUP (opcional)
    "",  # INTERVALO_BACKUP_HORAS (opcional)
]


def test_run_setup_wizard_full_flow_writes_all_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(_ANSWERS_IN_FIELD_ORDER)
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    env_path = tmp_path / ".env"
    assert run_setup_wizard(env_path) is True

    content = env_path.read_text(encoding="utf-8")
    assert "NS_REGION=Portugal" in content
    assert "NS_NATION=New Libertalia Kingdom" in content
    assert "NS_CLIENT_KEY=clientkey123" in content
    assert "NS_TELEGRAM_ID=999888777" in content
    assert "NS_SECRET_KEY=secretkeyabc" in content


def test_run_setup_wizard_writes_priority_flag_countries_when_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(
        [
            "Portugal", "New Libertalia Kingdom", "",
            "clientkey123", "999888777", "secretkeyabc",
            "Portugal,Brazil",  # PRIORITY_FLAG_COUNTRIES
            "", "",  # PASTA_BACKUP, INTERVALO_BACKUP_HORAS (opcionais)
        ]
    )
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    env_path = tmp_path / ".env"
    assert run_setup_wizard(env_path) is True

    content = env_path.read_text(encoding="utf-8")
    assert "PRIORITY_FLAG_COUNTRIES=Portugal,Brazil" in content


def test_run_setup_wizard_grava_definicoes_de_backup_quando_fornecidas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(
        [
            "Portugal", "New Libertalia Kingdom", "",
            "clientkey123", "999888777", "secretkeyabc",
            "",  # PRIORITY_FLAG_COUNTRIES
            "D:\\Backups\\NSRecruiter",  # PASTA_BACKUP
            "6",  # INTERVALO_BACKUP_HORAS
        ]
    )
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    env_path = tmp_path / ".env"
    assert run_setup_wizard(env_path) is True

    content = env_path.read_text(encoding="utf-8")
    assert "PASTA_BACKUP=D:\\Backups\\NSRecruiter" in content
    assert "INTERVALO_BACKUP_HORAS=6" in content


def test_run_setup_wizard_declining_confirmation_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(_ANSWERS_IN_FIELD_ORDER)
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: False)

    env_path = tmp_path / ".env"
    assert run_setup_wizard(env_path) is False
    assert not env_path.exists()


def test_run_setup_wizard_empty_secret_reprompts_until_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simula o utilizador a carregar Enter sem escrever nada na Client Key (obrigatoria,
    # sem valor existente para manter) antes de finalmente a escrever.
    answers = iter(
        [
            "Portugal", "New Libertalia Kingdom", "",
            "", "clientkey123",  # primeira tentativa vazia, depois valor valido
            "999888777", "secretkeyabc", "",
            "", "",  # PASTA_BACKUP, INTERVALO_BACKUP_HORAS (opcionais)
        ]
    )
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: True)

    env_path = tmp_path / ".env"
    assert run_setup_wizard(env_path) is True
    content = env_path.read_text(encoding="utf-8")
    assert "NS_CLIENT_KEY=clientkey123" in content
