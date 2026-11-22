"""Los valores enumerados de `--pacai` en inglés y `snake_case` (§8 de PACAI 0.3): estados, motivos, modos,
acciones… de la extensión `alfalfa.*`, y los nombres de la herramienta del cliente `browser`.

La lista de valores sale del código, no de memoria: un recorrido del AST de `alfalfa/` busca cada texto que se
asigna a un campo enumerado (`estado`, `motivo`, `modo`…), directamente o por una constante del módulo, y se le
suman las constantes que definen cada conjunto (`modos.MODOS`, `bajo_demanda.MOTIVOS`…). Si alguien añade un
valor nuevo en castellano y no lo traduce en `alfalfa/pacai/nombres.py`, la prueba falla y dice cuál."""
import ast
import asyncio
import collections
import json
import re
from pathlib import Path

import pytest

from alfalfa.pacai.nombres import (
    CAMPOS,
    NAVEGADOR_CAMPOS,
    NAVEGADOR_VALORES,
    ORDEN_VALORES,
    TIPOS,
    VALORES,
    YA_EN_INGLES,
    en_ingles,
    peticion_del_navegador,
    respuesta_del_navegador,
)
from alfalfa.stdio import Transport
from tests.pacai_comun import Cliente, abrir, cerrar

RAIZ = Path(__file__).resolve().parents[1] / 'alfalfa'
ID = re.compile(r'[a-z][a-z0-9_]*')
# Campos v2 cuyos valores son enumerados (los compara una máquina). `tipo` no está: en v2 también es el nombre del
# evento, y sus valores enumerados (los de `importadas`, los permisos del navegador…) se cubren por sus constantes.
ENUMERADOS = ('estado', 'motivo', 'motivos', 'modo', 'accion', 'clase', 'almacen', 'alcance', 'ambito', 'origen',
              'paso', 'aviso', 'agotado', 'fase', 'contexto', 'permisos', 'decision', 'instalacion', 'capacidad',
              'resultado')
# Nombres que no son valores enumerados aunque se asignen a esos campos: claves de diccionario y códigos ajenos.
NO_ENUMERADOS = {'decision'}


def _arboles():
    return {p: ast.parse(p.read_text()) for p in RAIZ.rglob('*.py') if 'pacai' not in p.parts}


def _constantes(arboles):
    """NOMBRE → textos que se le asignan a nivel de módulo (también `A, B = 'a', 'b'`)."""
    constantes = collections.defaultdict(set)
    for arbol in arboles.values():
        for n in arbol.body:
            if not isinstance(n, ast.Assign):
                continue
            for objetivo in n.targets:
                nombres = [objetivo] if isinstance(objetivo, ast.Name) else \
                    (list(objetivo.elts) if isinstance(objetivo, ast.Tuple) else [])
                valores = list(n.value.elts) if isinstance(n.value, ast.Tuple) and len(nombres) > 1 else [n.value]
                for nombre, valor in zip(nombres, valores):
                    if isinstance(nombre, ast.Name) and isinstance(valor, ast.Constant) \
                            and isinstance(valor.value, str):
                        constantes[nombre.id].add(valor.value)
    return constantes


