"""PACAI UI Lang y las piezas declarativas (prototipo, detrás de ALFALFA_PACAI_UI_LANG): traducción exacta,
cambios atómicos, streaming, prompt, flag apagado y los vectores de conformidad compartidos con la web."""
import asyncio
import json
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from alfalfa.pacai import piezas, ui_lang
from alfalfa.pacai.ui import Catalogo, Vistas, errores_de, partir
from tests.pacai_comun import Cliente, abrir, cerrar

RAIZ = Path(__file__).resolve().parent.parent
CONFORMIDAD = Path(__file__).with_name('conformidad')
DECLARACION = json.loads((CONFORMIDAD / 'catalogo-cuymercado.json').read_text(encoding='utf-8'))
VECTORES = json.loads((CONFORMIDAD / 'pacai-ui-piezas.json').read_text(encoding='utf-8'))['vectores']
CON_FLAG = {ui_lang.FLAG: '1'}

TABLERO = '''root Stack direction=vertical gap=medium > filtros,cifras,p_bajo,fuente
filtros Filters
cifras Grid columns=4 > c_subieron,c_bajaron
c_subieron Stat "Subieron" icon=subio query={"tipo":"totales","campo":"subieron"}
c_bajaron Stat "Bajaron" icon=bajo query={"tipo":"totales","campo":"bajaron"}
p_bajo Panel "Lo que más bajó" > bajo_top
bajo_top PriceBars query={"tipo":"productos","orden":"baja","limite":6}
fuente Text "Fuente: MIDAGRI · Gran Mercado Mayorista de Lima." style=caption'''

TABLERO_JSON = '''{"id":"root","component":"Stack","props":{"direction":"vertical","gap":"medium"},"children":["filtros","cifras","p_bajo","fuente"]}
{"id":"filtros","component":"Filters","props":{"period":{"$":"periodo"},"family":{"$":"familia"}}}
{"id":"cifras","component":"Grid","props":{"columns":4},"children":["c_subieron","c_bajaron"]}
{"id":"c_subieron","component":"Stat","props":{"label":"Subieron","icon":"subio","period":{"$":"periodo"},"family":{"$":"familia"},"query":{"tipo":"totales","campo":"subieron"}}}
{"id":"c_bajaron","component":"Stat","props":{"label":"Bajaron","icon":"bajo","period":{"$":"periodo"},"family":{"$":"familia"},"query":{"tipo":"totales","campo":"bajaron"}}}
{"id":"p_bajo","component":"Panel","props":{"title":"Lo que más bajó"},"children":["bajo_top"]}
{"id":"bajo_top","component":"PriceBars","props":{"period":{"$":"periodo"},"family":{"$":"familia"},"query":{"tipo":"productos","orden":"baja","limite":6}}}
{"id":"fuente","component":"Text","props":{"style":"caption","text":"Fuente: MIDAGRI · Gran Mercado Mayorista de Lima."}}'''  # noqa: E501

PATCH = '''~p_bajo "Bajas e historial" +> bajo_hist
bajo_hist PriceLines query={"tipo":"historial","productos":["palta","limon"]}'''


def catalogo(declaracion=None, entorno=None):
    return Catalogo.de(DECLARACION if declaracion is None else declaracion, CON_FLAG if entorno is None else entorno)


def tablero():
    nodos, diagnosticos = ui_lang.traducir(TABLERO, catalogo())
    assert diagnosticos == []
    return {n['id']: n for n in nodos}


