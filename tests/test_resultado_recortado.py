"""Un resultado de herramienta de más de 20 000 caracteres llega al modelo como JSON válido.

Antes se cortaba el JSON ya serializado por el carácter 19 000 y se metía como texto dentro de
otro JSON: el modelo no veía el final de la salida («RESULTADO FINAL: 3 pruebas fallaron») ni los
campos de detrás (`exit_code`, `classification`), y recibía JSON escapado dos veces.
"""
import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from alfalfa.proveedores.nativo.recorte import MAX_RESULTADO, recortar_medio, serializar
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

FINAL = 'RESULTADO FINAL: 3 pruebas fallaron en test_pagos.py'
ORDEN = ("import json\n"
         "print(json.dumps({'paquete_%04d' % i: {'version': '1.0.%d' % i, 'ruta': 'lib\\\\mod_%d' % i}"
         " for i in range(260)}, indent=2))\n"
         "print('" + FINAL + "')\n")


# ---------------------------------------------------------------- serializar
def test_lo_que_cabe_no_cambia():
    resultado = {'output': 'hola', 'exit_code': 0}
    assert serializar(resultado) == json.dumps(resultado, ensure_ascii=False)


def test_conserva_principio_final_y_los_demas_campos():
    salida = 'inicio\n' + ''.join('línea %05d con "comillas" y \\barras\n' % i for i in range(2000)) + FINAL + '\n'
    resultado = {'exit_code': 1, 'output': salida, 'classification': {'disposition': 'failure'}, 'truncated': False}
    texto = serializar(resultado)
    assert len(texto) <= MAX_RESULTADO
    valor = json.loads(texto)
    assert valor['exit_code'] == 1 and valor['classification'] == {'disposition': 'failure'}
    assert valor['truncated'] is False, 'el campo de la herramienta no se toca'
    assert valor['output'].startswith('inicio\n') and valor['output'].endswith(FINAL + '\n')
    assert 'recortados del medio' in valor['output']
    assert '«output»' in valor['recortado']


def test_recorta_por_lineas_enteras():
    texto, quitado = recortar_medio(''.join('fila %04d\n' % i for i in range(1000)), 2000)
    inicio, _, fin = texto.partition('\n…[')
    assert quitado and all(linea.startswith('fila ') for linea in inicio.splitlines())
    assert all(linea.startswith('fila ') for linea in fin.split(']…\n', 1)[1].splitlines())


def test_varios_campos_largos_se_recortan_los_dos():
    resultado = {'salida_nueva': 'a' * 30000, 'salida_vieja': 'b' * 30000, 'exit_code': 0}
    valor = json.loads(serializar(resultado))
    assert len(json.dumps(valor, ensure_ascii=False)) <= MAX_RESULTADO
    assert valor['salida_nueva'].startswith('a') and valor['salida_vieja'].startswith('b')
    assert valor['exit_code'] == 0


def test_una_lista_larga_pierde_elementos_del_final_y_lo_dice():
    resultado = {'matches': [{'path': 'src/a%04d.py' % i, 'line': i, 'text': 'x = %d' % i} for i in range(2000)],
                 'truncated': False}
    valor = json.loads(serializar(resultado))
    assert len(json.dumps(valor, ensure_ascii=False)) <= MAX_RESULTADO
    assert valor['matches'][0]['path'] == 'src/a0000.py'
    assert 'elementos más recortados' in valor['matches'][-1]
    assert 'elementos del final' in valor['recortado']


def test_caracteres_que_ocupan_mas_al_escaparse():
    resultado = {'text': '"\\\n\t' * 20000}
    texto = serializar(resultado)
    assert len(texto) <= MAX_RESULTADO
    json.loads(texto)


def test_sin_un_campo_largo_sigue_siendo_json_valido():
    resultado = {'k%05d' % i: i for i in range(5000)}
    texto = serializar(resultado)
    assert len(texto) <= MAX_RESULTADO
    valor = json.loads(texto)
    assert valor['truncated'] is True and 'recortado' in valor


@pytest.mark.parametrize('texto', ['ñandú ' * 5000, '😀' * 25000])
def test_texto_no_ascii(texto):
    valor = json.loads(serializar({'text': texto}))
    assert len(json.dumps(valor, ensure_ascii=False)) <= MAX_RESULTADO


# ---------------------------------------------------------------- en un turno
def turno(tmp_path, monkeypatch, llamada):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'clientes.csv').write_text(''.join(','.join('"%02d"' % ((i + k) % 100) for k in range(20)) + '\n'
                                                   for i in range(400)))
    vistos = []

    async def request(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        herramientas = [x for x in payload['messages'] if x['role'] == 'tool']
        if not herramientas:
            mensaje = dict(role='assistant', content='', tool_calls=[dict(function=llamada)])
        else:
            vistos.append(herramientas[-1]['content'])
            mensaje = dict(role='assistant', content='Listo.')
        return dict(message=mensaje, done=True, done_reason='stop', prompt_eval_count=10, eval_count=1)

    m = TerminalManager('Prueba', home=tmp_path / 'alfalfa', auto=True)
    try:
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(m.run('Prueba'), 60))
    finally:
        m.close()
    assert vistos, m.task.get('summary')
    return vistos[0]


def test_el_modelo_ve_el_final_de_una_orden_larga(tmp_path, monkeypatch):
    recibido = turno(tmp_path, monkeypatch, dict(name='run_command', arguments={
        'argv': [sys.executable, '-c', ORDEN], 'purpose': 'Ver el informe de dependencias y pruebas'}))
    assert len(recibido) <= MAX_RESULTADO
    valor = json.loads(recibido)
    assert FINAL in valor['output']
    assert valor['exit_code'] == 0 and 'recortado' in valor


def test_una_lectura_con_comillas_llega_como_json_legible(tmp_path, monkeypatch):
    recibido = turno(tmp_path, monkeypatch, dict(name='read_file', arguments={'path': 'clientes.csv'}))
    assert len(recibido) <= MAX_RESULTADO
    valor = json.loads(recibido)
    # Los campos de read_file llegan enteros: el modelo sabe cuántas líneas hay y desde dónde leyó.
    assert valor['total_lines'] == 400 and valor['start_line'] == 1 and len(valor['sha256']) == 64
    assert valor['text'].startswith('"00","01"') and 'recortados del medio' in valor['text']
    assert '«text»' in valor['recortado']
