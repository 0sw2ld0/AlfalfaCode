"""La extensión `alfalfa.*` habla inglés y `snake_case`, como el núcleo de PACAI: sus tipos, los campos de sus
payloads, el bloque `payload.alfalfa` del núcleo y los campos de las órdenes del cliente. v2 no cambia."""
import asyncio
import json
import re

import pytest

from alfalfa.pacai.nombres import CAMPOS, TIPOS, de_la_orden, en_ingles
from alfalfa.stdio import Transport
from tests.pacai_comun import Cliente, abrir, cerrar, herramienta, reposo

# Los eventos v2 que más usa la app, con sus campos de verdad (sacados de lo que emite el motor).
EVENTOS = [
    ('sesion', dict(id='s1', cwd='/x', modelo='m', esfuerzo='high', modo='auto', contexto=10, tokens=3,
                    bifurcada_de='s0', continuada_de='s-1', coste=0.1)),
    ('presupuesto', dict(sesion={'gastado': 1.0, 'tope': 5}, mes={'gastado': 2.0, 'tope': 50, 'mes': '2026-11'},
                         aviso='sesion', agotado=None)),
    ('procesos', dict(procesos=[dict(id='p1', argv=['npm', 'run', 'dev'], cwd='.', proposito='Servidor', vivo=True,
                                     desde=1.0, codigo=None, detenido=False, sin_sandbox=False, ultimas=['ok'])])),
    ('despierto', dict(activo=True)),
    ('cuota', dict(servicio='ollama', cuotas=[dict(nombre='semanal', proveedor='ollama', usado_pct=40,
                                                   renueva='2026-11-20')])),
    ('preparando', dict(texto='Preparando deshacer…')),
    ('revision_codigo', dict(revision=dict(turno='t1', estado='disponible', autor='m', modelo='m', hallazgos=[],
                                           resumen='ok', coste=0.01, coste_sesion=0.2, tokens_sesion=9,
                                           reserva_maxima=1.0))),
    ('encargos', dict(cola=dict(id='q', estado='lista', modo='serie', paralelos=1, modelo='m', esfuerzo='high',
                                permisos='auto', encargos=[dict(id='e1', texto='uno', titulo='uno',
                                                                estado='pendiente')]))),
    ('aprendizaje_propuesto', dict(id='a1', clase='memoria', almacen='proyecto', diff='+x', motivo='porque',
                                   sesion='s1', turno='t1', creada=1.0)),
    ('aprendizaje_resuelto', dict(id='a1', aplicado=True, descartada=False, texto='Hecho')),
    ('quitado_de_cola', dict(id='c1', quitado=False, motivo='desconocido')),
    ('sesion_cambiada', dict(id='s1', eliminada=False, papelera=True, marcas={'fijada': True, 'titulo': 'x'})),
    ('subagentes', dict(subagentes=[dict(id='a1', nombre='Revisor', tipo='explorador', estado='terminado',
                                         actividad='leyendo', desde=1.0, hasta=2.0, pasos=3, informe='ok',
                                         modelo='m', error=None)])),
    ('contexto', dict(tokens=10, limite=100, desglose={'sistema': 1, 'herramientas': 2, 'mensajes': 3},
                      estimado=12, umbral=90, memoria=[{'ruta': 'AGENTS.md', 'tokens': 4}],
                      tarifa={'desde': 1, 'entrada': 0.1, 'salida': 0.2, 'modo': 'ahorro'})),
    ('evento', dict(rol='system', texto='Compactada', hora=1.0,
                    compactacion={'antes': 10, 'despues': 2, 'motivo': 'umbral', 'resumen': 'r'})),
]
ESPAÑOL = set(CAMPOS) - set(CAMPOS.values())


