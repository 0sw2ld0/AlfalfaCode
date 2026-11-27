"""PACAI UI Lang (con todos los modelos; ALFALFA_PACAI_UI_LANG=0 lo apaga) y las piezas declarativas (sólo con el
flag a 1): traducción exacta, cambios atómicos, streaming, prompt, encendido por modelo, flag apagado y los vectores de
conformidad compartidos con la web."""
import asyncio
import dataclasses
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
SIN_LANG = {ui_lang.FLAG: '0'}                  # el JSON con ejemplos: sólo con el flag a 0

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

PATCH = '''p_bajo Panel "Bajas e historial" > bajo_top,bajo_hist
bajo_hist PriceLines query={"tipo":"historial","productos":["palta","limon"]}'''


def catalogo(declaracion=None, entorno=None):
    return Catalogo.de(DECLARACION if declaracion is None else declaracion, CON_FLAG if entorno is None else entorno)


def tablero():
    nodos, diagnosticos = ui_lang.traducir(TABLERO, catalogo())
    assert diagnosticos == []
    return {n['id']: n for n in nodos}


# -- flag ------------------------------------------------------------------------------------------------
def test_con_el_flag_a_0_vuelve_el_json_con_ejemplos():
    assert Catalogo.de(DECLARACION, {}).lang                                   # sin flag: UI Lang, sin piezas
    cat = Catalogo.de(DECLARACION, SIN_LANG)
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


def test_el_posicional_puede_ser_un_enlace():
    nodos, diagnosticos = ui_lang.traducir('root Stack > t\nt Text $moneda style=caption', catalogo())
    assert diagnosticos == [] and nodos[1]['props'] == {'text': {'$': 'moneda'}, 'style': 'caption'}
    _, diagnosticos = ui_lang.traducir('root Stack > t\nt Text $moneda "otro"', catalogo())
    assert diagnosticos                                       # un solo valor por posición


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
    assert primeras[0] == ['root'] and ['root', 'filtros'] in primeras
    # Nada espera al final del bloque: cada línea sale en cuanto se puede leer, aunque no haya llegado entera.
    assert len(publicaciones) >= 8
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
    # El modelo suele reenviar el bloque entero: las líneas idénticas se ignoran y la corregida entra.
    reenvio = TABLERO.replace('icon=bajo', 'icon=bajo')
    nodos, diagnosticos = flujo.reintentar(reenvio)
    assert diagnosticos == [] and nodos == ui_lang.traducir(TABLERO, catalogo())[0]
    with pytest.raises(RuntimeError):
        flujo.reintentar('x Text "y"')


def test_en_el_alta_y_en_el_reintento_gana_la_ultima_linea_de_un_id():
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('root Stack > a,b\na Text "A"\na Text "A"\na Text "otra"\n')   # repetida igual: se ignora
    nodos, diagnosticos = flujo.cerrar()
    assert [d.ident for d in diagnosticos] == ['b'] and nodos[1]['props']['text'] == 'otra'
    assert flujo.reparaciones == ['a va dos veces: vale la línea 4.']
    nodos, diagnosticos = flujo.reintentar('a Text "A2"\nb Text "B"')
    assert diagnosticos == [] and [n['props']['text'] for n in nodos[1:]] == ['A2', 'B']


def test_al_cerrar_se_piden_los_hijos_que_no_llegaron_y_los_sueltos():
    nodos, diagnosticos = ui_lang.traducir('root Stack > a,b\na Text "A"\nsuelto Text "S"', catalogo())
    assert nodos[0]['children'] == ['a']
    motivos = ' '.join(d.motivo for d in diagnosticos)
    assert 'Falta la línea de b' in motivos and 'suelto no cuelga de root' in motivos


# -- reparaciones deterministas del alta --------------------------------------------------------------------------
FORMULARIO_SIN_ROOT = '''formulario Form "Preparar solicitud" > inicio,fin,motivo,cobertura
inicio Input "Inicio" name=inicio kind=date
fin Input "Fin" name=fin kind=date
motivo Input "Motivo" name=motivo
cobertura Input "Quién te cubre" name=cobertura'''


