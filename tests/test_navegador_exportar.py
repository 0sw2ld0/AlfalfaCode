"""Un flujo comprobado se exporta sin datos; lo ambiguo o incompleto no se simula."""
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alfalfa.navegador import TOOLS
from alfalfa.navegador_exportar import (
    ExportacionNavegador,
    Paso,
    Propuesta,
    RegistroExportacion,
    guardar_spec,
    renderizar,
    validar_ruta,
)
from alfalfa.proveedores.nativo.herramientas import WorkspaceTools
from alfalfa.stdio import Transport

URL = 'https://privado.ejemplo.test/ruta-secreta?ticket=no-exportar'
NOMBRE = 'Campo privado ${process.env.INYECCION}'
BOTON = 'Crear pedido con "etiqueta privada"'
VALOR = 'valor-literal-que-no-debe-salir'
TEXTO = 'Pedido privado confirmado'


def observar(registro, arbol, args=None, **extra):
    registro.registrar('browser_read', args or {}, dict(url=URL, arbol=arbol, recortado=False, **extra))


def iniciar():
    registro = RegistroExportacion()
    registro.registrar('browser_navigate', {'url': URL}, {'url': URL})
    return registro


def flujo():
    registro = iniciar()
    observar(registro, f'- textbox «{NOMBRE}» [e17]')
    registro.registrar('browser_type', {'ref': 'e17', 'text': VALOR},
                        {'url': URL, 'elemento': f'textbox «{NOMBRE}»', 'valor': VALOR})
    observar(registro, f'- button «{BOTON}» [e22]')
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': URL, 'elemento': f'button «{BOTON}»'})
    observar(registro, f'- text «{TEXTO}»')
    registro.registrar('browser_wait', {'text': TEXTO}, {'url': URL, 'cumplida': True})
    return registro


def test_exporta_acciones_y_asercion_sin_datos_de_pagina():
    registro = flujo()
    # Una observación adicional no transforma el flujo ni aparece como acción Playwright.
    registro.registrar('browser_console', {}, {'mensajes': [{'texto': VALOR}]})
    propuesta, tarjeta = registro.cerrar(True)
    script = renderizar(propuesta)
    assert tarjeta['pasos'] == 4
    assert [v['clave'] for v in tarjeta['variables']] == [
        'ALFALFA_E2E_BASE_URL', 'ALFALFA_E2E_NOMBRE_2', 'ALFALFA_E2E_VALOR_2',
        'ALFALFA_E2E_NOMBRE_3', 'ALFALFA_E2E_TEXTO_4']
    assert "page.getByRole('textbox', { name: variable('ALFALFA_E2E_NOMBRE_2'), exact: true })" in script
    assert "await paso2.fill(variable('ALFALFA_E2E_VALOR_2'));" in script
    assert 'await paso3.click();' in script and 'await expect(texto4).toBeVisible();' in script
    assert 'toHaveCount(1)' in script and 'destino.origin === base.origin' in script
    for dato in (URL, 'privado.ejemplo.test', 'ruta-secreta', NOMBRE, BOTON, VALOR, TEXTO, '[e17]', '[e22]'):
        assert dato not in script and dato not in json.dumps(tarjeta, ensure_ascii=False)
    assert 'evaluate(' not in script and '.nth(' not in script and 'storageState' not in script


def test_no_ofrece_turnos_incompletos_o_sin_asercion():
    assert flujo().cerrar(False) == (None, None)
    registro = iniciar()
    observar(registro, f'- button «{BOTON}» [e22]')
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': URL, 'elemento': f'button «{BOTON}»'})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'espera final' in tarjeta['motivo']


@pytest.mark.parametrize('nombre', ['browser_handoff', 'browser_eval', 'browser_upload', 'browser_download',
                                    'browser_compare'])
def test_paso_manual_no_genera_prueba_parcial(nombre):
    registro = flujo()
    registro.registrar(nombre, {'script': VALOR, 'paths': [VALOR]}, {'url': URL})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'motivo' in tarjeta
    assert VALOR not in json.dumps(tarjeta)


def test_otro_origen_o_redireccion_no_se_exporta():
    otro = 'https://otro.ejemplo.test/'
    registro = RegistroExportacion()
    registro.registrar('browser_navigate', {'url': URL}, {'url': otro})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'más de un origen' in tarjeta['motivo']

    registro = iniciar()
    observar(registro, f'- button «{BOTON}» [e22]')
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': otro, 'elemento': f'button «{BOTON}»'})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'más de un origen' in tarjeta['motivo']


def test_una_ruta_mismo_origen_tambien_va_por_variable():
    registro = flujo()
    otra = 'https://privado.ejemplo.test/ruta-dos-no-exportar'
    registro.registrar('browser_navigate', {'url': otra}, {'url': otra})
    registro.registrar('browser_read', {}, {'url': otra, 'arbol': f'- text «{TEXTO}»'})
    registro.registrar('browser_wait', {'text': TEXTO}, {'url': otra, 'cumplida': True})
    propuesta, tarjeta = registro.cerrar(True)
    assert 'ALFALFA_E2E_RUTA_5' in [v['clave'] for v in tarjeta['variables']]
    script = renderizar(propuesta)
    assert 'ruta-dos-no-exportar' not in script and "ruta(base, variable('ALFALFA_E2E_RUTA_5'))" in script


