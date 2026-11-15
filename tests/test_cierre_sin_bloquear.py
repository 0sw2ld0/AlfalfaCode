"""Lo que queda pendiente al cerrar el turno se avisa; el turno no acaba «bloqueado».

Antes, si la sesión había cambiado archivos y quedaba una orden fallida, una comprobación desfasada
o un permiso negado, el turno terminaba `blocked` aunque el modelo ya hubiera respondido, y seguía
así en todos los turnos siguientes («gracias» → bloqueada) hasta que cambiara algún archivo.

En AlfalfaCode el turno termina `completed` con un aviso de lo pendiente. Un integrador que quiera
el estado bloqueado (para no dar por buena una tarea) lo pide con `ajustes.cierre_pendiente_bloquea`.
"""
import asyncio
import hashlib
import subprocess
import sys
from unittest.mock import patch

import pytest

from alfalfa import ajustes
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

GIT = ['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com']


def escribir(antes, despues):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'write_file', 'arguments': {
        'path': 'mundo.txt', 'content': despues, 'expected_sha256': hashlib.sha256(antes.encode()).hexdigest()}}}]}


def orden(herramienta, argv, proposito):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'function': {'name': herramienta, 'arguments': {'argv': argv, 'purpose': proposito}}}]}


def texto(contenido):
    return {'role': 'assistant', 'content': contenido}


class Escena:
    def __init__(self, tmp_path, monkeypatch):
        self.raiz = tmp_path / 'repo'
        self.raiz.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=self.raiz, check=True)
        (self.raiz / 'mundo.txt').write_text('hola\n')
        subprocess.run(GIT + ['add', '-A'], cwd=self.raiz, check=True)
        subprocess.run(GIT + ['commit', '-qm', 'inicio'], cwd=self.raiz, check=True)
        monkeypatch.chdir(self.raiz)
        self.eventos = []
        self.m = TerminalManager('inicio', home=tmp_path / 'alfalfa')
        original = self.m.event

        def event(task, rol, contenido, **kwargs):
            self.eventos.append((rol, str(contenido)))
            return original(task, rol, contenido, **kwargs)

        self.m.event = event

    def turno(self, mensaje, *pasos):
        pasos = list(pasos)

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            paso = pasos.pop(0) if pasos else texto('De nada.')
            return dict(message=paso, done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)

        antes = len(self.eventos)
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(self.m.run(mensaje), 60))
        self.nuevos = [t for r, t in self.eventos[antes:] if r == 'system']
        return self.m.task['status']

    def avisos(self):
        return [t for t in self.nuevos if t.startswith('Queda pendiente')]


@pytest.fixture
def escena(tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch)
    yield e
    e.m.close()


GREP_SIN_COINCIDENCIAS = orden('run_command', ['grep', '-c', 'TODO', 'mundo.txt'], 'Buscar TODO')
COMPROBAR = orden('run_checks', [sys.executable, '-c', "assert open('mundo.txt').read()"], 'Comprobar el mundo')


def test_una_orden_fallida_tras_editar_se_avisa_sin_bloquear(escena):
    estado = escena.turno('cambia el saludo', escribir('hola\n', 'adiós\n'), GREP_SIN_COINCIDENCIAS,
                          texto('Cambié el saludo.'), texto('Cambié el saludo; no hay TODO.'))
    assert estado == 'completed'
    assert escena.m.task['summary'].startswith('Cambié el saludo')
    aviso, = escena.avisos()
    assert 'Buscar TODO' in aviso or 'grep' in aviso
    # Los turnos siguientes tampoco quedan bloqueados, ni repiten el mismo aviso.
    assert escena.turno('gracias') == 'completed' and escena.avisos() == []
    assert escena.turno('¿qué hora es?') == 'completed' and escena.avisos() == []


def test_editar_tras_comprobar_avisa_de_la_verificacion_sin_bloquear(escena):
    estado = escena.turno('cambia el saludo dos veces', escribir('hola\n', 'adiós\n'), COMPROBAR,
                          escribir('adiós\n', 'chao\n'), texto('Listo.'), texto('Listo, sin volver a probar.'))
    assert estado == 'completed'
    aviso, = escena.avisos()
    assert 'verificación' in aviso.lower() or 'comprobaciones' in aviso.lower()
    assert escena.turno('gracias') == 'completed'


def test_un_permiso_negado_no_bloquea_el_turno(escena):
    escena.m.approval.denied = 1
    assert escena.turno('¿qué hay en la carpeta?', texto('Está mundo.txt.')) == 'completed'


def test_sin_respuesta_del_modelo_el_turno_sigue_sin_darse_por_hecho(escena):
    # Una sesión que termina sin respuesta no se marca como completada.
    with patch('alfalfa.evidencia.registro.RunEvidence._result', return_value=None):
        assert escena.turno('hola', texto('')) == 'blocked'


def test_el_integrador_puede_pedir_el_estado_bloqueado(escena, monkeypatch):
    monkeypatch.setattr(ajustes, 'cierre_pendiente_bloquea', lambda: True)
    estado = escena.turno('cambia el saludo', escribir('hola\n', 'adiós\n'), GREP_SIN_COINCIDENCIAS,
                          texto('Hecho.'), texto('Hecho, aunque grep falló.'))
    assert estado == 'blocked' and 'sin resolver' in escena.m.task['summary']
    assert escena.turno('gracias') == 'blocked'


def test_lo_pendiente_sigue_apuntado_aunque_el_turno_termine(escena):
    escena.turno('cambia el saludo', escribir('hola\n', 'adiós\n'), GREP_SIN_COINCIDENCIAS,
                 texto('Hecho.'), texto('Hecho, aunque grep falló.'))
    assert escena.m.runtime().evidence.bloqueantes()
    assert escena.m.task['status'] == 'completed'