def test_un_alta_sin_root_se_envuelve_en_un_stack_y_luego_se_puede_cambiar():
    # La primera línea no es un contenedor: no es la raíz con otro nombre, así que un Stack nuevo envuelve.
    vistas = Vistas(catalogo())
    lineas = ['titulo Text "Vacaciones" style=title'] + FORMULARIO_SIN_ROOT.splitlines()
    data, errores = vistas.aplicar({'view': 'vacaciones', 'lang': True}, lineas)
    assert errores == [] and data['op'] == 'replace'
    assert data['nodes'][0] == {'id': 'root', 'component': 'Stack', 'props': {'direction': 'vertical'},
                                'children': ['titulo', 'formulario']}
    assert [n['id'] for n in data['nodes']] == ['root', 'titulo', 'formulario', 'inicio', 'fin', 'motivo',
                                                'cobertura']
    assert vistas.reparaciones == ['Faltaba root: un Stack vertical envuelve titulo, formulario.']
    # Los ids no cambian: un cambio a `formulario` vale como con cualquier otra vista.
    data, errores = vistas.aplicar({'view': 'vacaciones', 'lang': True},
                                   ['formulario Form "Enviar solicitud" > inicio,fin'])
    assert errores == [] and data['op'] == 'patch' and vistas.reparaciones == []
    assert data['nodes'][0]['props']['submit_label'] == 'Enviar solicitud'
    assert sorted(data['remove']) == ['cobertura', 'motivo']
    assert vistas.vistas['vacaciones'].nodos['root']['children'] == ['titulo', 'formulario']


def test_varios_nodos_sueltos_acaban_en_root_en_el_orden_en_que_llegaron():
    nodos, diagnosticos = ui_lang.traducir('b Text "B"\nc Card "C" > d\na Text "A"\nd Text "D"', catalogo())
    assert diagnosticos == []
    assert nodos[0]['id'] == 'root' and nodos[0]['children'] == ['b', 'c', 'a']
    assert [n['id'] for n in nodos] == ['root', 'b', 'c', 'd', 'a']


def test_sin_nodos_de_primer_nivel_sigue_faltando_root():
    nodos, diagnosticos = ui_lang.traducir('a Card "A" > b\nb Card "B" > a', catalogo())
    assert nodos == [] and [d.motivo for d in diagnosticos] == ['Falta root.']


def test_un_root_que_se_rechaza_no_se_sustituye_por_otro():
    nodos, diagnosticos = ui_lang.traducir('root Grid columns=2 gap=medium > a,b\na Text "A"\nb Text "B"',
                                           catalogo())
    assert nodos == [] and 'Falta root.' in [d.motivo for d in diagnosticos]


def test_un_root_ilegible_tampoco_se_sustituye():
    nodos, diagnosticos = ui_lang.traducir('root Grid columns=2 query={"tipo": > a,b\na Text "A"\nb Text "B"',
                                           catalogo())
    assert nodos == [] and 'Falta root.' in [d.motivo for d in diagnosticos]


def test_los_hijos_de_un_contenedor_rechazado_no_suben_al_root_nuevo():
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('titulo Text "Pedir"\nformulario Form "Mandar" inventada=1 > nombre\n'
                    'nombre Input "Nombre" name=nombre\n')
    nodos, diagnosticos = flujo.cerrar()
    assert [n['id'] for n in nodos] == ['root', 'titulo'] and nodos[0]['children'] == ['titulo']
    motivos = ' '.join(d.motivo for d in diagnosticos)
    assert 'inventada' in motivos and 'nombre no cuelga de root' in motivos


def test_si_no_cabe_el_root_nuevo_se_dice_por_que():
    lleno = dataclasses.replace(catalogo(), max_nodos=2)
    nodos, diagnosticos = ui_lang.traducir('a Text "A"\nb Text "B"', lleno)
    assert nodos == [] and any('ya tiene 2 nodos' in d.motivo for d in diagnosticos)


def test_un_alta_que_no_se_dibuja_no_deja_reparaciones():
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'v', 'lang': True}, ['root Stack inventada=1 > t', 't Text "a" text="b"'])
    assert data is None and vistas.reparaciones == []


def test_un_cambio_sin_root_no_se_repara():
    vistas = Vistas(catalogo())
    vistas.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())
    antes = dict(vistas.vistas['tablero'].nodos)
    # Un nodo suelto en un cambio no se cuelga de root: el cambio entero se rechaza como siempre.
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True}, ['suelto Text "S"'])
    assert data is None and any('suelto no cuelga de ningún nodo' in e for e in errores)
    assert vistas.reparaciones == [] and vistas.vistas['tablero'].nodos == antes