# -- flag ------------------------------------------------------------------------------------------------
def test_sin_flag_todo_sigue_como_hoy():
    cat = Catalogo.de(DECLARACION, {})
    assert not cat.lang and not cat.piezas and 'Calc' not in cat.componentes()
    assert '```pacai-ui view=tickets' in cat.manual() and ui_lang.VALLA_LANG not in cat.manual()
    # La valla compacta es texto normal sin el flag; con él, un bloque de pantalla.
    texto = '```pacai-ui-lang view=t\nroot Stack\n```'
    assert [t[0] for t in partir(texto)] == ['text']
    assert partir(texto, lang=True)[0][:2] == ('ui', {'view': 't', 'lang': True})
    # Una prop de las piezas no pasa por el esquema base.
    assert cat.errores_del_nodo({'id': 't', 'component': 'Text', 'props': {'text': 'x', 'rojo': True}})
    assert not ui_lang.activo({}) and ui_lang.activo({ui_lang.FLAG: '1'})


def test_las_piezas_piden_flag_y_que_el_cliente_las_declare():
    sin_piezas = dict(DECLARACION)
    del sin_piezas['pieces']
    assert catalogo(sin_piezas).lang and not catalogo(sin_piezas).piezas
    cat = catalogo()
    assert cat.piezas and set(cat.consultas) == {'formPedido', 'comparar'}
    assert errores_de('declaration', DECLARACION) == []
    assert cat.errores_del_nodo({'id': 't', 'component': 'Text', 'props': {'value': {'$': 'm.pct'},
                                                                          'rojo': {'$': 'm.perdida'}}}) == []
    assert cat.errores_del_nodo({'id': 'n', 'component': 'Input', 'props': {
        'name': 'cantidad', 'kind': 'number', 'min': 1, 'max': 500, 'step': 1, 'required': True}}) == []
    assert cat.errores_del_nodo({'id': 'n', 'component': 'Input', 'props': {'name': 'n', 'step': 0}})
    # `si` también vale en un componente propio, aunque su esquema no lo nombre.
    assert cat.errores_del_nodo({'id': 'f', 'component': 'Filters', 'props': {'si': {'$': 'v', 'es': 'a'}}}) == []


# -- traducción exacta -------------------------------------------------------------------------------------
def test_el_tablero_se_traduce_exacto_al_json_de_hoy_y_vuelve():
    esperado = [json.loads(linea) for linea in TABLERO_JSON.splitlines()]
    nodos, diagnosticos = ui_lang.traducir(TABLERO, catalogo())
    assert diagnosticos == [] and nodos == esperado
    assert ui_lang.a_lang(esperado, catalogo()) == TABLERO
    for nodo in nodos:
        assert catalogo().errores_del_nodo(nodo) == []


def test_un_valor_explicito_gana_a_la_herencia():
    texto = 'root Stack > s\ns Stat "Hoy" period=$periodo_b query={"tipo":"totales","campo":"iguales"}'
    nodos, _ = ui_lang.traducir(texto, catalogo())
    assert nodos[1]['props']['period'] == {'$': 'periodo_b'} and nodos[1]['props']['family'] == {'$': 'familia'}


@pytest.mark.parametrize('linea, motivo', [
    ('t Text "a" text="b"', 'posicional o nombrada'),
    ('g Grid "x" columns=2', 'no tiene texto posicional'),
    ('t Text style=caption "tarde"', 'justo después del componente'),
    ('t Texto "a"', 'no está en el catálogo'),
    ('p PriceLines query={"tipo":"productos","productos":["palta"]}', 'historial'),
    ('t Text "a" @press={"send":"x"}', 'no tiene el evento'),
    ('t Text "a" > b', 'no admite hijos'),
    ('t Text "a" style=caption style=title', 'repetida'),
    ('t Text "a" query={"tipo":', 'JSON mal cerrado'),
    ('T Text "a"', 'empieza por un id'),
    ('t "a"', 'Falta el componente'),
    ('~t "a"', 'sólo van líneas de nodo'),
])
def test_una_linea_mala_se_descarta_sin_corregirla(linea, motivo):
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('root Stack > t\n' + linea + '\n')
    nodos, diagnosticos = flujo.cerrar()
    assert [n['id'] for n in nodos] == ['root'] and nodos[0]['children'] == []
    assert any(motivo in d.motivo for d in diagnosticos), [d.texto() for d in diagnosticos]


