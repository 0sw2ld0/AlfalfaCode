"""Una respuesta cortada por el límite de salida no deja la sesión sin poder seguir.

Si la respuesta y su única recuperación se cortan (o la recuperación se interrumpe), el turno
falla. Antes, además, la marca `length_recovery` se quedaba guardada y todo mensaje posterior
fallaba al instante, sin llamar al modelo; bifurcar desde un turno anterior heredaba el fallo.
"""
import asyncio
import subprocess
from unittest.mock import patch

import pytest

from alfalfa import sesiones
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

LARGO = 'ESCRIBE-MUCHO'


class Escena:
    def __init__(self, tmp_path, monkeypatch):
        raiz = tmp_path / 'proyecto'
        raiz.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
        monkeypatch.chdir(raiz)
        self.home = tmp_path / 'alfalfa'
        self.peticiones, self.en_recuperacion = 0, False
        self.m = TerminalManager('inicio', home=self.home)

    def turno(self, texto, detener_en_la_recuperacion=False):
        escena = self

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            escena.peticiones += 1
            ultimos = [m['content'] for m in payload['messages'] if m['role'] == 'user'][-2:]
            if any('se cortó por el límite de salida' in u for u in ultimos[-1:]):
                escena.en_recuperacion = True
                if detener_en_la_recuperacion:
                    await asyncio.Event().wait()
            if LARGO in ' '.join(ultimos):
                # El modelo se queda sin salida a mitad de un archivo grande, y al recuperar también.
                return dict(message={'role': 'assistant', 'content': 'def f():\n    ...'}, done=True,
                            done_reason='length', prompt_eval_count=900, eval_count=4096)
            return dict(message={'role': 'assistant', 'content': 'Respuesta normal.'}, done=True,
                        done_reason='stop', prompt_eval_count=900, eval_count=10)

        async def correr():
            tarea = asyncio.create_task(self.m.run(texto))
            while detener_en_la_recuperacion and not tarea.done():
                if escena.en_recuperacion:
                    tarea.cancel()
                    break
                await asyncio.sleep(0.01)
            await tarea

        self.en_recuperacion = False
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(correr(), 60))
        return self.m.task['status']

    def estado(self):
        return historial.leer(self.m.directory / 'native' / self.m.task['id'] / 'session.json')


@pytest.fixture
def escena(tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch)
    yield e
    e.m.close()


def test_tras_una_respuesta_cortada_dos_veces_la_sesion_sigue(escena):
    assert escena.turno('Hola') == 'completed'
    assert escena.turno(LARGO + ': genera el archivo entero de 3000 líneas') == 'failed'
    assert 'recuperación' in escena.m.task['summary']
    antes = escena.peticiones
    for _ in range(2):
        assert escena.turno('vale, entonces hazlo en trozos pequeños') == 'completed'
    assert escena.peticiones == antes + 2
    assert 'length_recovery' not in escena.estado()


def test_tras_detener_durante_la_recuperacion_la_sesion_sigue(escena):
    assert escena.turno(LARGO + ': genera el archivo entero', detener_en_la_recuperacion=True) == 'interrupted'
    antes = escena.peticiones
    assert escena.turno('mejor en trozos pequeños') == 'completed'
    assert escena.peticiones == antes + 1


def test_el_motor_sin_persona_sigue_sin_repetir_la_recuperacion(escena):
    # Quien reanuda solo (un integrador) no vuelve a lanzar una recuperación ya gastada.
    assert escena.turno(LARGO + ': genera el archivo entero') == 'failed'
    rt = escena.m.runtime()
    with pytest.raises(ValueError, match='Recuperación por límite de salida'):
        asyncio.run(asyncio.wait_for(rt.run('otra vez'), 30))


def test_bifurcar_desde_un_turno_anterior_no_hereda_la_marca(escena):
    assert escena.turno('Hola') == 'completed'
    assert escena.turno(LARGO + ': genera el archivo entero') == 'failed'
    assert escena.estado().get('length_recovery')
    ident, primer_turno = escena.m.task['id'], escena.m.task['turnos'][0]['id']
    escena.m.close()
    nuevo = sesiones.bifurcar(escena.home / 'sesiones', ident, primer_turno)
    copia = historial.leer(escena.home / 'sesiones' / nuevo / 'native' / nuevo / 'session.json')
    assert 'length_recovery' not in copia
    assert not any(LARGO in str(m.get('content')) for m in copia['messages'])