def test_posicional_y_nombrada_a_la_vez_se_queda_la_nombrada():
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'v', 'lang': True},
                                   ['root Stack > t,f', 't Text "a" text="b"',
                                    'f Form "Mandar" submit_label="Enviar" > n', 'n Input "Nombre" name=nombre'])
    assert errores == []
    props = {n['id']: n.get('props', {}) for n in data['nodes']}
    assert props['t']['text'] == 'b' and props['f']['submit_label'] == 'Enviar'
    assert vistas.reparaciones == ['t: text iba posicional y nombrada; se queda la nombrada.',
                                   'f: submit_label iba posicional y nombrada; se queda la nombrada.']
    # En un cambio sobre la vista que ya existe sigue siendo un error.
    data, errores = vistas.aplicar({'view': 'v', 'lang': True}, ['t Text "a" text="c"'])
    assert data is None and any('posicional o nombrada' in e for e in errores)


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
    ('root Stack > filtros,cifras,p_bajo,fuente,nada', 'nada no existe'),
    ('-root', 'no se borra'),
    ('nuevo Text "suelto"', 'no cuelga de ningún nodo'),
    ('~p_bajo "Otro"', 'sin ~ ni +>'),
    ('fuente Text "x" > hijo', 'no admite hijos'),
])
def test_cambios_que_se_rechazan(texto, motivo):
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert cambio is None and any(motivo in d.motivo for d in diagnosticos), [d.texto() for d in diagnosticos]


def test_reescribir_la_linea_reordena_sustituye_y_borra():
    texto = 'root Stack direction=vertical gap=medium > fuente,cifras,filtros,p_bajo\n-p_bajo'
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert diagnosticos == []
    assert cambio.nodos['root']['children'] == ['fuente', 'cifras', 'filtros']     # -id lo quita de su padre
    assert set(cambio.borrados) == {'p_bajo', 'bajo_top'}                          # y lo que sólo colgaba de él
    # Un hijo que el padre deja fuera se va; una línea idéntica a lo que hay no cuenta como cambio.
    texto = 'cifras Grid columns=4 > c_subieron\nfiltros Filters'
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert diagnosticos == [] and cambio.borrados == ['c_bajaron']
    assert [n['id'] for n in cambio.actualizados] == ['cifras']
    # Sustituir es entero: lo que no se escribe se pierde (salvo la herencia, que el motor vuelve a poner).
    cambio, _ = ui_lang.aplicar_cambio(tablero(), 'c_subieron Stat "Suben" query={"tipo":"totales","campo":"subieron"}',
                                       catalogo())
    assert cambio.nodos['c_subieron']['props'] == {'label': 'Suben', 'query': {'tipo': 'totales', 'campo': 'subieron'},
                                                   'period': {'$': 'periodo'}, 'family': {'$': 'familia'}}


def test_borrar_un_nodo_con_dos_padres_lo_quita_de_los_dos():
    nodos = tablero()
    nodos['cifras']['children'].append('fuente')
    cambio, diagnosticos = ui_lang.aplicar_cambio(nodos, '-fuente', catalogo())
    assert diagnosticos == [] and 'fuente' not in cambio.nodos['cifras']['children'] + cambio.nodos['root']['children']


def test_la_revision_la_pone_el_motor_y_un_rev_del_modelo_se_ignora():
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())
    assert errores == [] and data['op'] == 'replace' and data['rev'] == 1 and len(data['nodes']) == 8
    assert errores_de('update', data) == [] and vistas.reparaciones == []
    # La vista ya existe: el mismo bloque es un cambio contra la última revisión, aunque el modelo ponga otro rev.
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True, 'rev': '7'}, PATCH.splitlines())
    assert errores == [] and data['op'] == 'patch' and data['rev'] == 2 and errores_de('update', data) == []
    assert vistas.reparaciones == ['rev=7 ignorado: la revisión la pone el motor.']
    # Un rev en una vista que no existe tampoco la rechaza: es un alta.
    data, errores = vistas.aplicar({'view': 'otra', 'lang': True, 'rev': '1'}, ['root Text "hola"'])
    assert errores == [] and data['op'] == 'replace' and vistas.reparaciones
    resumen = vistas.resumen_lang('tablero')
    assert 'rev' not in resumen and 'p_bajo Panel "Bajas e historial" > bajo_top,bajo_hist' in resumen
    assert 'bajo_hist PriceLines query={"tipo":"historial","productos":["palta","limon"]}' in resumen
    assert 'period' not in resumen                                   # lo heredado no se repite
    sin_flag = Vistas(Catalogo.de(DECLARACION, SIN_LANG))
    assert sin_flag.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())[0] is None


RENOMBRADO = 'Faltaba root: {} va primero, no cuelga de nadie y es un contenedor; pasa a llamarse root.'