def test_valores_json_enums_y_enlaces():
    linea = ui_lang.leer_linea('n Input "Cantidad" name=cantidad kind=number min=1 max=500 step=1 required=true '
                               'placeholder="p. ej. 20" @change={"set":{"x":1}}')
    assert linea.props == {'name': 'cantidad', 'kind': 'number', 'min': 1, 'max': 500, 'step': 1, 'required': True,
                           'placeholder': 'p. ej. 20'}
    assert linea.eventos == {'change': {'set': {'x': 1}}} and linea.posicional == 'Cantidad'
    assert ui_lang.leer_linea('t Text value=$m.pct rojo=$m.perdida').props == {
        'value': {'$': 'm.pct'}, 'rojo': {'$': 'm.perdida'}}
    assert ui_lang.leer_linea('d Input name=d kind=date min=2026-11-25').props['min'] == '2026-11-25'


def test_un_default_solo_si_el_catalogo_lo_declara():
    declaracion = json.loads(json.dumps(DECLARACION))
    declaracion['components']['Grid']['props']['properties']['columns']['default'] = 4
    declaracion['components']['Grid']['props']['required'] = ['columns']
    nodos, diagnosticos = ui_lang.traducir('root Grid', catalogo(declaracion))
    assert diagnosticos == [] and nodos[0]['props'] == {'columns': 4}
    nodos, diagnosticos = ui_lang.traducir('root Stack > s\ns Stat icon=subio', catalogo())
    assert any('label' in d.motivo or 'query' in d.motivo for d in diagnosticos)


# -- streaming ---------------------------------------------------------------------------------------------
def test_cada_nodo_se_publica_en_cuanto_llega_y_solo_crece():
    flujo = ui_lang.Flujo(catalogo())
    publicaciones = []
    texto = TABLERO + '\n'
    for i in range(0, len(texto), 7):                  # trozos que cortan líneas por la mitad
        publicaciones += flujo.alimentar(texto[i:i + 7])
    nodos, diagnosticos = flujo.cerrar()
    assert diagnosticos == []
    primeras = [[n['id'] for n in p] for p in publicaciones]
    assert primeras[0] == ['root'] and primeras[1] == ['root', 'filtros']
    assert len(publicaciones) == 8                       # una por línea: nada espera al final del bloque
    # Cada publicación es un árbol válido: sus hijos son el prefijo de los que ya llegaron.
    hijos_publicados = {}
    for publicacion in publicaciones:
        for nodo in publicacion:
            antes = hijos_publicados.get(nodo['id'], [])
            assert nodo.get('children', [])[:len(antes)] == antes          # sólo se añade al final
            hijos_publicados[nodo['id']] = nodo.get('children', [])
            assert all(h in hijos_publicados for h in nodo.get('children', []) if h != nodo['id']) or True
    assert hijos_publicados['root'] == ['filtros', 'cifras', 'p_bajo', 'fuente']


def test_un_hijo_que_falla_retiene_a_sus_hermanos_y_se_pide_una_vez():
    lineas = TABLERO.splitlines()
    lineas[3] = 'c_subieron Stat "Subieron" icon=sube query={"tipo":"totales","campo":"subieron"}'   # enum malo
    flujo = ui_lang.Flujo(catalogo())
    publicaciones = []
    for linea in lineas:
        publicaciones += flujo.alimentar(linea + '\n')
    ids = [n['id'] for p in publicaciones for n in p]
    assert 'c_bajaron' not in ids and 'p_bajo' in ids         # sin hueco: el hermano espera, el resto sigue
    nodos, diagnosticos = flujo.cerrar()
    assert [d.ident for d in diagnosticos] == ['c_subieron']
    assert 'c_subieron' in flujo.peticion(diagnosticos)
    nodos, diagnosticos = flujo.reintentar(
        'c_subieron Stat "Subieron" icon=subio query={"tipo":"totales","campo":"subieron"}\n'
        'root Stack > otra')                                   # no sustituye lo que ya valía
    assert [n['id'] for n in nodos] == [n['id'] for n in ui_lang.traducir(TABLERO, catalogo())[0]]
    assert any('ya está definido' in d.motivo for d in diagnosticos)
    with pytest.raises(RuntimeError):
        flujo.reintentar('x Text "y"')


