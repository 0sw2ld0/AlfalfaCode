"""Un fallo que llega con HTTP 200 en Chat Completions se trata como fallo, con su texto real.

OpenRouter documenta que, si el proveedor de arriba falla después de empezar, responde 200 con
`finish_reason: "error"` y el error en `choices[0].error`. Antes eso se leía como un mensaje vacío:
el turno insistía y acababa en «El modelo terminó sin respuesta ni herramientas», sin el error.
Con `content_filter`, el texto a medias se daba por la respuesta final.
"""
import asyncio

import httpx
import pytest

from alfalfa import proveedores
from alfalfa.proveedores import compatible, llavero
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

MODELO = 'openrouter/google/gemma-4-31b-it:free'
ARRIBA = 'Upstream provider returned error: connection reset'


def error_arriba(codigo=502, mensaje=ARRIBA):
    return {'id': 'gen-1', 'choices': [{
        'index': 0, 'finish_reason': 'error', 'native_finish_reason': 'error',
        'message': {'role': 'assistant', 'content': ''},
        'error': {'code': codigo, 'message': mensaje}}],
        'usage': {'prompt_tokens': 9000, 'completion_tokens': 0}}


FILTRADA = {'choices': [{'finish_reason': 'content_filter',
                         'message': {'role': 'assistant', 'content': 'El archivo dice que'}}]}
BIEN = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'Hola.'}}]}


def llamar(respuesta):
    llavero.guardar('openrouter', 'clave-inventada-openrouter-000')

    async def ir():
        transporte = httpx.MockTransport(lambda peticion: httpx.Response(200, json=respuesta))
        async with proveedores.obtener('openrouter').cliente(30, transporte=transporte) as cliente:
            hecha = await cliente.post('/api/chat', json={'model': 'google/gemma-4-31b-it:free',
                                                          'messages': [{'role': 'user', 'content': 'hola'}]})
            return hecha.status_code, hecha.json()
    return asyncio.run(ir())


# ---------------------------------------------------------------- la traducción
@pytest.mark.parametrize('respuesta, estado, texto', [
    (error_arriba(), 502, ARRIBA),
    (error_arriba(429, 'Rate limit exceeded upstream'), 429, 'Rate limit exceeded upstream'),
    (error_arriba(400, 'This model\'s maximum context length is 8192 tokens'), 400, 'maximum context length'),
    # Un 401 del proveedor de arriba no es la clave del usuario: no se le pide que la revise.
    (error_arriba(401, 'upstream auth failed'), 502, 'upstream auth failed'),
    ({'choices': [{'finish_reason': 'error', 'message': {'content': ''}}]}, 502, 'falló a mitad'),
    ({'choices': [{'finish_reason': 'stop', 'error': 'algo se rompió', 'message': {'content': ''}}]}, 502,
     'algo se rompió'),
    ({'id': 'x', 'choices': []}, 502, 'sin ninguna elección'),
    ({'id': 'x'}, 502, 'sin ninguna elección'),
    (FILTRADA, 422, 'content_filter'),
])
def test_el_fallo_dentro_de_un_200_vuelve_como_error(respuesta, estado, texto):
    codigo, cuerpo = llamar(respuesta)
    assert codigo == estado
    assert texto in cuerpo['error']
    assert 'message' not in cuerpo


def test_una_respuesta_normal_sigue_igual():
    codigo, cuerpo = llamar(BIEN)
    assert codigo == 200 and cuerpo['message']['content'] == 'Hola.' and cuerpo['done_reason'] == 'stop'


# ---------------------------------------------------------------- en un turno entero
@pytest.fixture
def turno(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    llavero.guardar('openrouter', 'clave-inventada-openrouter-000')

    async def sin_esperar(self, segundos):
        return None

    monkeypatch.setattr(NativeRuntime, '_retry_sleep', sin_esperar)
    gestores = []

    def correr(*respuestas):
        cola, pedidas = list(respuestas), []
        real = compatible.Transporte.__init__

        def api(peticion):
            pedidas.append(peticion)
            return httpx.Response(200, json=cola.pop(0) if len(cola) > 1 else cola[0])

        def init(self, proveedor, url, clave, tarea=None, timeout=None, transporte=None):
            real(self, proveedor, url, clave, tarea, timeout, httpx.MockTransport(api))

        monkeypatch.setattr(compatible.Transporte, '__init__', init)
        m = TerminalManager('Hola', home=tmp_path / 'alfalfa', model=MODELO, auto=True)
        gestores.append(m)
        try:
            asyncio.run(asyncio.wait_for(m.run('Hola'), 60))
        except Exception:  # noqa: BLE001  el fallo del proveedor puede subir; lo que importa es la tarea
            pass
        return m, len(pedidas)

    yield correr
    for m in gestores:
        m.close()


def test_en_un_turno_se_ve_el_error_real_del_proveedor(turno):
    m, pedidas = turno(error_arriba())
    error = m.task.get('runtime_provider_error') or {}
    assert ARRIBA in str(error.get('detail'))
    assert m.task['status'] != 'completed'
    assert 'sin respuesta ni herramientas' not in str(m.task.get('summary'))
    assert pedidas == 3, 'un 502 del proveedor de arriba se reintenta como cualquier 502'


def test_si_el_proveedor_se_recupera_el_turno_sigue(turno):
    m, pedidas = turno(error_arriba(), BIEN)
    assert m.task['status'] == 'completed' and pedidas == 2
    assert m.task['summary'].startswith('Hola.')


def test_una_respuesta_filtrada_no_se_da_por_buena(turno):
    m, pedidas = turno(FILTRADA)
    assert pedidas == 1, 'el filtro de contenido no se arregla reintentando'
    assert m.task['status'] != 'completed'
    assert not str(m.task.get('summary')).startswith('El archivo dice que')
    assert 'filtro de contenido' in (m.task.get('runtime_provider_error') or {}).get('action', '')