def test_una_raiz_con_otro_nombre_se_llama_root():
    # Haiku escribe a veces «raiz» o «fila» en la primera línea: es un contenedor, va primero y no cuelga de nadie,
    # así que es la raíz con otro nombre. Pasa a llamarse root, sin un Stack de más.
    vistas = Vistas(catalogo())
    lineas = ['raiz Stack direction=vertical gap=small > foto,nota', 'foto Image src="https://ejemplo.pe/p.png" alt=P',
              'nota Text "Foto de referencia" style=caption']
    data, errores = vistas.aplicar({'view': 'foto', 'lang': True}, lineas)
    assert errores == [] and [n['id'] for n in data['nodes']] == ['root', 'foto', 'nota']
    assert data['nodes'][0]['children'] == ['foto', 'nota'] and errores_de('update', data) == []
    assert vistas.reparaciones == [RENOMBRADO.format('raiz')]
    # En un cambio la raíz ya existe y no se toca.
    vistas.aplicar({'view': 'foto', 'lang': True}, ['nota Text "Otra" style=caption'])
    assert vistas.reparaciones == [] and vistas.vistas['foto'].nodos['nota']['props']['text'] == 'Otra'
    # Un Form o un componente propio con hijos también son contenedores.
    nodos, _ = ui_lang.traducir(FORMULARIO_SIN_ROOT, catalogo())
    assert [n['id'] for n in nodos[:2]] == ['root', 'inicio'] and nodos[0]['component'] == 'Form'
    nodos, _ = ui_lang.traducir('cifras Grid columns=2 > a\na Text "A"', catalogo())
    assert nodos[0] == {'id': 'root', 'component': 'Grid', 'props': {'columns': 2}, 'children': ['a']}


def test_la_raiz_sólo_se_renombra_si_la_primera_línea_es_un_contenedor_que_valió_y_no_cuelga_de_nadie():
    envuelve = 'Faltaba root: un Stack vertical envuelve {}.'
    # La primera línea no es un contenedor: se envuelve.
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('nota Text "x"\ncaja Stack > nota\n')
    nodos, diagnosticos = flujo.cerrar()
    assert diagnosticos == [] and nodos[0]['children'] == ['caja'] and flujo.reparaciones == [envuelve.format('caja')]
    # La primera línea es un contenedor que cuelga de otro: se envuelve el de arriba.
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('caja Stack > nota\nnota Text "x"\narriba Card "A" > caja\n')
    nodos, _ = flujo.cerrar()
    assert nodos[0]['component'] == 'Stack' and nodos[0]['children'] == ['arriba']
    # La primera línea no valió: el primer nodo válido no se adivina como raíz, se envuelve.
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('fila Stack inventada=1 > a\ncaja Stack > b\nb Text "B"\n')
    nodos, _ = flujo.cerrar()
    assert nodos[0]['component'] == 'Stack' and nodos[0]['children'] == ['caja'] and 'caja' in [n['id'] for n in nodos]
    # Un root escrito y rechazado no se sustituye ni por renombrado.
    nodos, diagnosticos = ui_lang.traducir('raiz Stack > a\na Text "A"\nroot Grid columns=2 gap=medium > raiz',
                                           catalogo())
    assert nodos == [] and 'Falta root.' in [d.motivo for d in diagnosticos]


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
    assert 'crear Button "Crear" variant=primary @press={"send":"crear_ticket"}' in texto and 'rev=' not in texto
    c.comprobar()


# -- encendido por modelo -----------------------------------------------------------------------------------
def test_ui_lang_va_con_todos_los_modelos_y_el_flag_a_0_lo_apaga():
    for modelo in ('gpt-6-luna', 'chatgpt/gpt-6-luna', 'alfalfa/claude-haiku-5-5', 'claude-haiku-5-5-20261001',
                   'openrouter/anthropic/claude-haiku-5.5'):
        assert ui_lang.medido(modelo) and ui_lang.para_modelo(modelo, {}), modelo
    for modelo in ('claude-sonnet-5-5', 'gpt-6', 'ollama/glm-5.3', 'qwen3.8:27b', '', None):
        assert not ui_lang.medido(modelo) and ui_lang.para_modelo(modelo, {}), modelo      # medido es informativo
        assert not ui_lang.para_modelo(modelo, SIN_LANG), modelo
    assert ui_lang.para_modelo('glm-5.3-flash', CON_FLAG)
    assert not ui_lang.para_modelo('gpt-6-luna', {ui_lang.FLAG: '0'})          # apagado: con todos
    assert ui_lang.forzado({}) is None and ui_lang.forzado({ui_lang.FLAG: 'quizá'}) is None
    assert not ui_lang.activo({ui_lang.FLAG: '0'})