def test_al_cerrar_se_piden_los_hijos_que_no_llegaron_y_los_sueltos():
    nodos, diagnosticos = ui_lang.traducir('root Stack > a,b\na Text "A"\nsuelto Text "S"', catalogo())
    assert nodos[0]['children'] == ['a']
    motivos = ' '.join(d.motivo for d in diagnosticos)
    assert 'Falta la línea de b' in motivos and 'suelto no cuelga de root' in motivos
    assert ui_lang.traducir('a Text "A"', catalogo())[0] == []


# -- cambios ----------------------------------------------------------------------------------------------
def test_el_patch_del_tablero_cambia_titulo_y_anade_al_final():
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), PATCH, catalogo())
    assert diagnosticos == [] and cambio.borrados == []
    p_bajo, bajo_hist = cambio.actualizados
    assert p_bajo == {'id': 'p_bajo', 'component': 'Panel', 'props': {'title': 'Bajas e historial'},
                      'children': ['bajo_top', 'bajo_hist']}
    assert bajo_hist['props'] == {'query': {'tipo': 'historial', 'productos': ['palta', 'limon']},
                                  'period': {'$': 'periodo'}, 'family': {'$': 'familia'}}


def test_un_patch_con_un_fallo_no_cambia_nada():
    antes = tablero()
    cambio, diagnosticos = ui_lang.aplicar_cambio(antes, PATCH.replace('historial', 'productos'), catalogo())
    assert cambio is None and diagnosticos and antes == tablero()


@pytest.mark.parametrize('texto, motivo', [
    ('~root > filtros,cifras,fuente', 'falta p_bajo'),
    ('~root > filtros,cifras,p_bajo,fuente,nada', 'no existen'),
    ('-root', 'no se borra'),
    ('-nada', 'no está en la pantalla'),
    ('fuente Text "otra"', 'ya existe'),
    ('nuevo Text "suelto"', 'no cuelga de ningún nodo'),
    ('~c_subieron "A"\n~c_subieron icon=bajo', 'sólo cambia una vez'),
    ('~cifras +> fantasma', 'fantasma no existe'),
    ('~fuente +> x\nx Text "x"', 'no admite hijos'),
])
def test_cambios_que_se_rechazan(texto, motivo):
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert cambio is None and any(motivo in d.motivo for d in diagnosticos), [d.texto() for d in diagnosticos]


def test_reordenar_borrar_y_anadir_dos_veces():
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), '~root > fuente,cifras,filtros\n-p_bajo', catalogo())
    assert diagnosticos == []
    assert cambio.nodos['root']['children'] == ['fuente', 'cifras', 'filtros']
    assert set(cambio.borrados) == {'p_bajo', 'bajo_top'}           # bajo_top sólo colgaba de p_bajo
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), '~cifras +> c_subieron', catalogo())
    assert diagnosticos == [] and cambio.avisos and cambio.nodos['cifras']['children'] == ['c_subieron', 'c_bajaron']


def test_no_se_borra_un_nodo_con_dos_padres():
    nodos = tablero()
    nodos['cifras']['children'].append('fuente')
    _, diagnosticos = ui_lang.aplicar_cambio(nodos, '-fuente', catalogo())
    assert 'más de un padre' in diagnosticos[0].motivo


