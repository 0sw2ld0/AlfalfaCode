"""Programas externos que leen un adjunto (alfalfa.aisladas): en el sandbox, sin red ni claves, con tope y plazo.

Las «real_» lanzan el sandbox del sistema de verdad y se saltan, con el motivo, donde no arranca."""
import socket
import sys
import time

import pytest

from alfalfa import aisladas
from alfalfa.seatbelt import SandboxUnavailable
from tests.sandbox_del_sistema import motivo_sin_sandbox

NO_ARRANCA = motivo_sin_sandbox()
real = pytest.mark.skipif(NO_ARRANCA is not None, reason=str(NO_ARRANCA))


def test_sin_sandbox_no_se_lanza(tmp_path, monkeypatch):
    marca = tmp_path / 'lanzado'

    class Roto:
        def command_args(self, argv):
            return ['/no/existe/bwrap', '--', *argv]

    monkeypatch.setattr(aisladas, 'sandbox_del_sistema', lambda raiz, **opciones: Roto())
    with pytest.raises(SandboxUnavailable, match='No se pudo iniciar el sandbox'):
        aisladas.correr(['/usr/bin/touch', str(marca)], tope=100, plazo=5)
    assert not marca.exists()


@real
def test_real_sin_red_ni_claves_del_usuario(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-del-usuario-123')
    servidor = socket.create_server(('127.0.0.1', 0))
    try:
        guion = ('import os, socket, sys\n'
                 'print("clave" if "OPENAI_API_KEY" in os.environ else "sin clave")\n'
                 'try:\n'
                 '    socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=3)\n'
                 '    print("con red")\n'
                 'except OSError:\n'
                 '    print("sin red")\n')
        codigo, salida, _ = aisladas.correr([sys.executable, '-I', '-c', guion, str(servidor.getsockname()[1])],
                                            tope=1000, plazo=30)
    finally:
        servidor.close()
    assert codigo == 0 and salida.split() == [b'sin', b'clave', b'sin', b'red']


@real
def test_real_lo_que_pasa_del_tope_se_corta_y_muere():
    inicio = time.monotonic()
    with pytest.raises(aisladas.Cortada) as corte:
        aisladas.correr(['/usr/bin/yes', 'alfalfa'], tope=10_000, plazo=30)
    assert corte.value.salida == (b'alfalfa\n' * 1250)[:10_000]
    assert time.monotonic() - inicio < 15


@real
def test_real_lo_que_tarda_demasiado_se_mata():
    inicio = time.monotonic()
    with pytest.raises(TimeoutError):
        aisladas.correr(['/bin/sleep', '60'], tope=100, plazo=1)
    assert time.monotonic() - inicio < 10


@real
def test_real_del_error_queda_el_final():
    guion = 'import sys\nsys.stderr.write("ruido " * 20000 + "\\nValueError: el motivo\\n")\nsys.exit(2)\n'
    codigo, salida, error = aisladas.correr([sys.executable, '-I', '-c', guion], tope=100, plazo=30)
    assert codigo == 2 and salida == b''
    assert error.endswith('ValueError: el motivo\n') and len(error) <= aisladas.MAX_ERROR