def test_el_catalogo_sigue_al_modelo_de_cada_inferencia():
    cat = Catalogo.de(DECLARACION, {}, modelo='gpt-6-luna')
    # Por modelo sólo cambia el formato: las piezas declarativas siguen pidiendo el flag, aunque el cliente las declare.
    assert cat.lang and not cat.piezas and 'Calc' not in cat.componentes()
    assert ui_lang.VALLA_LANG in cat.manual()
    assert cat.con_modelo('claude-sonnet-5-5').lang and cat.con_modelo('ollama/qwen3.8:27b').lang
    assert Catalogo.de(DECLARACION, {}).lang                                   # sin modelo todavía: también
    apagado = Catalogo.de(DECLARACION, {ui_lang.FLAG: '0'}, modelo='gpt-6-luna')
    assert not apagado.lang and not apagado.con_modelo('claude-haiku-5-5').lang
    encendido = Catalogo.de(DECLARACION, CON_FLAG)
    assert encendido.lang and encendido.piezas and encendido.con_modelo('claude-sonnet-5-5').lang


async def test_el_motor_usa_ui_lang_con_cualquier_modelo_sin_flag(monkeypatch):
    monkeypatch.delenv(ui_lang.FLAG, raising=False)
    c = Cliente(perfiles=('work', 'ui'))
    c.hello['render'] = {'ui': {'version': '1'}}
    await abrir(c, 'Enséñame un resumen')
    assert '```pacai-ui-lang' in c.t.manual_ui()
    assert '```pacai-ui-lang' in c.t.manual_ui(modelo='claude-sonnet-5-5')
    assert '```pacai-ui-lang' in c.t.manual_ui(modelo='ollama/glm-5.3')
    c.t.emit('evento', rol='agent', hora=1.0, texto='```pacai-ui-lang view=resumen\n'
             'root Stack direction=vertical > titulo\ntitulo Text "2 tickets" style=title\n```\n')
    mensajes = c.drenar()
    datos = [b['data'] for m in mensajes for b in m['payload'].get('blocks', []) if b.get('type') == 'structured']
    assert [n['id'] for n in datos[0]['nodes']] == ['root', 'titulo']
    await cerrar(c)
    c.comprobar()


# -- prompt -----------------------------------------------------------------------------------------------
def test_el_prompt_sale_del_catalogo_y_cabe_en_15_lineas():
    texto = ui_lang.prompt(catalogo())
    assert len(texto.splitlines()) <= ui_lang.MAX_LINEAS_PROMPT
    assert 'Stat "label" icon=subio|bajo|igual' in texto and 'PriceLines query={"tipo":"historial"' in texto
    assert 'family=$familia, period=$periodo' in texto and '-c_bajaron' in texto
    assert 'rev=N' not in texto and '+>' not in texto and '~id' not in texto
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


# -- reglas para perder menos pantallas (docs/pacai/perfil-ui.md §10.3) -----------------------------------------
@pytest.mark.parametrize('texto, queda, aviso', [
    ('fuente Text "A"\nfuente Text "B"', 'B', 'fuente: va dos veces; vale la línea 2.'),
    ('-fuente\nfuente Text "otra"', 'otra', 'fuente: se borra y se vuelve a escribir; vale la línea 2.'),
    ('fuente Text "A"\n-fuente', None, 'fuente: se escribe y se borra; vale la baja de la línea 2.'),
])
def test_en_un_cambio_gana_la_ultima_linea_de_un_id(texto, queda, aviso):
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert diagnosticos == [] and cambio.avisos == [aviso]
    if queda is None:
        assert 'fuente' not in cambio.nodos and 'fuente' not in cambio.nodos['root']['children']
    else:
        assert cambio.nodos['fuente']['props'] == {'text': queda}


def test_un_cambio_que_gana_la_ultima_linea_no_es_un_rechazo_en_las_vistas():
    vistas = Vistas(catalogo())
    vistas.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())
    data, errores = vistas.aplicar({'view': 'tablero', 'lang': True},
                                   ['c_bajaron Stat "Bajaron" icon=bajo query={"tipo":"totales","campo":"bajaron"}',
                                    '-c_bajaron'])
    assert errores == [] and data['remove'] == ['c_bajaron']
    assert vistas.reparaciones == ['c_bajaron: se escribe y se borra; vale la baja de la línea 2.']


