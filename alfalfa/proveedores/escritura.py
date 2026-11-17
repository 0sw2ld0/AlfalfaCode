"""El texto de la respuesta del modelo mientras llega, para quien lo enseña letra a letra (`alfalfa --pacai`).

El motor sigue trabajando con la respuesta entera: este módulo sólo deja oír los trozos de texto según llegan.
Quien quiere oírlos (el turno principal, con un transporte que los admite) abre `escuchando(oyente)` alrededor de
la petición; el proveedor que recibe trozos llama a `escribir` con cada uno. Si la petición se reintenta o se
descarta, `descartar` avisa de que lo escrito hasta ahí ya no vale.

- Ollama (local y en la nube): con `stream: true`, `/api/chat` da una línea JSON por trozo; `LectorNDJSON` las lee
  según llegan y al final rehace la misma respuesta que sin stream.
- Anthropic: su transporte ya lee el stream SSE (`anthropic.juntar`) y avisa de cada `text_delta`.
- Los demás proveedores dan la respuesta entera; su texto sale al final, como siempre.

Los avisos se dan con una variable de contexto, así que no cruzan a otras peticiones (compactación, revisión,
subagentes) aunque compartan proceso.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

__all__ = ['escuchando', 'activa', 'escribir', 'descartar', 'LectorNDJSON']

Oyente = Callable[[str, str], None]          # (qué, texto): 'texto' con un trozo nuevo, o 'descartar'

_OYENTE: ContextVar[Oyente | None] = ContextVar('alfalfa_escritura', default=None)


@contextmanager
def escuchando(oyente: Oyente | None):
    """Mientras dura, el texto que reciba el proveedor llega a `oyente`. Con None no hace nada."""
    if oyente is None:
        yield
        return
    marca = _OYENTE.set(oyente)
    try:
        yield
    finally:
        _OYENTE.reset(marca)


def activa() -> bool:
    return _OYENTE.get() is not None


def _avisar(que: str, texto: str = '') -> None:
    oyente = _OYENTE.get()
    if oyente is None:
        return
    try:
        oyente(que, texto)
    except Exception:  # noqa: BLE001 - enseñar el texto nunca rompe la respuesta
        pass


def escribir(texto: str) -> None:
    """Un trozo nuevo del texto de la respuesta."""
    if isinstance(texto, str) and texto:
        _avisar('texto', texto)


def descartar() -> None:
    """Lo escrito hasta ahora no vale (la petición se reintenta): quien lo enseña lo retira."""
    _avisar('descartar')


class LectorNDJSON:
    """`/api/chat` de Ollama con `stream: true`: una línea JSON por trozo y la última con `done: true`.

    `trozo` recibe los bytes según llegan y avisa del texto de cada línea sin terminar (`done: false`). `respuesta`
    rehace lo que habría dado la petición sin stream: la última línea con el texto, el razonamiento y las llamadas a
    herramientas de todas. Si llega una sola respuesta entera (los transportes de otros proveedores), la devuelve tal
    cual y no avisa de nada: su texto sale con el mensaje final."""

    def __init__(self):
        self._resto = b''
        self.lineas: list[dict] = []
        self.tamano = 0                       # bytes de texto, razonamiento y llamadas: el tope es el de siempre

    def trozo(self, datos: bytes) -> None:
        self._resto += datos
        *completas, self._resto = self._resto.split(b'\n')
        for linea in completas:
            self._linea(linea)

    def _linea(self, linea: bytes) -> None:
        linea = linea.strip()
        if not linea:
            return
        try:
            valor = json.loads(linea)
        except ValueError:
            return
        if not isinstance(valor, dict):
            return
        self.lineas.append(valor)
        mensaje = valor.get('message')
        if not isinstance(mensaje, dict):
            return
        for clave in ('content', 'thinking'):
            if isinstance(mensaje.get(clave), str):
                self.tamano += len(mensaje[clave].encode('utf-8', 'replace'))
        if isinstance(mensaje.get('tool_calls'), list):
            self.tamano += len(json.dumps(mensaje['tool_calls'], default=str))
        if valor.get('done') is False:
            escribir(mensaje.get('content'))

    def respuesta(self) -> dict[str, Any] | None:
        if self._resto.strip():
            resto, self._resto = self._resto, b''
            self._linea(resto)
        if not self.lineas:
            return None
        error = next((linea for linea in self.lineas if linea.get('error')), None)
        if error is not None:
            return error
        if len(self.lineas) == 1:
            return self.lineas[0]
        final = dict(self.lineas[-1])
        textos, pensado, llamadas = [], [], []
        for linea in self.lineas:
            m = linea.get('message')
            if not isinstance(m, dict):
                continue
            if isinstance(m.get('content'), str):
                textos.append(m['content'])
            if isinstance(m.get('thinking'), str):
                pensado.append(m['thinking'])
            if isinstance(m.get('tool_calls'), list):
                llamadas += m['tool_calls']
        mensaje = dict(final.get('message') if isinstance(final.get('message'), dict) else {})
        mensaje.setdefault('role', 'assistant')
        mensaje['content'] = ''.join(textos)
        if any(pensado):
            mensaje['thinking'] = ''.join(pensado)
        else:
            mensaje.pop('thinking', None)
        if llamadas:
            mensaje['tool_calls'] = llamadas
        else:
            mensaje.pop('tool_calls', None)
        final['message'] = mensaje
        return final
