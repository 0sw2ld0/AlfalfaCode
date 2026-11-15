"""Sobre común (§2): ids, `seq` por emisor y hora UTC."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .tipos import VERSION

__all__ = ['Emisor', 'ahora', 'instante', 'nuevo_id', 'nueva_sesion', 'en_segundos']


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def instante(momento: datetime) -> str:
    """ISO 8601 en UTC con milisegundos, como `2026-11-12T10:15:30.250Z`."""
    return momento.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def en_segundos(momento: datetime, segundos: float) -> str:
    return instante(momento + timedelta(seconds=segundos))


def nuevo_id() -> str:
    return 'm_' + secrets.token_hex(8)


def nueva_sesion() -> str:
    """128 bits aleatorios, como pide §2 en los transportes de red."""
    return 's_' + secrets.token_hex(16)


class Emisor:
    """Fabrica los mensajes de un lado con su `seq` correlativa. `reloj` e `ids` se sustituyen en pruebas."""

    def __init__(self, lado: str, sesion: str, *, reloj: Callable[[], datetime] = ahora,
                 ids: Callable[[], str] = nuevo_id):
        if lado not in ('agent', 'client'):
            raise ValueError('lado debe ser "agent" o "client"')
        self.lado, self.sesion, self.reloj, self.ids, self.seq = lado, sesion, reloj, ids, 0

    def mensaje(self, tipo: str, payload: dict[str, Any] | None = None, *, reply_to: str | None = None) -> dict:
        self.seq += 1
        m = {'pacai': VERSION, 'id': self.ids(), 'type': tipo, 'session': self.sesion, 'sender': self.lado,
             'seq': self.seq, 'time': instante(self.reloj()), 'payload': dict(payload or {})}
        if reply_to is not None:
            m['reply_to'] = reply_to
        return m