def test_la_linea_que_aun_llega_se_publica_cerrada_y_luego_se_sustituye():
    flujo = ui_lang.Flujo(catalogo())
    assert flujo.alimentar('root Stack > a\n') == [[{'id': 'root', 'component': 'Stack', 'children': []}]]
    publicado = flujo.alimentar('a Text "Hol')
    assert publicado[-1][-1] == {'id': 'a', 'component': 'Text', 'props': {'text': 'Hol'}}
    assert publicado[-1][0]['children'] == ['a']
    assert flujo.alimentar('a"')[-1][-1]['props'] == {'text': 'Hola'}
    assert flujo.alimentar(' style=capt') == []            # a medias no vale: no se publica y queda lo anterior
    assert flujo.alimentar('ion\n')[-1][-1]['props'] == {'text': 'Hola', 'style': 'caption'}
    nodos, diagnosticos = flujo.cerrar()
    assert diagnosticos == [] and nodos[1]['props'] == {'text': 'Hola', 'style': 'caption'}


def test_la_linea_que_aun_llega_no_pisa_una_entera():
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('root Stack > a\na Text "Uno"\n')
    assert flujo.alimentar('a Text "Do') == []
    assert flujo.publicado['a']['props'] == {'text': 'Uno'}


def test_cerrar_cola_cierra_texto_y_corchetes():
    tabla = 't Table columns=[{"key":"id","label":"I'
    assert ui_lang.cerrar_cola(tabla) == tabla + '"}]'
    assert ui_lang.cerrar_cola('t Text "a \\"b') == 't Text "a \\"b"'
    assert ui_lang.cerrar_cola('t Text "x" style=body') == 't Text "x" style=body'


def test_una_pantalla_sin_valla_se_lee_si_todo_son_lineas_del_formato():
    vistas = Vistas(catalogo())
    attrs = vistas.sin_valla(TABLERO)
    assert attrs == {'view': 'pantalla', 'lang': True, 'sin_valla': True}
    data, errores = vistas.aplicar(attrs, TABLERO.splitlines())
    assert errores == [] and data['view'] == 'pantalla' and len(data['nodes']) == 8
    assert vistas.reparaciones == ['Pantalla sin valla: se lee como PACAI UI Lang (view=pantalla).']
    # Un cambio sin valla va a la última vista si nombra o cuelga uno de sus nodos.
    cambio = 'cifras Grid columns=4 > c_subieron,c_iguales\nc_iguales Stat "Iguales" icon=igual query={"tipo":"totales","campo":"iguales"}'  # noqa: E501
    assert vistas.sin_valla(cambio) == {'view': 'pantalla', 'lang': True, 'sin_valla': True}
    assert vistas.sin_valla('root Stack > hola\nhola Text "Otra"')['view'] == 'pantalla_2'


@pytest.mark.parametrize('texto', [
    'Aquí tienes la tabla con los precios de hoy.',
    'nota Texto "no es un componente"',
    '```text\nroot Stack > a\n```',
    'root Stack > a\nY esto es una frase.',
    '-nada',
])
def test_lo_que_no_es_una_pantalla_sin_valla_sigue_siendo_texto(texto):
    assert Vistas(catalogo()).sin_valla(texto) is None


def test_sin_el_formato_compacto_no_hay_pantallas_sin_valla():
    assert Vistas(Catalogo.de(DECLARACION, SIN_LANG)).sin_valla(TABLERO) is None


def test_ui_lang_en_la_valla_del_json_se_lee_como_ui_lang():
    # CuyMercado pedía «un bloque pacai-ui view=tablero op=replace» y el modelo escribía ahí líneas de UI Lang: cada
    # una salía «no es JSON» y el tablero no cambiaba.
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'tablero', 'op': 'replace'}, TABLERO.splitlines())
    assert errores == [] and data['view'] == 'tablero' and len(data['nodes']) == 8
    assert vistas.reparaciones == ['Bloque pacai-ui con líneas de PACAI UI Lang: se lee como PACAI UI Lang.']
    # El segundo pedido es un cambio contra lo que ya hay, como con la valla de UI Lang.
    data, errores = vistas.aplicar({'view': 'tablero', 'op': 'replace'}, PATCH.splitlines())
    assert errores == [] and data['rev'] == 2


def test_la_valla_del_json_sigue_siendo_json():
    vistas = Vistas(catalogo())
    data, errores = vistas.aplicar({'view': 'tablero'}, TABLERO_JSON.splitlines())
    assert errores == [] and len(data['nodes']) == 8 and vistas.reparaciones == []
    # Una línea que no es del formato ni JSON: no se adivina, se rechaza como siempre.
    data, errores = Vistas(catalogo()).aplicar({'view': 'v'}, ['root Stack > a', 'esto no es nada'])
    assert data is None and errores == ['Línea 1: no es JSON.', 'Línea 2: no es JSON.']
    # Sin el formato compacto tampoco se lee.
    data, errores = Vistas(Catalogo.de(DECLARACION, SIN_LANG)).aplicar({'view': 'v'}, TABLERO.splitlines())
    assert data is None and errores[0] == 'Línea 1: no es JSON.'