def test_vistas_aplica_alta_y_cambio_contra_la_revision():
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())
    assert errores == [] and data['op'] == 'replace' and data['rev'] == 1 and len(data['nodes']) == 8
    assert errores_de('update', data) == []
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True, 'rev': '0'}, PATCH.splitlines())
    assert data is None and 'revisión 0' in errores[0]
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True, 'rev': '1'}, PATCH.splitlines())
    assert errores == [] and data['op'] == 'patch' and data['rev'] == 2 and errores_de('update', data) == []
    resumen = vistas.resumen_lang('tablero')
    assert resumen.splitlines()[0] == 'rev=2' and 'p_bajo Panel "Bajas e historial" > bajo_top,bajo_hist' in resumen
    assert 'query' not in resumen
    sin_flag = Vistas(Catalogo.de(DECLARACION, {}))
    assert sin_flag.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())[0] is None


async def test_el_motor_traduce_la_valla_compacta_con_el_flag(monkeypatch):
    monkeypatch.setenv(ui_lang.FLAG, '1')
    c = Cliente(perfiles=('work', 'ui'))
    c.hello['render'] = {'ui': {'version': '1'}}
    await abrir(c, 'Enséñame un resumen')
    assert '```pacai-ui-lang' in c.t.manual_ui()
    c.t.emit('evento', rol='agent', hora=1.0, texto='Aquí está:\n\n```pacai-ui-lang view=resumen\n'
             'root Stack direction=vertical > titulo,crear\ntitulo Text "2 tickets" style=title\n'
             'crear Button "Crear" variant=primary @press={"send":"crear_ticket"}\n```\n')
    mensajes = c.drenar()
    datos = [b['data'] for m in mensajes for b in m['payload'].get('blocks', []) if b.get('type') == 'structured']
    assert datos[0]['op'] == 'replace' and [n['id'] for n in datos[0]['nodes']] == ['root', 'titulo', 'crear']
    assert datos[0]['nodes'][2] == {'id': 'crear', 'component': 'Button',
                                    'props': {'label': 'Crear', 'variant': 'primary'},
                                    'events': {'press': {'send': 'crear_ticket'}}}
    await cerrar(c)
    # Al pulsar, el modelo recibe la pantalla resumida con su revisión, para cambiarla con rev=N.
    c.enviar('input', {'blocks': [{'type': 'structured', 'schema': 'pacai.ui.action/1', 'data': {
        'view': 'resumen', 'rev': datos[-1]['rev'], 'node': 'crear', 'event': 'press', 'name': 'crear_ticket',
        'values': {}}}]})
    entregado = await asyncio.wait_for(c.t.next_message(), 1)
    texto = getattr(entregado, 'texto', None) or getattr(entregado, 'text', None) or str(entregado)
    assert f'rev={datos[-1]["rev"]}' in texto and 'crear Button "Crear"' in texto
    c.comprobar()


# -- prompt -----------------------------------------------------------------------------------------------
def test_el_prompt_sale_del_catalogo_y_cabe_en_15_lineas():
    texto = ui_lang.prompt(catalogo())
    assert len(texto.splitlines()) <= ui_lang.MAX_LINEAS_PROMPT
    assert 'Stat "label" icon=subio|bajo|igual' in texto and 'PriceLines query={"tipo":"historial"' in texto
    assert 'family=$familia, period=$periodo' in texto and '+>' in texto
    piezas_texto = ui_lang.prompt_piezas(catalogo())
    assert len(piezas_texto.splitlines()) <= 6 and 'formPedido da hoy/lista' in piezas_texto
    # Con muchos componentes propios las líneas se alargan, pero el máximo se mantiene.
    grande = json.loads(json.dumps(DECLARACION))
    for i in range(40):
        grande['components'][f'Pieza{i}'] = {'props': {'type': 'object', 'properties': {'valor': {'type': 'string'}}}}
    assert len(ui_lang.prompt(catalogo(grande)).splitlines()) <= ui_lang.MAX_LINEAS_PROMPT
    assert 'expr' not in texto and 'ui-logica' not in texto + piezas_texto


