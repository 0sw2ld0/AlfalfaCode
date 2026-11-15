"""Respuestas raras del modelo vuelven al modelo como error en vez de tumbar el turno.

Antes:
- 13 llamadas en una respuesta → «Llamadas a herramientas inválidas», turno muerto, ninguna ejecutada;
- medio emoji suelto (`\\ud83d`) en el texto o en un argumento → UnicodeEncodeError al guardar,
  que salía de run() y cerraba la sesión en la app;
- un nombre de herramienta que no es texto (una lista) → TypeError fuera del turno.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

from alfalfa.proveedores.nativo.runtime import NO_EJECUTADA_POR_TOPE, NativeRuntime, sin_sustitutos
from alfalfa.terminal import TerminalManager


def leer(ruta):
    return dict(function=dict(name='read_file', arguments={'path': ruta}))


class Escena:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.raiz = tmp_path
        for i in range(20):
            (tmp_path / ('a%02d.txt' % i)).write_text('archivo %d\n' % i)
        self.m = TerminalManager('Prueba', home=tmp_path / 'alfalfa', auto=True)
        self.resultados = []

    def turno(self, *respuestas):
        cola = list(respuestas)

        async def request(rt, client, endpoint, payload, **kwargs):
            if endpoint == '/api/show':
                return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
            herramientas = [x for x in payload['messages'] if x['role'] == 'tool']
            if herramientas:
                self.resultados = herramientas
            mensaje = cola.pop(0) if cola else dict(role='assistant', content='Listo.')
            return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=10, eval_count=1)

        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(self.m.run('Prueba'), 60))
        return self.m.task['status']


@pytest.fixture
def escena(tmp_path, monkeypatch):
    e = Escena(tmp_path, monkeypatch)
    yield e
    e.m.close()


def test_trece_lecturas_ejecutan_doce_y_devuelven_la_ultima(escena):
    llamadas = [leer('a%02d.txt' % i) for i in range(13)]
    assert escena.turno(dict(role='assistant', content='', tool_calls=llamadas)) == 'completed'
    assert len(escena.resultados) == 13, 'cada llamada tiene su resultado: el historial queda emparejado'
    contenidos = [json.loads(r['content']) for r in escena.resultados]
    assert [c['text'] for c in contenidos[:12]] == ['archivo %d\n' % i for i in range(12)]
    assert contenidos[12] == {'error': NO_EJECUTADA_POR_TOPE}


def test_mas_de_cien_llamadas_sigue_siendo_invalido(escena):
    llamadas = [leer('a00.txt')] * 101
    assert escena.turno(dict(role='assistant', content='', tool_calls=llamadas)) != 'completed'
    assert 'inválidas' in escena.m.task.get('summary', '')


def test_medio_emoji_en_el_texto_no_cierra_la_sesion(escena):
    assert escena.turno(dict(role='assistant', content='Hecho \ud83d y 😀.')) == 'completed'
    assert escena.m.task['summary'].startswith('Hecho � y 😀.')
    # La sesión se guardó y se puede volver a leer.
    escena.m.save()
    assert '�' in (escena.m.directory / 'task.json').read_text()


def test_medio_emoji_en_un_argumento_se_escribe_sin_romper(escena):
    llamada = dict(function=dict(name='write_file', arguments={'path': 'b.txt', 'content': 'hola \ud83d\n',
                                                                'expected_sha256': 'NEW'}))
    assert escena.turno(dict(role='assistant', content='', tool_calls=[llamada])) == 'completed'
    assert (escena.raiz / 'b.txt').read_text() == 'hola �\n'


@pytest.mark.parametrize('nombre', [['read_file'], 123, None, ''])
def test_un_nombre_que_no_es_texto_vuelve_como_error(escena, nombre):
    llamada = dict(function=dict(name=nombre, arguments={'path': 'a00.txt'}))
    assert escena.turno(dict(role='assistant', content='', tool_calls=[llamada])) == 'completed'
    resultado = json.loads(escena.resultados[-1]['content'])
    assert 'error' in resultado


@pytest.mark.parametrize('entrada, salida', [
    ('sin nada raro', 'sin nada raro'),
    ('par 😀', 'par 😀'),
    ('suelto \ud83d', 'suelto �'),
    ('al revés \ude00\ud83d', 'al revés ��'),
    ({'a': ['x\udfff', {'b': '\ud800'}], 'n': 1}, {'a': ['x�', {'b': '�'}], 'n': 1}),
])
def test_sin_sustitutos(entrada, salida):
    assert sin_sustitutos(entrada) == salida
    json.dumps(sin_sustitutos(entrada), ensure_ascii=False).encode('utf-8')
