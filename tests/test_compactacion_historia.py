"""Tras compactar, el modelo no retoma pedidos viejos ni toma por suyos cambios ajenos.

Un modelo flash leía los mensajes antiguos del usuario, que Codex pasa sueltos,
como encargos nuevos y rehízo un commit; además, sin la historia, metió en el
commit cambios que no eran suyos. Y el informe de estado que se añade en cada
turno crecía sin tope y se acumulaba en el contexto.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from alfalfa.compactacion import HISTORIA, PREFIJO, historial_compactado
from alfalfa.proveedores.nativo.runtime import ARCHIVOS_PROPIOS, ESTADO_PREFIJO, MAX_ESTADO, NativeRuntime
from tests.test_alfalfa_compactacion import VENTANA, Modelo, Sesion, leer, llamada

SISTEMA = {'role': 'system', 'content': 'Instrucciones'}


def escribir(path='nuevo.txt'):
    return llamada('write_file', {'path': path, 'content': 'hola\n', 'expected_sha256': 'NEW'})


def test_los_mensajes_anteriores_van_como_historia_y_el_ultimo_como_encargo():
    contexto = {'role': 'user', 'content': 'archivos'}
    mensajes = historial_compactado(SISTEMA, ['Haz commit', 'Arregla la prueba', 'Ahora el README'],
                                    'Resumen', [contexto])
    assert mensajes[0] == SISTEMA
    assert mensajes[1]['content'].startswith(HISTORIA)
    assert '1. Haz commit' in mensajes[1]['content'] and '2. Arregla la prueba' in mensajes[1]['content']
    assert 'Ahora el README' not in mensajes[1]['content']
    # El contexto del arnés va justo antes del último mensaje real, como en Codex.
    assert mensajes[2:] == [contexto, {'role': 'user', 'content': 'Ahora el README'},
                            {'role': 'user', 'content': PREFIJO + '\nResumen'}]


def test_entre_turnos_tambien_el_ultimo_es_historia():
    mensajes = historial_compactado(SISTEMA, ['Haz commit', 'Ya está'], 'Resumen', ultimo_es_encargo=False)
    assert len(mensajes) == 3 and '2. Ya está' in mensajes[1]['content']
    assert not any(m['content'] in ('Haz commit', 'Ya está') for m in mensajes)


def test_un_solo_mensaje_sigue_como_en_codex():
    assert historial_compactado(SISTEMA, ['Haz tres pasos.'], 'R') == [
        SISTEMA, {'role': 'user', 'content': 'Haz tres pasos.'}, {'role': 'user', 'content': PREFIJO + '\nR'}]


def test_tras_compactar_sabe_que_archivos_cambio_el(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    modelo = Modelo([escribir(), leer(), {'role': 'assistant', 'content': 'Terminado.'}],
                    usos=[900, VENTANA, 900])
    assert s.correr(modelo) == 0 and len(modelo.resumenes) == 1
    tras = modelo.payloads[-1]['messages']
    propios = [i for i, m in enumerate(tras) if m['content'].startswith(ARCHIVOS_PROPIOS)]
    assert len(propios) == 1 and 'nuevo.txt' in tras[propios[0]]['content']
    assert 'no los metas en un commit' in tras[propios[0]]['content']
    assert tras[propios[0] + 1] == {'role': 'user', 'content': 'Haz tres pasos.'}
    # De qué estaba hecho el contexto, sin su texto.
    hecha, = [f['compactacion'] for f in s.filas('evento')
              if (f.get('compactacion') or {}).get('estado') == 'hecha']
    assert hecha['mensajes_antes'] >= 4 and hecha['composicion']['tool.content'] > 0
    assert sum(hecha['composicion'].values()) <= hecha['antes']


def test_compactar_entre_turnos_no_deja_el_ultimo_mensaje_como_encargo(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    assert s.correr(Modelo([escribir(), {'role': 'assistant', 'content': 'Hecho.'}]), cerrar=False) == 0
    modelo = Modelo([{'role': 'assistant', 'content': 'Vale.'}])
    with patch.object(NativeRuntime, 'request', modelo.request()):
        assert asyncio.run(s.m.compact_now()) is True
    assert s.estado()['checkpoint']['entre_turnos'] is True
    assert s.correr(modelo, prompt='Otra cosa') == 0
    enviados = modelo.payloads[-1]['messages']
    assert {'role': 'user', 'content': 'Haz tres pasos.'} not in enviados
    assert any(m['content'].startswith(HISTORIA) and '1. Haz tres pasos.' in m['content'] for m in enviados)
    assert enviados[-1]['content'] == 'Otra cosa' or any(m['content'] == 'Otra cosa' for m in enviados)


def test_el_modelo_solo_ve_el_ultimo_informe_de_estado(tmp_path, monkeypatch):
    s = Sesion(tmp_path, monkeypatch)
    assert s.correr(Modelo([escribir('a1.txt'), {'role': 'assistant', 'content': 'Hecho.'}]), cerrar=False) == 0
    for n in range(3):
        modelo = Modelo([escribir('b%d.txt' % n), {'role': 'assistant', 'content': 'Hecho.'}])
        assert s.correr(modelo, prompt='Turno %d' % n, cerrar=False) == 0
    s.m.close()
    informes = [m['content'] for m in modelo.payloads[-1]['messages'] if m['content'].startswith(ESTADO_PREFIJO)]
    assert len(informes) == 1 and len(informes[0]) <= MAX_ESTADO
    datos = json.loads(informes[0][len(ESTADO_PREFIJO):])
    assert 'a1.txt' in datos['archivos_que_cambiaste'] and 'b1.txt' in datos['archivos_que_cambiaste']
    # El historial guardado los conserva todos.
    guardados = [m for m in s.estado()['messages'] if m['content'].startswith(ESTADO_PREFIJO)]
    assert len(guardados) == 3


def test_un_informe_viejo_gigante_no_entra_entero():
    gigante = ESTADO_PREFIJO + 'x' * 5_000_000
    mensajes = [SISTEMA, {'role': 'user', 'content': gigante}, {'role': 'user', 'content': 'Hola'},
                {'role': 'user', 'content': gigante}]
    salida = NativeRuntime._solo_el_ultimo_estado(mensajes)
    assert [m['content'][:4] for m in salida] == ['Inst', 'Hola', ESTADO_PREFIJO[:4]]
    assert len(salida[-1]['content']) < MAX_ESTADO + 50


def test_el_informe_breve_tiene_tope():
    fallo = {'argv': ['git', 'push'], 'purpose': 'subir', 'exit_code': 1, 'output': 'error ' * 20000}
    informe = {'status': 'failed', 'operation_failures': [fallo] * 300, 'check_failures': [fallo] * 300,
               'mismatches': ['m%d' % i for i in range(500)], 'checks_run': 3}
    yo = SimpleNamespace(_archivos_propios=lambda: ['src/f%d.swift' % i for i in range(5000)])
    breve = NativeRuntime._estado_breve(yo, informe)
    assert len(ESTADO_PREFIJO + json.dumps(breve, ensure_ascii=False)) <= MAX_ESTADO
    assert breve['fallos_total'] == 600 and breve['archivos_que_cambiaste_total'] == 5000
    assert breve['archivos_que_cambiaste'][-1] == 'src/f4999.swift'
