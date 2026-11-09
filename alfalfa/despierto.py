"""Mantener el Mac despierto mientras un turno trabaja, como «Prevent sleep while running» de Codex.

Mientras dura un turno (con sus subagentes, sus órdenes en segundo plano y la
espera de un permiso) el arnés tiene una aserción de energía de macOS
`PreventUserIdleSystemSleep` con el nombre de AlfalfaCode: el Mac no se duerme
por inactividad. La pantalla sí se apaga cuando le toca, y cerrar la tapa lo
duerme igual: no se toca `pmset` ni nada que pida contraseña.

La aserción es del propio proceso del arnés, por IOKit. macOS la suelta al
salir el proceso, aunque muera con SIGKILL, así que nunca queda un hijo
huérfano como podría quedar un `caffeinate`.

Se apaga con `ALFALFA_MANTENER_DESPIERTO=0`, con `--sin-mantener-despierto` o,
desde la app, con el interruptor de Ajustes ▸ General.
"""
from __future__ import annotations

import ctypes
import os
import sys

VARIABLE = 'ALFALFA_MANTENER_DESPIERTO'
# Sin tildes: `pmset -g assertions` no escribe el nombre en UTF-8.
NOMBRE = 'AlfalfaCode trabaja en un turno'
_APAGADO = {'0', 'no', 'false', 'off', 'apagado'}


def activado_por_defecto(entorno=None):
    """Encendido salvo que `ALFALFA_MANTENER_DESPIERTO` diga 0, no, false, off o apagado."""
    valor = (os.environ if entorno is None else entorno).get(VARIABLE, '')
    return valor.strip().lower() not in _APAGADO


class AsercionIOKit:
    """`IOPMAssertionCreateWithName` con `kIOPMAssertPreventUserIdleSystemSleep`, por ctypes."""

    _NIVEL_ON = 255                   # kIOPMAssertionLevelOn
    _UTF8 = 0x08000100                # kCFStringEncodingUTF8

    def __init__(self):
        iokit = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/IOKit.framework/IOKit')
        cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                      ctypes.POINTER(ctypes.c_uint32)]
        iokit.IOPMAssertionRelease.restype = ctypes.c_int32
        iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        self._iokit, self._cf = iokit, cf

    def _cadena(self, texto):
        valor = self._cf.CFStringCreateWithCString(None, texto.encode('utf-8'), self._UTF8)
        if not valor:
            raise OSError('No se pudo crear la cadena para IOKit.')
        return valor

    def crear(self, nombre):
        tipo, texto = self._cadena('PreventUserIdleSystemSleep'), self._cadena(nombre)
        try:
            ident = ctypes.c_uint32(0)
            codigo = self._iokit.IOPMAssertionCreateWithName(tipo, self._NIVEL_ON, texto, ctypes.byref(ident))
        finally:
            self._cf.CFRelease(tipo)
            self._cf.CFRelease(texto)
        if codigo != 0:
            raise OSError('IOPMAssertionCreateWithName devolvió %d.' % codigo)
        return ident.value

    def soltar(self, ident):
        self._iokit.IOPMAssertionRelease(ident)


def _asercion_del_sistema():
    if sys.platform != 'darwin':
        return None
    try:
        return AsercionIOKit()
    except (OSError, AttributeError):
        return None


class MantenerDespierto:
    """Una aserción por sesión mientras haya trabajo en marcha y el ajuste esté encendido.

    `empezar()` y `terminar()` van en pareja (un turno, una compactación) y se
    pueden anidar. `aviso(bool)` se llama cada vez que la aserción se toma o se
    suelta de verdad: si macOS no la concede, no se avisa de nada.
    """

    def __init__(self, activo=None, *, aviso=None, sistema=None):
        self.activo = activado_por_defecto() if activo is None else bool(activo)
        self.aviso = aviso
        # Se resuelve al tomar la primera aserción, para que las pruebas lo cambien.
        self._sistema = sistema or (lambda: _asercion_del_sistema())
        self._backend = None
        self._trabajos = 0
        self._ident = None

    @property
    def sostenida(self):
        return self._ident is not None

    def empezar(self):
        self._trabajos += 1
        self._ajustar()

    def terminar(self):
        self._trabajos = max(0, self._trabajos - 1)
        self._ajustar()

    def cambiar(self, activo):
        """El interruptor de la app: vale ya, también a mitad de turno."""
        self.activo = bool(activo)
        self._ajustar()

    def cerrar(self):
        self._trabajos = 0
        self._ajustar()

    def _ajustar(self):
        quiere = self.activo and self._trabajos > 0
        if quiere and self._ident is None:
            if self._backend is None and self._sistema is not None:
                self._backend, self._sistema = self._sistema(), None
            if self._backend is None:
                return
            try:
                self._ident = self._backend.crear(NOMBRE)
            except OSError:
                return
            self._avisar(True)
        elif not quiere and self._ident is not None:
            ident, self._ident = self._ident, None
            try:
                self._backend.soltar(ident)
            finally:
                self._avisar(False)

    def _avisar(self, valor):
        if self.aviso is not None:
            try:
                self.aviso(valor)
            except Exception:
                pass