def valores_del_codigo():
    """(campo v2, valor) de cada texto que el código asigna a un campo enumerado, con su archivo."""
    arboles = _arboles()
    constantes = _constantes(arboles)

    def textos(nodo):
        if isinstance(nodo, ast.Constant):
            return [nodo.value]
        if isinstance(nodo, (ast.Tuple, ast.List)):
            return [t for x in nodo.elts for t in textos(x)]
        if isinstance(nodo, ast.IfExp):
            return textos(nodo.body) + textos(nodo.orelse)
        nombre = nodo.id if isinstance(nodo, ast.Name) else (nodo.attr if isinstance(nodo, ast.Attribute) else None)
        return sorted(constantes.get(nombre, ())) if nombre else []

    hallados = set()
    for ruta, arbol in arboles.items():
        for n in ast.walk(arbol):
            pares = []
            if isinstance(n, ast.Dict):
                pares = [(k.value, v) for k, v in zip(n.keys, n.values)
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            elif isinstance(n, ast.Call):
                pares = [(k.arg, k.value) for k in n.keywords if k.arg]
            elif isinstance(n, ast.Assign) and len(n.targets) == 1:
                objetivo = n.targets[0]
                if isinstance(objetivo, ast.Subscript) and isinstance(objetivo.slice, ast.Constant):
                    pares = [(objetivo.slice.value, n.value)]
                elif isinstance(objetivo, ast.Attribute):
                    pares = [(objetivo.attr, n.value)]
            for campo, nodo in pares:
                if campo in ENUMERADOS:
                    for valor in textos(nodo):
                        if isinstance(valor, str) and ID.fullmatch(valor) and valor not in NO_ENUMERADOS:
                            hallados.add((campo, valor, ruta.relative_to(RAIZ.parent).as_posix()))
    return hallados


def conjuntos_oficiales():
    """Las constantes que definen cada conjunto de valores, con el campo inglés que los lleva en PACAI."""
    from alfalfa import aprendizaje, contenido_externo, encargos, navegador, navegador_actividad, subagentes
    from alfalfa.apps import MOTIVO as APP
    from alfalfa.aprendizaje.sesion import MOTIVO as APRENDIZAJE
    from alfalfa.compactacion import MODOS_CONTEXTO
    from alfalfa.importadas.orden import ESTADOS as IMPORTADAS
    from alfalfa.mcp_cliente.gestor import MOTIVO as MCP
    from alfalfa.permisos import bajo_demanda, modos
    from alfalfa.proveedores.nativo.herramientas import MOTIVO_DIAGNOSTICOS_SWIFT
    from alfalfa.proveedores.nativo.web import MODOS as BUSCADOR
    from alfalfa.reglas import PERMITIDA, PREGUNTAR, PROHIBIDA
    assert aprendizaje
    return [
        ('mode', modos.MODOS), ('permissions', modos.MODOS), ('mode', MODOS_CONTEXTO), ('context', MODOS_CONTEXTO),
        ('mode', BUSCADOR),
        ('reason', bajo_demanda.MOTIVOS + (modos.MOTIVO_EDICION, contenido_externo.MOTIVO, navegador.MOTIVO,
                                           APRENDIZAJE, MCP, APP, MOTIVO_DIAGNOSTICOS_SWIFT)),
        ('status', encargos.EN_MARCHA + encargos.FINALES), ('status', subagentes.TERMINADOS),
        ('status', tuple(IMPORTADAS)), ('status', tuple(navegador_actividad.ESTADOS_CUY)),
        ('decision', (PERMITIDA, PREGUNTAR, PROHIBIDA)), ('type', tuple(navegador.TIPOS_DE_PERMISO)),
    ]


def _traducido(clave, valor):
    """Lo que sale en PACAI para un valor bajo esa clave inglesa."""
    return en_ingles({next((k for k, v in CAMPOS.items() if v == clave), clave): valor})[clave]


def _comprobar(clave, valor, donde):
    if valor in YA_EN_INGLES:
        return None
    salida = VALORES.get(clave, {}).get(valor)
    if salida is None:
        return f'{donde}: {clave}={valor!r} no tiene traducción en nombres.VALORES'
    if not ID.fullmatch(salida):
        return f'{donde}: {clave}={valor!r} se traduce a {salida!r}, que no es snake_case'
    return None


def test_cada_valor_enumerado_del_codigo_tiene_su_traduccion_al_ingles():
    hallados = valores_del_codigo()
    assert len(hallados) > 80                                 # el recorrido encuentra de verdad los valores
    fallos = [f for campo, valor, ruta in sorted(hallados) if (f := _comprobar(CAMPOS.get(campo, campo), valor, ruta))]
    for clave, conjunto in conjuntos_oficiales():
        fallos += [f for valor in conjunto if (f := _comprobar(clave, valor, 'constante'))]
    assert fallos == []


def test_las_traducciones_son_snake_case_y_ningun_valor_ingles_es_uno_castellano():
    castellanos = {v for tabla in VALORES.values() for v in tabla}
    for clave, tabla in VALORES.items():
        assert ID.fullmatch(clave), clave
        for origen, ingles in tabla.items():
            assert ID.fullmatch(ingles), (clave, origen, ingles)
            assert ingles == origen or ingles not in castellanos - YA_EN_INGLES, (clave, origen, ingles)
    assert _traducido('status', 'trabajando') == 'working' and _traducido('mode', 'ediciones') == 'accept_edits'
    assert _traducido('reasons', ['red', 'peligroso']) == ['network', 'dangerous']
    assert _traducido('summary', 'trabajando') == 'trabajando'           # un texto libre no se toca


def test_cada_evento_v2_tiene_tipo_en_ingles_o_su_traduccion_al_nucleo():
    from alfalfa.pacai.traductor import Traductor
    emitidos = set()
    for arbol in _arboles().values():
        for n in ast.walk(arbol):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'emit' and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                emitidos.add(n.args[0].value)
    # Los de `encargos`, `deshacer` y `aprendizaje` que se emiten con el tipo en una variable.
    emitidos |= {'deshacer_vista', 'deshecho', 'aprendido', 'habilidad', 'memoria', 'pendiente', 'volver'}
    assert len(emitidos) > 30
    # `permiso` lo atiende `Traductor.permiso` (desde el transporte); los demás, su `_v2_<tipo>` o la extensión.
    sin_nombre = [t for t in sorted(emitidos)
                  if t not in TIPOS and not hasattr(Traductor, '_v2_' + t) and not hasattr(Traductor, t)]
    assert sin_nombre == []
    assert all(ID.fullmatch(t) for t in TIPOS.values())


# -- lo que sale y lo que entra ----------------------------------------------
def test_los_eventos_salen_con_los_valores_en_ingles():
    c = Cliente()
    c.saludar()
    c.t.emit('subagentes', subagentes=[{'id': 'a1', 'nombre': 'R', 'tipo': 'explorador', 'estado': 'trabajando'}])
    c.t.emit('encargos', cola={'id': 'q', 'estado': 'pausada', 'modo': 'paralelo', 'permisos': 'ediciones',
                               'motivo': 'La ejecución se interrumpió.',
                               'encargos': [{'id': 'e', 'estado': 'cancelado'}]})
    c.t.emit('presupuesto', agotado='mes', aviso='sesion')
    c.t.emit('sesion', id='s', modo='omitir', contexto='ahorro')
    salida = {m['type']: m['payload'] for m in c.drenar()}
    assert salida['alfalfa.subagents']['subagents'][0] == {'id': 'a1', 'name': 'R', 'type': 'explorador',
                                                           'status': 'working'}
    assert salida['alfalfa.jobs']['queue'] == {'id': 'q', 'status': 'paused', 'mode': 'parallel',
                                               'permissions': 'accept_edits', 'reason': 'La ejecución se interrumpió.',
                                               'jobs': [{'id': 'e', 'status': 'cancelled'}]}
    assert salida['alfalfa.budget'] == {'exhausted': 'month', 'warning': 'session'}
    assert salida['alfalfa.session'] == {'id': 's', 'mode': 'bypass_permissions', 'context': 'saving'}


async def test_el_motivo_propio_de_un_permiso_y_sus_motivos_salen_en_ingles():
    c = Cliente()
    await abrir(c)
    from tests.pacai_comun import herramienta
    herramienta(c, 'run_command', {'argv': ['swift', 'build'], 'purpose': 'p'})
    tarea = asyncio.create_task(c.t.approval('.', False)(dict(argv=['swift', 'build'], purpose='p',
                                                              motivo='diagnosticos_swift')))
    await asyncio.sleep(0.02)
    peticion = c.del_agente('approval_request')[-1]['payload']
    assert peticion['reason'] == 'alfalfa.swift_diagnostics'
    tarea.cancel()
    c.t._cerrar_sesion()


@pytest.mark.parametrize('orden, payload, campo', [
    ('set_settings', {'mode': 'ediciones'}, 'mode'),          # el valor de v2 ya no vale
    ('set_settings', {'mode': 'nada'}, 'mode'),
    ('set_context_mode', {'mode': 'completo'}, 'mode'),
    ('jobs_command', {'action': 'iniciar'}, 'action'),
    ('jobs_command', {'action': 'prepare', 'jobs': ['a'], 'mode': 'paralelo'}, 'mode'),
])
def test_un_valor_desconocido_en_una_orden_es_un_error_claro_y_no_llega_al_motor(monkeypatch, orden, payload, campo):
    c = Cliente()
    c.saludar()
    recibidas = []
    monkeypatch.setattr(Transport, 'receive', lambda self, linea: recibidas.append(json.loads(linea)))
    error = [m for m in c.enviar('alfalfa.' + orden, payload) if m['type'] == 'error']
    assert len(error) == 1 and error[0]['payload']['code'] == 'invalid_message'
    assert 'Valor desconocido para ' + campo in error[0]['payload']['message'] and recibidas == []


@pytest.mark.parametrize('orden, payload, v2', [
    ('set_settings', {'mode': 'turbo'}, {'tipo': 'ajustes', 'modo': 'turbo'}),
    ('set_settings', {'mode': 'accept_edits'}, {'tipo': 'ajustes', 'modo': 'ediciones'}),
    ('set_settings', {'mode': 'bypass_permissions'}, {'tipo': 'ajustes', 'modo': 'omitir'}),
    ('set_context_mode', {'mode': 'full'}, {'tipo': 'contexto', 'modo': 'completo'}),
    ('jobs_command', {'action': 'pause'}, {'tipo': 'encargos', 'accion': 'pausar'}),
    ('jobs_command', {'action': 'clean'}, {'tipo': 'encargos', 'accion': 'limpiar'}),
])
def test_los_valores_de_una_orden_vuelven_a_v2(monkeypatch, orden, payload, v2):
    c = Cliente()
    c.saludar()
    recibidas = []
    monkeypatch.setattr(Transport, 'receive', lambda self, linea: recibidas.append(json.loads(linea)))
    c.enviar('alfalfa.' + orden, payload)
    assert recibidas == [v2]
    assert set(ORDEN_VALORES) <= {'set_settings', 'set_context_mode', 'jobs_command'}


# -- la herramienta del cliente `browser` -------------------------------------
def _peticiones_del_navegador():
    """Acciones y campos de cada petición del motor a la app (`_pedir`, `_con_permisos`, `pedir`), del AST."""
    arbol = ast.parse((RAIZ / 'navegador.py').read_text())
    acciones, campos = set(), set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('_pedir', 'pedir',
                                                                                              '_con_permisos'):
            textos = [a for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            acciones |= {a.value for a in textos if ID.fullmatch(a.value)}
            for a in n.args:
                if isinstance(a, ast.Dict):
                    campos |= {k.value for k in a.keys if isinstance(k, ast.Constant)}
                if isinstance(a, ast.Call) and getattr(a.func, 'id', None) == 'dict':
                    campos |= {k.arg for k in a.keywords if k.arg}
        if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'campos' for t in n.targets) \
                and isinstance(n.value, ast.Dict):
            campos |= {k.value for k in n.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return acciones - {'runtime'}, campos


def test_cada_accion_y_campo_que_el_motor_pide_al_navegador_tiene_su_nombre_en_ingles():
    acciones, campos = _peticiones_del_navegador()
    assert {'abrir', 'leer', 'pulsar', 'escribir', 'relevo', 'cancelar_sitio'} <= acciones
    assert sorted(acciones - set(NAVEGADOR_VALORES['action'])) == []
    iguales = {'url', 'max', 'ref', 'token'}               # ya son inglés en v2
    assert sorted(campos - set(NAVEGADOR_CAMPOS) - iguales) == []
    assert all(ID.fullmatch(v) for v in NAVEGADOR_CAMPOS.values())


def test_la_peticion_y_la_respuesta_del_navegador_se_traducen_en_los_dos_sentidos():
    v2 = {'accion': 'leer', 'modo': 'arbol', 'max': 500, 'sitios': {'permitidos': ['a.dev'], 'estricto': True},
          'visita_sitio': None, 'permitido': ['formulario'], 'direccion': 'abajo'}
    pacai = peticion_del_navegador(v2)
    assert pacai == {'action': 'read', 'mode': 'tree', 'max': 500, 'sites': {'allowed': ['a.dev'], 'strict': True},
                     'visit_site': None, 'granted': ['form'], 'direction': 'down'}
    respuesta = {'ok': True, 'result': {'url': 'https://a.dev', 'tree': '- e1', 'truncated': False,
                                        'consumed_visit': 'v1'},
                 'permission': {'type': 'site', 'started': False, 'url': 'https://b.dev', 'token': 't'}}
    assert respuesta_del_navegador(respuesta) == {
        'ok': True, 'resultado': {'url': 'https://a.dev', 'arbol': '- e1', 'recortado': False,
                                  'visita_consumida': 'v1'},
        'permiso': {'tipo': 'sitio', 'iniciada': False, 'url': 'https://b.dev', 'token': 't'}}


async def test_el_navegador_de_la_app_habla_ingles_por_pacai_y_v2_por_dentro():
    c = Cliente(herramientas=('browser',))
    await abrir(c)
    futuro = asyncio.get_running_loop().create_future()
    c.t.navegador_pendientes['n1'] = futuro
    c.t.emit('navegador', id='n1', accion='escribir', objetivo='Buscar', texto='hola', enviar=False, borrar=True,
             permitido=['contrasena'])
    inicio = [m for m in c.drenar() if m['type'] == 'tool_start'][0]
    assert inicio['payload']['arguments'] == {'action': 'type', 'target': 'Buscar', 'text': 'hola', 'submit': False,
                                              'clear': True, 'granted': ['password']}
    cuerpo = {'ok': True, 'permission': {'type': 'password', 'detail': 'Campo de contraseña'}}
    c.enviar('tool_end', {'tool_call_id': inicio['id'], 'status': 'success', 'output': [
        {'type': 'structured', 'schema': 'urn:alfalfa:browser-response', 'data': cuerpo}]}, reply_to=inicio['id'])
    assert futuro.result() == {'tipo': 'navegador_respuesta', 'id': 'n1', 'ok': True,
                               'permiso': {'tipo': 'contrasena', 'detalle': 'Campo de contraseña'}}
    await cerrar(c)
    c.comprobar()
