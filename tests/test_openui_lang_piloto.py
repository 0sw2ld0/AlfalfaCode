"""El lector de OpenUI Lang 1.0 del piloto de formatos (`scripts/openui_lang.py`) sigue la especificación oficial."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'scripts'))

import openui_lang as o  # noqa: E402

from alfalfa.pacai.ui import Catalogo  # noqa: E402

DECL = {k: v for k, v in json.loads((RAIZ / 'tests' / 'conformidad' / 'catalogo-cuymercado.json')
                                    .read_text(encoding='utf-8')).items() if k not in ('pieces', 'queries')}
CAT = Catalogo.de(DECL, {})
B = {'period': {'$': 'periodo'}, 'family': {'$': 'familia'}}
TABLERO = [
    {'id': 'root', 'component': 'Stack', 'props': {'direction': 'vertical'}, 'children': ['cifras', 'nota']},
    {'id': 'cifras', 'component': 'Grid', 'props': {'columns': 4}, 'children': ['c_sub', 'c_baj']},
    {'id': 'c_sub', 'component': 'Stat', 'props': {'label': 'Subieron', 'icon': 'subio', **B,
                                                  'query': {'tipo': 'totales', 'campo': 'subieron'}}},
    {'id': 'c_baj', 'component': 'Stat', 'props': {'label': 'Bajaron', **B,
                                                  'query': {'tipo': 'totales', 'campo': 'bajaron'}}},
    {'id': 'nota', 'component': 'Text', 'props': {'text': 'Fuente', 'style': 'caption'}},
]


def test_los_ejemplos_del_catalogo_van_y_vuelven_exactos():
    for nodo in CAT.ejemplos() + TABLERO:
        texto = o.a_openui([dict(nodo, id='x' + nodo['id'])], DECL)
        nodos, errores, _ = o.mapa('root = Stack([x' + nodo['id'] + '])\n' + texto, DECL)
        # Los ejemplos sueltos citan hijos que no traen: sólo cuentan los demás errores.
        assert not [e for e in errores if e[1] != 'unresolved'], (texto, errores)
        assert nodos['x' + nodo['id']] == dict(nodo, id='x' + nodo['id'])


def test_las_acciones_son_llamadas_con_arroba():
    texto = o.a_openui([{'id': 'b', 'component': 'Button', 'props': {'label': 'Ir'},
                         'events': {'press': [{'set': {'v': 1}}, {'send': 'ir', 'with': ['v']}]}}], DECL)
    assert texto == 'b = Button("Ir", null, null, Action([@Set($v, 1), @Send("ir", ["v"])]))'


def test_un_parche_sustituye_por_nombre_y_recoge_lo_que_root_ya_no_alcanza():
    base = o.a_openui(TABLERO, DECL)
    parche = ('cifras = Grid([c_sub, c_igu], 4)\n'
              'c_igu = Stat("Iguales", {tipo: "totales", campo: "iguales"}, "igual", $periodo, $familia)')
    nodos, errores, _ = o.mapa(parche, DECL, base=base)
    assert not errores
    assert 'c_baj' not in nodos and nodos['cifras']['children'] == ['c_sub', 'c_igu']
    nodos, errores, _ = o.mapa('nota = null\nroot = Stack([cifras], "vertical")', DECL, base=base)
    assert not errores and 'nota' not in nodos


def test_huerfanos_referencias_sin_definir_y_componentes_inventados_son_errores():
    _, errores, _ = o.mapa('root = Stack([a, falta])\na = Text("x")\nsuelto = Text("y")\nb = Foo()', DECL)
    codigos = {c for _, c, _ in errores}
    assert {'unresolved', 'orphan', 'unknown-component'} <= codigos


def test_sin_root_vale_la_primera_sentencia_que_llama_a_stack():
    nodos, errores, avisos = o.mapa('pagina = Stack([t])\nt = Text("hola")', DECL)
    assert not errores and avisos == ['no-root'] and nodos['root']['children'] == ['t']


def test_comas_opcionales_comentarios_y_comillas_simples():
    nodos, errores, _ = o.mapa("root = Stack([a b]) // raíz\n# nada\na = Text('it\\'s # no')\nb = Text(\"z\")", DECL)
    assert not errores and nodos['a']['props']['text'] == "it's # no"


def test_valla_a_media_linea_y_texto_sin_valla():
    nodos, errores, _ = o.mapa(o.extraer('Aquí va:```openui-lang\nroot = Stack([t])\nt = Text("a")\n```'), DECL)
    assert not errores and nodos['root']['children'] == ['t']
    assert o.extraer('root = Stack([])').strip() == 'root = Stack([])'
    assert o.extraer('```openui-lang\nroot = Stack([])\n```\nListo.').strip() == 'root = Stack([])'


def test_la_cola_del_streaming_se_cierra_sola():
    completas, cola = o.sentencias('root = Stack([a])\na = Text("hol', cerrar_cola=False)
    assert completas == ['root = Stack([a])'] and cola == 'a = Text("hol'
    assert o.cerrar(cola) == 'a = Text("hol")'
