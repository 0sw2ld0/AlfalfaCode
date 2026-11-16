"""Programas externos que leen un archivo adjunto (pdftotext), aislados y sin red.

Un adjunto puede venir de cualquier sitio, y el programa que lo descifra es código nativo que nunca vio ese
archivo: como los linters (diagnosticos.Sandbox), corre en el sandbox del sistema (bwrap en Linux), sin red
y sin el entorno del usuario, donde están sus claves. Además no escribe nada que el arnés vaya a leer: su
resultado sale por stdout, con tope y con plazo, y el arnés lo guarda.

La carpeta de trabajo del sandbox es una vacía y temporal: la de la sesión vive en el hogar de AlfalfaCode,
que el sandbox no abre para escribir. El archivo se lee por su ruta, porque el sandbox lee todo el disco.
Antes de cada programa se lanza `true` en el mismo sandbox, como en `prepare` de las órdenes: así un fallo
del sandbox no se confunde con un archivo dañado. Sin sandbox no se lanza nada: SandboxUnavailable.
"""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
import time

from . import procesos
from .seatbelt import PREFLIGHT_SECONDS, SandboxUnavailable, sandbox_del_sistema

TRUE = '/usr/bin/true'
# Ni las claves ni la configuración del usuario: sólo dónde están los programas del sistema y el idioma.
ENTORNO = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
MAX_ERROR = 4096
TROZO = 65536


class Cortada(Exception):
    """El programa escribió más de lo admitido: se mató y `salida` lleva lo leído hasta el tope."""

    def __init__(self, salida):
        super().__init__('salida demasiado grande')
        self.salida = salida


def orden(argv, raiz):
    """`argv` envuelto en el sandbox del sistema para `raiz`, sin red, tras comprobar que arranca."""
    argv = list(argv)
    try:
        envuelta = sandbox_del_sistema(str(raiz), network=False).command_args(argv)
    except OSError as exc:
        raise SandboxUnavailable('No se pudo preparar el sandbox: ' + (exc.strerror or str(exc))) from exc
    prueba = envuelta[:len(envuelta) - len(argv)] + [TRUE]
    try:
        r = subprocess.run(prueba, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           env=dict(ENTORNO), timeout=PREFLIGHT_SECONDS, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise SandboxUnavailable('El sandbox no respondió en ' + str(int(PREFLIGHT_SECONDS)) + ' segundos.') from None
    except OSError as exc:
        raise SandboxUnavailable('No se pudo iniciar el sandbox: ' + (exc.strerror or str(exc))) from exc
    if r.returncode != 0:
        detalle = (r.stderr or b'').decode(errors='replace').strip()[:300]
        raise SandboxUnavailable('El sandbox no arrancó (código ' + str(r.returncode) + ')'
                                 + (': ' + detalle if detalle else '.'))
    return envuelta


def correr(argv, *, tope, plazo):
    """(código, stdout, final de stderr) de `argv` aislado y sin red, en una carpeta temporal vacía.

    Más de `tope` bytes por stdout: se mata y lanza Cortada. Más de `plazo` segundos: se mata y lanza
    TimeoutError. Sin sandbox: SandboxUnavailable, sin lanzar el programa."""
    with tempfile.TemporaryDirectory(prefix='alfalfa-aislada-') as raiz:
        return _correr(argv, raiz, tope, plazo)


def _correr(argv, raiz, tope, plazo):
    envuelta = orden(argv, raiz)
    proc = subprocess.Popen(envuelta, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(ENTORNO), cwd=str(raiz), start_new_session=True)
    try:
        salida, error = _leer(proc, tope, time.monotonic() + plazo)
    finally:
        if proc.poll() is None:
            # bwrap lleva --die-with-parent: su hijo, en otra sesión, muere con él.
            procesos.senal_grupo(proc.pid, signal.SIGKILL)
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    return proc.returncode, salida, error.decode(errors='replace')


def _leer(proc, tope, fin):
    """(stdout, final de stderr) hasta que el programa cierra los dos y sale."""
    salida, error = bytearray(), bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ, salida)
        selector.register(proc.stderr, selectors.EVENT_READ, error)
        while selector.get_map():
            restante = fin - time.monotonic()
            if restante <= 0:
                raise TimeoutError
            for clave, _ in selector.select(restante):
                trozo = os.read(clave.fd, TROZO)
                if not trozo:
                    selector.unregister(clave.fileobj)
                    continue
                clave.data.extend(trozo)
                if clave.data is error:
                    # Del error interesa el final: ahí está la excepción o el motivo.
                    del error[:-MAX_ERROR]
                elif len(salida) > tope:
                    raise Cortada(bytes(salida[:tope]))
    try:
        # Cerró stdout y stderr: o sale ya o lo retiene algo más; un segundo de margen aunque se acabe el plazo.
        proc.wait(max(1.0, fin - time.monotonic()))
    except subprocess.TimeoutExpired:
        raise TimeoutError from None
    return bytes(salida), bytes(error)