def _claves(valor):
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from _claves(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _claves(v)


def _extension_de(m):
    """Lo que es de la extensión en un mensaje: todo el payload de `alfalfa.*`, o su bloque `payload.alfalfa`."""
    if m['type'].startswith('alfalfa.'):
        return m['payload']
    return m['payload'].get('alfalfa')


@pytest.mark.parametrize('tipo, campos', EVENTOS, ids=[t for t, _ in EVENTOS])
def test_los_campos_de_la_extension_van_en_ingles_y_snake_case(tipo, campos):
    c = Cliente()
    c.saludar()
    c.t.emit(tipo, **campos)
    salida = [m for m in c.drenar() if _extension_de(m)]
    assert salida, tipo
    for m in salida:
        assert re.fullmatch(r'alfalfa\.[a-z][a-z0-9_]*', m['type']) or '.' not in m['type']
        claves = set(_claves(_extension_de(m)))
        assert not claves & ESPAÑOL, (m['type'], sorted(claves & ESPAÑOL))
        assert all(re.fullmatch(r'[a-z][a-z0-9_]*', k) for k in claves if k not in ('AGENTS.md',)), claves
    c.comprobar()


def test_un_ejemplo_entero_de_la_extension():
    c = Cliente()
    c.saludar()
    c.t.emit('presupuesto', sesion={'gastado': 1.0, 'tope': 5}, aviso='sesion')
    c.t.emit('quitado_de_cola', id='c1', quitado=True)
    assert [(m['type'], m['payload']) for m in c.drenar()] == [
        ('alfalfa.budget', {'session': {'spent': 1.0, 'cap': 5}, 'warning': 'sesion'}),
        ('alfalfa.dequeued', {'id': 'c1', 'removed': True})]


async def test_el_bloque_alfalfa_del_nucleo_va_en_ingles():
    c = Cliente()
    await abrir(c)
    c.t.emit('plan', pasos=[{'paso': 'Leer', 'estado': 'completed'}], explicacion='Voy por partes')
    tarea = asyncio_tarea(c, dict(argv=['rm', 'x'], purpose='Borrar', motivo='peligroso', justificacion='Limpia'))
    await reposo()
    plan, *_ = c.del_agente('plan_updated')
    assert plan['payload']['alfalfa'] == {'explanation': 'Voy por partes'}
    peticion = c.del_agente('approval_request')[-1]
    assert peticion['payload']['alfalfa'] == {'argv': ['rm', 'x'], 'purpose': 'Borrar', 'justification': 'Limpia'}
    c.t.emit('error', texto='Sin visión', motivo='sin_vision', rechazado={'texto': 'hola'})
    assert c.del_agente('error')[-1]['payload']['alfalfa'] == {'reason': 'sin_vision', 'rejected': {'text': 'hola'}}
    tarea.cancel()
    c.t.emit('permiso_resuelto', id='x', aprobado=False)
    await cerrar(c, 'cortado')
    cierre = c.t.t.cerrar_todo({'estado': 'completed', 'codigo_salida': 0, 'tokens': 4, 'coste': 0.5})
    assert cierre[-1]['payload']['alfalfa'] == {'status': 'completed', 'exit_code': 0, 'tokens': 4, 'cost': 0.5}


def asyncio_tarea(c, args):
    herramienta(c, 'run_command', {'argv': args['argv'], 'purpose': args['purpose']})
    return asyncio.create_task(c.t.approval('.', False)(args))


@pytest.mark.parametrize('orden, payload, v2', [
    ('set_settings', {'model': 'm', 'effort': 'high', 'mode': 'auto'},
     {'tipo': 'ajustes', 'modelo': 'm', 'esfuerzo': 'high', 'modo': 'auto'}),
    ('compact', {'instructions': 'breve'}, {'tipo': 'compactar', 'instrucciones': 'breve'}),
    ('set_context_mode', {'mode': 'ahorro'}, {'tipo': 'contexto', 'modo': 'ahorro'}),
    ('set_budget', {'session': 5, 'month': None}, {'tipo': 'presupuesto', 'sesion': 5, 'mes': None}),
    ('set_keep_awake', {'active': False}, {'tipo': 'mantener_despierto', 'activo': False}),
    ('set_diagnostics', {'active': True}, {'tipo': 'diagnosticos', 'activo': True}),
    ('set_swift_diagnostics', {'active': True}, {'tipo': 'diagnosticos_swift', 'activo': True}),
    ('set_signing', {'active': False}, {'tipo': 'firma', 'activo': False}),
    ('resolve_learning', {'id': 'a1', 'apply': True}, {'tipo': 'aprendizaje', 'id': 'a1', 'aplicar': True}),
    ('review_code', {'turn': 't1', 'model': 'm'}, {'tipo': 'revisar_codigo', 'turno': 't1', 'modelo': 'm'}),
    ('export_browser_test', {'id': 'f' * 32, 'path': 'prueba'},
     {'tipo': 'exportar_prueba_navegador', 'id': 'f' * 32, 'ruta': 'prueba'}),
    ('jobs_command', {'action': 'prepare', 'jobs': ['a'], 'mode': 'serie', 'parallel': 1},
     {'tipo': 'encargos', 'accion': 'prepare', 'encargos': ['a'], 'modo': 'serie', 'paralelos': 1}),
    ('stop_process', {'id': 'p1'}, {'tipo': 'detener_proceso', 'id': 'p1'}),
])
def test_las_ordenes_del_cliente_llevan_campos_en_ingles_y_llegan_al_motor_en_v2(monkeypatch, orden, payload, v2):
    c = Cliente()
    c.saludar()
    recibidas = []
    monkeypatch.setattr(Transport, 'receive', lambda self, linea: recibidas.append(json.loads(linea)))
    c.enviar('alfalfa.' + orden, payload)
    assert recibidas == [v2]


def test_la_tabla_no_pisa_claves_y_cada_tipo_es_snake_case():
    assert en_ingles({'model': 'a', 'modelo': 'b'}) == {'model': 'a', 'modelo': 'b'}
    assert en_ingles([{'texto': 1, 'otra_cosa': {'estado': 2}}]) == [{'text': 1, 'otra_cosa': {'status': 2}}]
    assert de_la_orden({'session': 1, 'text': 'x'}) == {'sesion': 1, 'text': 'x'}
    for nombre in list(TIPOS.values()) + list(CAMPOS.values()):
        assert re.fullmatch(r'[a-z][a-z0-9_]*', nombre), nombre
