"""Borrador del perfil `ui` de PACAI (docs/pacai/perfil-ui.md): el ejemplo cumple el núcleo 0.3 y el esquema del perfil.

Todavía no hay implementación: estas pruebas sólo mantienen coherentes la especificación, su esquema y su ejemplo.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from alfalfa.pacai.verificador import Session

RAIZ = Path(__file__).resolve().parent.parent
DOCS = RAIZ / 'docs' / 'pacai'
ESQUEMA = json.loads((RAIZ / 'alfalfa' / 'pacai' / 'pacai-ui-1.schema.json').read_text(encoding='utf-8'))
EJEMPLO = [json.loads(linea) for linea in (DOCS / 'ejemplos' / 'ui-tickets.jsonl').read_text(encoding='utf-8')
           .splitlines() if linea.strip()]


def errores(definicion, dato):
    validador = Draft202012Validator({**ESQUEMA, '$ref': f'#/$defs/{definicion}'})
    return [e.message for e in validador.iter_errors(dato)]


def bloques(esquema):
    for m in EJEMPLO:
        for b in m['payload'].get('blocks', []):
            if b.get('type') == 'structured' and b.get('schema') == esquema:
                yield m, b['data']


def test_el_esquema_es_valido():
    Draft202012Validator.check_schema(ESQUEMA)


def test_el_ejemplo_cumple_el_nucleo():
    assert Session.check(EJEMPLO) == []


def test_el_ejemplo_cumple_el_perfil():
    assert errores('declaration', EJEMPLO[0]['payload']['render']['ui']) == []
    actualizaciones = list(bloques('pacai.ui/1'))
    acciones = list(bloques('pacai.ui.action/1'))
    assert actualizaciones and acciones
    for _, data in actualizaciones:
        assert errores('update', data) == [], data
    for m, data in acciones:
        assert m['sender'] == 'client' and m['type'] == 'input'
        assert errores('action', data) == []


def test_las_revisiones_suben_y_cada_message_end_es_una_foto():
    revisiones = [data['rev'] for _, data in bloques('pacai.ui/1')]
    assert revisiones == sorted(set(revisiones))
    for m, data in bloques('pacai.ui/1'):
        if m['type'] == 'message_end':
            assert data['op'] == 'replace'
            ids = {n['id'] for n in data['nodes']}
            hijos = {k for n in data['nodes'] for k in n.get('children', [])}
            assert 'root' in ids and hijos <= ids


CAMPO = {'id': 'f', 'component': 'Input', 'props': {'name': 'q'}}
NODO_BIEN = {'id': 'b', 'component': 'Button', 'props': {'label': 'Crear'}, 'events': {'press': {'send': 'crear'}}}


@pytest.mark.parametrize('nodo, motivo', [
    ({'id': 'x', 'component': 'Text', 'props': {}}, 'Text sin text'),
    ({'id': 'x', 'component': 'Text', 'props': {'text': 'hola'}, 'children': ['y']}, 'Text no admite hijos'),
    ({'id': 'x', 'component': 'Text', 'props': {'text': 'hola', 'html': '<b>'}}, 'propiedad desconocida'),
    ({**NODO_BIEN, 'events': {'submit': {'send': 'crear'}}}, 'evento que Button no tiene'),
    ({**NODO_BIEN, 'events': {'press': [{'send': 'a'}, {'send': 'b'}]}}, 'dos send en un evento'),
    ({**NODO_BIEN, 'events': {'press': {'open_url': 'http://ejemplo.com'}}}, 'open_url sin https'),
    ({'id': 'i', 'component': 'Image', 'props': {'src': 'javascript:alert(1)', 'alt': 'x'}}, 'src peligroso'),
    ({'id': 'i', 'component': 'Image', 'props': {'src': 'https://ejemplo.com/a.png'}}, 'Image sin alt'),
    ({**CAMPO, 'events': {'change': {'send': 'buscar'}}}, 'change con send'),
    ({'id': 'f', 'component': 'Select', 'props': {'name': 'q', 'options': [{'value': 'a'}]},
      'events': {'change': [{'set': {'a': 1}}, {'send': 'filtrar'}]}}, 'change con send en una lista'),
    ({**CAMPO, 'events': {'change': {'call': 'herramienta'}}}, 'no hay call'),
])
def test_nodos_invalidos(nodo, motivo):
    assert errores('node', nodo), motivo


def test_nodos_validos():
    assert errores('node', NODO_BIEN) == []
    assert errores('node', {**NODO_BIEN, 'events': {'press': [{'set': {'abierto': True}}, {'send': 'crear'}]}}) == []
    assert errores('node', {'id': 't', 'component': 'Text', 'props': {'text': {'$': 'titulo'}}}) == []
    filtro = {'change': {'set': {'filtro': 'x'}}}
    assert errores('node', {**CAMPO, 'events': filtro}) == []
    # Un componente propio del cliente sólo se comprueba por forma: su esquema llega en la declaración.
    assert errores('node', {'id': 't', 'component': 'Ticket', 'props': {'title': 'Uno'}}) == []


def test_placement():
    base = {'view': 'v', 'op': 'patch', 'rev': 2, 'nodes': []}
    assert errores('update', {**base, 'placement': 'latest'}) == []
    assert errores('update', {**base, 'placement': 'inline'}) == []
    assert errores('update', {**base, 'placement': 'arriba'})


def test_replace_exige_raiz_y_alt():
    nodos = [{'id': 'root', 'component': 'Stack'}]
    assert errores('update', {'view': 'v', 'op': 'replace', 'rev': 1, 'nodes': nodos, 'alt': 'vacía'}) == []
    assert errores('update', {'view': 'v', 'op': 'replace', 'rev': 1, 'nodes': nodos})
    assert errores('update', {'view': 'v', 'op': 'replace', 'rev': 1, 'alt': 'x',
                              'nodes': [{'id': 'otro', 'component': 'Stack'}]})


def test_un_componente_propio_no_pisa_el_catalogo_base():
    propio = {'props': {'type': 'object'}}
    assert errores('declaration', {'version': '1', 'components': {'Ticket': propio}}) == []
    assert errores('declaration', {'version': '1', 'components': {'Button': propio}})