@pytest.mark.parametrize('modo,meta', [
    ('diff', {'modo': 'diff', 'solicitado': 'diff', 'completa': True}),
    ('diff', {'modo': 'tree', 'solicitado': 'diff', 'completa': True}),
    ('tree', {'modo': 'tree', 'solicitado': 'tree', 'completa': False}),
])
def test_diff_o_recorte_a12_no_se_trata_como_arbol_completo(modo, meta):
    registro = iniciar()
    observar(registro, f'- button «{BOTON}» [e22]')
    observar(registro, f'- button «{BOTON}» [e22]', {'mode': modo}, lectura=meta)
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': URL, 'elemento': f'button «{BOTON}»'})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'árbol completo reciente' in tarjeta['motivo']


@pytest.mark.parametrize('caso', ['duplicado', 'sin_prueba_cuy', 'ref_distinta'])
def test_no_adivina_un_elemento(caso):
    registro = iniciar()
    arbol = f'- button «{BOTON}» [e22]'
    if caso == 'duplicado':
        arbol += f'\n- button «{BOTON}» [e23]'
    observar(registro, arbol)
    resultado = {'url': URL}
    if caso != 'sin_prueba_cuy':
        resultado['elemento'] = f'button «{BOTON}»'
    registro.registrar('browser_click', {'ref': 'e23' if caso == 'ref_distinta' else 'e22'}, resultado)
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'elemento efectivo y único' in tarjeta['motivo']


def test_fallo_mutante_no_se_repite_y_lectura_fallida_invalida_observacion():
    registro = flujo()
    registro.fallo('browser_click')
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'No se confirmó' in tarjeta['motivo']

    registro = iniciar()
    observar(registro, f'- button «{BOTON}» [e22]')
    registro.fallo('browser_read')
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': URL, 'elemento': f'button «{BOTON}»'})
    assert registro.cerrar(True)[0] is None


def test_escritura_con_submit_o_contrasena_se_deja_manual():
    for extra, args in (('', {'submit': True}), (' (contraseña)', {}), ('', {'clear': False})):
        registro = iniciar()
        observar(registro, f'- textbox «{NOMBRE}» [e17]{extra}')
        registro.registrar('browser_type', {'ref': 'e17', 'text': VALOR, **args},
                            {'url': URL, 'elemento': f'textbox «{NOMBRE}»'})
        propuesta, tarjeta = registro.cerrar(True)
        assert propuesta is None and 'manual' in tarjeta['motivo']


@pytest.mark.parametrize('arbol,marca', [(' (envía el formulario)', {}), ('', {'envio_formulario': True})])
def test_el_click_que_envio_un_formulario_no_se_exporta(arbol, marca):
    registro = iniciar()
    observar(registro, f'- button «{BOTON}» [e22]{arbol}')
    registro.registrar('browser_click', {'ref': 'e22'}, {'url': URL, 'elemento': f'button «{BOTON}»', **marca})
    propuesta, tarjeta = registro.cerrar(True)
    assert propuesta is None and 'formulario' in tarjeta['motivo']


def test_rol_manipulado_no_se_interpela_en_typescript():
    propuesta = Propuesta('id', (Paso('abrir'), Paso('pulsar', "button'); evil();"), Paso('comprobar')))
    with pytest.raises(ValueError, match='no reproducible'):
        renderizar(propuesta)


@pytest.mark.parametrize('ruta', ['/tmp/prueba.spec.ts', 'tests/e2e/../fuera.spec.ts',
                                  'tests/e2e/sub/prueba.spec.ts', 'tests/e2e/prueba.ts', None])
def test_ruta_no_autorizada(ruta):
    with pytest.raises(ValueError, match='tests/e2e'):
        validar_ruta(ruta)


def test_guarda_exclusivamente_y_no_sigue_enlaces(tmp_path):
    propuesta, tarjeta = flujo().cerrar(True)
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    ruta = tarjeta['ruta']
    assert guardar_spec(proyecto, ruta, propuesta) == ruta
    creado = proyecto / ruta
    contenido = creado.read_text()
    assert contenido == renderizar(propuesta)
    with pytest.raises(ValueError, match='no se sobrescribe'):
        guardar_spec(proyecto, ruta, propuesta)
    assert creado.read_text() == contenido

    fuera = tmp_path / 'fuera'
    fuera.mkdir()
    otro = tmp_path / 'otro'
    (otro / 'tests').mkdir(parents=True)
    (otro / 'tests/e2e').symlink_to(fuera, target_is_directory=True)
    with pytest.raises(OSError):
        guardar_spec(otro, ruta, propuesta)
    assert list(fuera.iterdir()) == []

    raiz_enlace = tmp_path / 'raiz-enlace'
    raiz_enlace.symlink_to(fuera, target_is_directory=True)
    with pytest.raises(OSError):
        guardar_spec(raiz_enlace, ruta, propuesta)
    assert list(fuera.iterdir()) == []


