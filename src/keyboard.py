"""Leitura de teclas sem bloquear nem exigir Enter, para os atalhos do dashboard
(p/q/r/l) e para a navegacao no modo de selecao da fila (setas/Enter/Esc).

No Windows usa-se msvcrt (kbhit/getch). Fora do Windows usa-se termios/tty em modo
cbreak com select para poll nao-bloqueante. Ambos os caminhos sao stdlib puro.

Teclas normais chegam ao callback como o proprio caracter (ex: "p", "q"). Teclas
especiais chegam como um destes tokens: "UP", "DOWN", "LEFT", "RIGHT", "ENTER", "ESC".
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

_POLL_INTERVAL_SECONDS = 0.05
_ESCAPE_SEQUENCE_TIMEOUT_SECONDS = 0.02

KeyCallback = Callable[[str], None]

_WINDOWS_ARROW_SCANCODES = {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT"}
_POSIX_ARROW_FINAL_BYTES = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}


async def read_keys(on_key: KeyCallback) -> None:
    if sys.platform == "win32":
        await _read_keys_windows(on_key)
    else:
        await _read_keys_posix(on_key)


async def _read_keys_windows(on_key: KeyCallback) -> None:
    import msvcrt

    while True:
        if msvcrt.kbhit():
            raw = msvcrt.getch()
            if raw in (b"\x00", b"\xe0"):
                # Prefixo de tecla especial (setas, F1-F12, Home/End, ...) -- o codigo
                # real vem sempre no byte seguinte, entregue pelo SO como par atomico.
                scancode = msvcrt.getch()
                arrow = _WINDOWS_ARROW_SCANCODES.get(scancode)
                if arrow:
                    on_key(arrow)
            elif raw == b"\r":
                on_key("ENTER")
            elif raw == b"\x1b":
                on_key("ESC")
            else:
                char = raw.decode("utf-8", errors="ignore")
                if char:
                    on_key(char)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _read_keys_posix(on_key: KeyCallback) -> None:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ready, _, _ = select.select([fd], [], [], _POLL_INTERVAL_SECONDS)
            if not ready:
                await asyncio.sleep(0)
                continue

            char = sys.stdin.read(1)
            if not char:
                continue

            if char in ("\r", "\n"):
                on_key("ENTER")
            elif char == "\x1b":
                on_key(_read_posix_escape_sequence(fd))
            else:
                on_key(char)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_posix_escape_sequence(fd: int) -> str:
    """Chamado logo depois de ler um ESC (0x1b): decide se foi um Esc isolado ou o
    inicio de uma sequencia de seta (ESC [ A/B/C/D), sem bloquear caso seja so o Esc."""
    import select

    ready, _, _ = select.select([fd], [], [], _ESCAPE_SEQUENCE_TIMEOUT_SECONDS)
    if not ready:
        return "ESC"

    second = sys.stdin.read(1)
    if second != "[":
        return "ESC"

    ready, _, _ = select.select([fd], [], [], _ESCAPE_SEQUENCE_TIMEOUT_SECONDS)
    if not ready:
        return "ESC"

    third = sys.stdin.read(1)
    return _POSIX_ARROW_FINAL_BYTES.get(third, "ESC")