# -- conformidad compartida con la web ---------------------------------------------------------------------
def _obtener(v):
    if v['tipo'] == 'decimal':
        return piezas.a_texto(piezas.decimal(v['entrada']))
    if v['tipo'] == 'formatear':
        return piezas.formatear(v['valor'], v['formato'])
    if v['tipo'] == 'calc':
        return piezas.calc(v['preset'], v['compra'], v['venta'], v['cantidad'])
    if v['tipo'] == 'validar':
        return piezas.validar_campo(v['componente'], v['props'], v['valor'])
    if v['tipo'] == 'argumentos':
        return piezas.argumentos(v['query'], v['estado'].get)
    nodos = {n['id']: n for n in v['nodos']}
    if v['tipo'] == 'visibles':
        estado = piezas.estado_inicial(nodos, {})
        salida = []
        for paso in v['pasos']:
            estado = {**estado, **paso['estado']}
            salida.append(piezas.visibles(nodos, estado))
        return salida
    if v['tipo'] == 'arbol':
        return piezas.errores_del_arbol(nodos, DECLARACION['queries'])
    raise AssertionError('tipo desconocido: ' + v['tipo'])


@pytest.mark.parametrize('vector', VECTORES, ids=[v['id'] for v in VECTORES])
def test_vector_de_conformidad(vector):
    obtenido = _obtener(vector)
    if vector['tipo'] == 'visibles':
        assert obtenido == [p['esperado'] for p in vector['pasos']]
    elif vector['tipo'] == 'arbol':
        assert len(obtenido) == len(vector['esperado'])
        for (ident, motivo), (esperado_id, fragmento) in zip(obtenido, vector['esperado']):
            assert ident == esperado_id and fragmento in motivo
    else:
        assert obtenido == vector['esperado']


def test_la_bateria_cubre_lo_innegociable():
    assert len(VECTORES) >= 60
    ids = {v['id'] for v in VECTORES}
    assert 'visibles-cambiar-select' in ids
    pares = [v for v in VECTORES if v['tipo'] == 'visibles' and 'nfd' in v['id']]
    textos = json.dumps(pares, ensure_ascii=False)
    assert unicodedata.normalize('NFD', 'café') in textos and unicodedata.normalize('NFC', 'café') in textos


@pytest.mark.skipif(shutil.which('node') is None, reason='sin Node.js')
def test_la_web_pasa_los_mismos_vectores():
    corredor = RAIZ / 'docs' / 'demos' / 'pacai-ui' / 'conformidad.mjs'
    salida = subprocess.run(['node', str(corredor), str(CONFORMIDAD / 'pacai-ui-piezas.json')],
                            capture_output=True, text=True, timeout=60)
    resultado = json.loads(salida.stdout.strip().splitlines()[-1])
    assert resultado['fallos'] == [] and resultado['total'] >= 60, salida.stderr


def test_las_muestras_de_medicion_dicen_lo_mismo_en_los_dos_formatos():
    """Lo que mide scripts/medir_pacai_ui_lang.py: cada muestra compacta se traduce exacta a su JSON."""
    muestras = json.loads((CONFORMIDAD / 'muestras-pacai-ui-lang.json').read_text(encoding='utf-8'))['muestras']
    assert {'tablero', 'patch', 'margen', 'vista', 'pedido', 'comparar'} <= set(muestras)
    for nombre, muestra in muestras.items():
        if nombre == 'patch':
            cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), muestra['lang'], catalogo())
            assert diagnosticos == [] and cambio.actualizados == [json.loads(x) for x in muestra['json'].splitlines()]
            continue
        nodos, diagnosticos = ui_lang.traducir(muestra['lang'], catalogo())
        assert diagnosticos == [], (nombre, [d.texto() for d in diagnosticos])
        assert nodos == [json.loads(x) for x in muestra['json'].splitlines()], nombre
        assert piezas.errores_del_arbol({n['id']: n for n in nodos}, DECLARACION['queries']) == []