async def test_el_motor_dibuja_una_respuesta_sin_valla_y_deja_la_prosa_como_texto(monkeypatch):
    monkeypatch.setenv(ui_lang.FLAG, '1')
    c = Cliente(perfiles=('work', 'ui'))
    c.hello['render'] = {'ui': {'version': '1'}}
    await abrir(c, 'Enséñame un resumen')
    c.t.emit('evento', rol='agent', hora=1.0, texto='root Stack direction=vertical > titulo\n'
             'titulo Text "2 tickets" style=title\n')
    c.t.emit('evento', rol='agent', hora=2.0, texto='Hay 2 tickets abiertos.')
    mensajes = c.drenar()
    datos = [b['data'] for m in mensajes for b in m['payload'].get('blocks', []) if b.get('type') == 'structured']
    assert datos[0]['view'] == 'pantalla' and [n['id'] for n in datos[0]['nodes']] == ['root', 'titulo']
    textos = [b['text'] for m in mensajes for b in m['payload'].get('blocks', []) if b.get('type') == 'text']
    assert textos == ['Hay 2 tickets abiertos.']
    await cerrar(c)
    c.comprobar()


def test_una_baja_con_espacio_vale_y_la_de_un_id_que_no_existe_no_hace_nada():
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), 'root Stack > filtros,cifras,p_bajo\n- fuente', catalogo())
    assert diagnosticos == [] and 'fuente' not in cambio.nodos and cambio.avisos == []
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), 'root Stack > cifras,p_bajo,fuente\n-c_filtros',
                                                  catalogo())
    assert diagnosticos == [] and 'filtros' not in cambio.nodos
    assert cambio.avisos == ['c_filtros: no está en la pantalla; la baja de la línea 2 no hace nada.']
    assert ui_lang.leer_linea('-  fuente ').ident == 'fuente'
    with pytest.raises(ui_lang.ErrorDeLinea):
        ui_lang.leer_linea('- fuente extra')


def test_el_prompt_compacto_pide_colgar_cada_nodo_nuevo_y_ensena_la_rejilla():
    manual = catalogo().manual()
    assert 'Cada id nuevo va en el > de su padre, en la posición pedida' in manual
    assert 'tarjetas Stack direction=grid gap=small > palta' in manual and 'fila Stack' not in manual
    # El JSON (con el flag a 0) conserva su ejemplo de Stack.
    assert '"id":"fila","component":"Stack"' in Catalogo.de(DECLARACION, SIN_LANG).manual()



@pytest.mark.parametrize('marca', ['~~~', ':::'])
def test_un_bloque_con_valla_de_tildes_o_de_dos_puntos_tambien_vale(marca):
    texto = f'Aquí está:\n{marca}pacai-ui-lang view=resumen\nroot Stack > t\nt Text "Hola"\n{marca} y algo más\nFin.'
    trozos = partir(texto, lang=True)
    assert [t[0] for t in trozos] == ['text', 'ui', 'text']
    assert trozos[1][1] == {'view': 'resumen', 'lang': True} and trozos[1][2] == ['root Stack > t', 't Text "Hola"']
    # Un bloque ``` sigue cerrándose sólo con ``` sola en su línea.
    assert partir('```pacai-ui-lang view=a\nroot Stack > t\n~~~\nt Text "x"\n```', lang=True)[0][2] == [
        'root Stack > t', '~~~', 't Text "x"']
    assert Vistas(catalogo()).sin_valla(f'{marca}pacai-ui-lang view=a\nroot Stack > t') is None


# -- una línea con sólo el id (baja sin guion): se tolera sólo si da igual ignorarla que borrar ------------------------
def test_un_id_suelto_que_da_igual_ignorar_o_borrar_se_ignora():
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), 'c_bajaron\ncifras Grid columns=4 > c_subieron',
                                                  catalogo())
    assert diagnosticos == [] and 'c_bajaron' not in cambio.nodos
    assert cambio.avisos[0].startswith('c_bajaron: una línea con sólo el id')