@pytest.fixture
def motor(tmp_path):
    transporte = Transport(io.StringIO())
    manager = SimpleNamespace(transport=transporte, task={'cwd': str(tmp_path), 'status': 'completed', 'modo': 'auto'},
                              read_only=False, cola_encargos=SimpleNamespace(activa=False),
                              home=tmp_path / 'hogar', approval=transporte.approval(tmp_path, auto=True))
    manager.exportacion_navegador = ExportacionNavegador(manager)
    transporte.exportar_prueba = manager.exportacion_navegador.guardar
    return manager


def comando(tarjeta):
    return json.dumps({'tipo': 'exportar_prueba_navegador', 'id': tarjeta['id'], 'ruta': tarjeta['ruta']})


async def test_despacho_registra_solo_resultados_y_el_comando_es_quien_escribe(motor, tmp_path):
    exportacion = motor.exportacion_navegador
    exportacion.empezar()
    pasos = [
        ('browser_navigate', {'url': URL}, {}),
        ('browser_read', {}, {'arbol': f'- button «{BOTON}» [e22]'}),
        ('browser_click', {'ref': 'e22'}, {'elemento': f'button «{BOTON}»'}),
        ('browser_read', {}, {'arbol': f'- text «{TEXTO}»'}),
        ('browser_wait', {'text': TEXTO}, {'cumplida': True}),
    ]
    runtime = SimpleNamespace(manager=motor, task=motor.task, excluded_tools=(), tool_specs=TOOLS)
    herramientas = WorkspaceTools(runtime)
    motor.navegador = SimpleNamespace(llamar=AsyncMock(side_effect=[dict(url=URL, **r) for _, _, r in pasos]))
    motor.pruebas_interfaz = SimpleNamespace(navegador=AsyncMock())
    for nombre, args, resultado in pasos:
        real = await herramientas.call(nombre, args)
        assert real == dict(url=URL, **resultado)
        motor.pruebas_interfaz.navegador.assert_awaited_with(runtime, nombre, real)
    motor.task['cierre'] = {'resumen': 'Hecho.', 'prueba_interfaz': {'estado': 'incompleta'}}
    exportacion.terminar()
    tarjeta = motor.task['cierre']['prueba_navegador']
    assert tarjeta['pasos'] == 3
    assert motor.task['cierre']['prueba_interfaz'] == {'estado': 'incompleta'}
    assert not (tmp_path / 'tests').exists()
    motor.transport.receive(comando(tarjeta))
    await motor.transport.exportacion
    archivo = tmp_path / tarjeta['ruta']
    assert 'await paso2.click();' in archivo.read_text()
    assert 'Prueba guardada en' in motor.transport.output.getvalue()
    motor.transport.receive(comando(tarjeta))
    await motor.transport.exportacion
    assert 'ya no está disponible' in motor.transport.output.getvalue()


@pytest.mark.parametrize('restriccion', ['plan', 'solo_lectura', 'cambio_a_plan', 'obsoleta', 'turno_activo', 'hogar'])
async def test_guardado_respeta_el_turno_y_los_limites_del_motor(motor, tmp_path, restriccion):
    exportacion = motor.exportacion_navegador
    exportacion.registro = flujo()
    exportacion.terminar()
    tarjeta = motor.task['cierre']['prueba_navegador']
    if restriccion == 'plan':
        motor.task['modo'] = 'plan'
    elif restriccion == 'solo_lectura':
        motor.read_only = True
    elif restriccion == 'cambio_a_plan':
        motor.transport.pending_settings['modo'] = 'plan'
    elif restriccion == 'obsoleta':
        exportacion.empezar()
    elif restriccion == 'turno_activo':
        motor.transport.in_turn = True
    else:
        motor.home = tmp_path / 'tests/e2e'
    motor.transport.receive(comando(tarjeta))
    await motor.transport.exportacion
    assert not (tmp_path / 'tests').exists()
    assert json.loads(motor.transport.output.getvalue())['tipo'] == 'error'


async def test_manual_espera_autorizacion_y_no_guarda_si_empieza_otro_turno(motor, tmp_path):
    exportacion = motor.exportacion_navegador
    exportacion.registro = flujo()
    exportacion.terminar()
    tarjeta = motor.task['cierre']['prueba_navegador']
    motor.task['modo'] = 'manual'
    motor.transport.receive(comando(tarjeta))
    await asyncio.sleep(0)
    permiso = json.loads(motor.transport.output.getvalue())
    assert permiso['tipo'] == 'permiso' and permiso['motivo'] == 'edicion'
    assert not (tmp_path / 'tests').exists()
    exportacion.empezar()
    motor.transport.receive(json.dumps({'tipo': 'permiso', 'id': permiso['id'], 'aprobado': True}))
    await motor.transport.exportacion
    assert not (tmp_path / 'tests').exists()
    assert 'ya no está disponible' in motor.transport.output.getvalue()
