"""Leitura de teclas sem bloquear nem exigir Enter, para os atalhos do dashboard (p/q/r).

No Windows usa-se msvcrt (kbhit/getch). Fora do Windows usa-se termios/tty em modo
cbreak com select para poll nao-bloqueante. Ambos os caminhos sao stdlib puro.
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

_POLL_INTERVAL_SECONDS = 0.05

KeyCallback = Callable[[str], None]


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
            if ready:
                char = sys.stdin.read(1)
                if char:
                    on_key(char)
            else:
                await asyncio.sleep(0)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