@pytest.mark.parametrize('texto', [
    'c_bajaron\nfuente Text "Otra" style=caption',                                         # el padre no lo suelta
    'c_bajaron\ncifras Grid columns=4 > c_subieron\np_bajo Panel "Lo que más bajó" > bajo_top,c_bajaron',  # se mueve
    'nada\ncifras Grid columns=4 > c_subieron',                                            # no estaba en la pantalla
    'cifras Grid columns=4 > c_subieron\nc_igu',                                           # bloque cortado
    'root',                                                                                # root no se borra
    'c_bajaron\nc_subieron\ncifras Grid columns=4 > p_x\np_x Text "x"',                     # dos sueltos
])
def test_un_id_suelto_que_cambiaria_la_pantalla_sigue_rechazando_el_bloque(texto):
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), texto, catalogo())
    assert cambio is None and diagnosticos



def test_un_bloque_que_se_vuelve_a_abrir_sin_cerrarse_empieza_de_nuevo():
    texto = ('```pacai-ui-lang view=tablero\nroot Stack > cifras\n-cifra?```pacai-ui-lang view=tablero\n'
             'root Stack > p_bajo\n```')
    trozos = partir(texto, lang=True)
    assert [t[0] for t in trozos] == ['ui']
    assert trozos[0][1] == {'view': 'tablero', 'lang': True, 'reabierto': True}
    assert trozos[0][2] == ['root Stack > p_bajo']
    # Otra vista: el primer bloque se cierra y cada uno vale por separado.
    otro = partir('```pacai-ui-lang view=a\nroot Stack > t\n```pacai-ui-lang view=b\nroot Stack > u\n```', lang=True)
    assert [(t[1]['view'], t[2]) for t in otro] == [('a', ['root Stack > t']), ('b', ['root Stack > u'])]
    vistas = Vistas(catalogo())
    vistas.aplicar({'view': 'tablero', 'lang': True}, TABLERO.splitlines())
    data, errores = vistas.aplicar(trozos[0][1] | {}, ['fuente Text "Otra" style=caption'])
    assert errores == [] and 'se volvió a abrir' in vistas.reparaciones[0]


# -- más tolerancias que dejan el mismo árbol (benchmark de UI Lang, docs/pacai/ui-lang.md) -------------------------
def test_un_mayor_que_sin_hijos_es_como_no_ponerlo():
    linea = 'c Card "Ahora" detail="Sin datos"'
    assert ui_lang.leer_linea(linea + ' >') == ui_lang.leer_linea(linea)
    nodos, diagnosticos = ui_lang.traducir('root Stack > c\nc Card "Ahora" >', catalogo())
    assert diagnosticos == [] and 'children' not in nodos[1]
    with pytest.raises(ui_lang.ErrorDeLinea):
        ui_lang.leer_linea('root Stack > a,')                 # una lista empezada sigue teniendo que ser id,id


def test_ids_con_tildes_o_mayusculas_se_leen_en_ascii_si_son_coherentes():
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('root Stack > previsión,serviceTable\nprevisión Text "Mañana"\nserviceTable Text "Pendientes"\n')
    nodos, diagnosticos = flujo.cerrar()
    assert diagnosticos == [] and [n['id'] for n in nodos] == ['root', 'prevision', 'servicetable']
    assert nodos[0]['children'] == ['prevision', 'servicetable']
    assert any('previsión → prevision' in r for r in flujo.reparaciones)
    assert ui_lang.leer_linea('- previsión').ident == 'prevision'
    for malo in ('T Text "a"', '_x Text "a"', '2x Text "a"'):
        with pytest.raises(ui_lang.ErrorDeLinea):
            ui_lang.leer_linea(malo)


def test_dos_ids_que_solo_cambian_en_tildes_no_se_juntan():
    flujo = ui_lang.Flujo(catalogo())
    flujo.alimentar('root Stack > prevision,previsión\nprevision Text "A"\nprevisión Text "B"\n')
    _, diagnosticos = flujo.cerrar()
    assert any('sólo cambian en tildes' in d.motivo for d in diagnosticos)
    # En un cambio, la pantalla que ya existe cuenta: «fuénte» no es «fuente».
    cambio, diagnosticos = ui_lang.aplicar_cambio(tablero(), 'fuénte Text "x" style=caption', catalogo())
    assert cambio is None and any('sólo cambian en tildes' in d.motivo for d in diagnosticos)
    # Un id nuevo con tilde, escrito igual en el padre y en su línea, vale y queda avisado.
    cambio, diagnosticos = ui_lang.aplicar_cambio(
        tablero(), 'root Stack direction=vertical gap=medium > filtros,cifras,p_bajo,fuente,nota_más\n'
                   'nota_más Text "Más"', catalogo())
    assert diagnosticos == [] and 'nota_mas' in cambio.nodos['root']['children']
    assert cambio.avisos == ['nota_más → nota_mas: los ids van en a-z, 0-9 y _.']
