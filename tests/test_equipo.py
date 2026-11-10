"""Reglas compartidas y registro de permisos, siempre con un proyecto y un hogar de prueba."""
import asyncio
import io
import json
import os
import stat
from contextlib import contextmanager

import pytest

from alfalfa import equipo, proveedores
from alfalfa.presupuesto import PresupuestoAgotado
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport, check_command
from alfalfa.terminal import TerminalManager
from tests.test_compactar_sesion import Cliente
from tests.test_encargos import git

POLITICA = '''
modelos = ["ollama/autor", "openai/revisor"]

[permisos]
modos = ["manual", "plan", "omitir"]

[presupuesto]
sesion = 2
mes = 8

[[regla]]
patron = ["pwd"]
decision = "preguntar"

[[regla]]
patron = ["echo", "prohibido"]
decision = "prohibida"
'''


@pytest.fixture(autouse=True)
def hogar_temporal(tmp_path, monkeypatch):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'hogar'))


def escribir(raiz, texto=POLITICA):
    ruta = raiz / equipo.ORIGEN
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(texto)
    return ruta


@contextmanager
def sesion(tmp_path, texto=POLITICA, **opciones):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir(exist_ok=True)
    escribir(raiz, texto)
    opciones.setdefault('model', 'ollama/autor')
    transporte = Transport(io.StringIO())
    gestor = TerminalManager('Prueba de equipo.', cwd=raiz, home=tmp_path / 'hogar',
                             transport=transporte, **opciones)
    try:
        yield gestor, transporte, raiz
    finally:
        gestor.close()


def registros(gestor):
    return [json.loads(s) for s in gestor.auditoria_equipo.ruta.read_text().splitlines()]


async def responder(transporte, trabajo, aprobado, sesion=False):
    async def esperar():
        while not transporte.pending:
            if trabajo.done():
                await trabajo
                pytest.fail('La operación terminó sin pedir el permiso de equipo.')
            await asyncio.sleep(.01)
        return next(iter(transporte.pending))

    ident = await asyncio.wait_for(esperar(), 10)
    transporte.receive(json.dumps(dict(tipo='permiso', id=ident, aprobado=aprobado, sesion=sesion)))


def test_toma_la_politica_de_la_raiz_git_y_no_una_mas_permisiva_de_una_subcarpeta(tmp_path):
    (tmp_path / '.git').mkdir()
    raiz = tmp_path / 'subcarpeta'
    raiz.mkdir()
    escribir(tmp_path)
    escribir(raiz, 'modelos = ["otro"]')
    reglas = equipo.cargar(raiz)
    assert reglas.raiz == tmp_path
    reglas.comprobar_modelo('autor')
    reglas.comprobar_modelo('ollama/autor')
    with pytest.raises(equipo.ErrorDeEquipo, match='Modelo fuera'):
        reglas.comprobar_modelo('otro')
    with pytest.raises(equipo.ErrorDeEquipo, match='Modelo fuera'):
        reglas.comprobar_modelo('openai/autor')


@pytest.mark.parametrize('texto', [
    'permisos = "manual"', 'modelos = []', 'modelos = [3]', 'regla = 3',
    '[presupuesto]\nsesion = 0', '[presupuesto]\nmes = true', '[presupuesto]\nsesion = inf',
    '[presupuesto]\ndiario = 1', '[permisos]\nmodos = ["inexistente"]',
    '[[regla]]\npatron = ["pwd"]\ndecision = "permitida"',
    '[[regla]]\npatron = ["pwd"]\ndecision = []',
    '[[regla]]\npatron = ["pwd"]\ndecision = "preguntar"\ncoincide = [["otra"]]',
    'modelos = [',
])
def test_politica_invalida_falla_cerrada(tmp_path, texto):
    escribir(tmp_path, texto)
    with pytest.raises(equipo.ErrorDeEquipo, match='equipo.toml'):
        equipo.cargar(tmp_path)


@pytest.mark.parametrize('enlace', ['archivo', 'carpeta', 'duro'])
def test_no_lee_reglas_a_traves_de_enlaces(tmp_path, enlace):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    externa = escribir(tmp_path)
    if enlace == 'carpeta':
        (raiz / equipo.CARPETA).symlink_to(externa.parent)
    else:
        destino = raiz / equipo.ORIGEN
        destino.parent.mkdir()
        if enlace == 'archivo':
            destino.symlink_to(externa)
        else:
            os.link(externa, destino)
    with pytest.raises(equipo.ErrorDeEquipo, match='equipo.toml'):
        equipo.cargar(raiz)


def test_cambios_stdio_y_topes_no_amplian_la_politica(tmp_path):
    with sesion(tmp_path, budget=1, monthly_budget=40) as (m, _, _):
        assert m.task['model'] == 'ollama/autor' and m.task['modo'] == 'manual'
        assert (m.presupuesto.sesion, m.presupuesto.mes) == (1, 8)
        m.cambiar_presupuesto(sesion=None, mes=99)
        assert (m.presupuesto.sesion, m.presupuesto.mes) == (2, 8)
        m.aplicar_ajustes({'model': 'openai/revisor', 'modo': 'plan'})
        for cambios in ({'model': 'otro', 'modo': 'manual'}, {'model': 'autor', 'modo': 'auto'}):
            with pytest.raises(equipo.ErrorDeEquipo):
                m.aplicar_ajustes(cambios)
            assert (m.task['model'], m.task['modo']) == ('openai/revisor', 'plan')
        m.cambiar_presupuesto(sesion=.5, mes=3)
        assert (m.presupuesto.sesion, m.presupuesto.mes) == (.5, 3)
        m.task['coste_usd'] = .5
        with pytest.raises(PresupuestoAgotado, match='agotó'):
            m.presupuesto.reservar('peticion', .001)
    with pytest.raises(equipo.ErrorDeEquipo, match='Modo fuera'):
        with sesion(tmp_path, mode='auto'):
            pass


def test_politica_no_elige_un_proveedor_ni_un_modo_mas_permisivo(tmp_path, monkeypatch):
    from alfalfa import terminal

    monkeypatch.setattr(terminal, 'modelo_por_defecto', lambda: 'local-del-usuario')
    with pytest.raises(equipo.ErrorDeEquipo, match='Modelo fuera'):
        with sesion(tmp_path, model=None):
            pass
    with pytest.raises(equipo.ErrorDeEquipo, match='Modo fuera'):
        with sesion(tmp_path, texto='[permisos]\nmodos = ["omitir"]'):
            pass


def test_comprobar_orden_aplica_la_misma_politica(tmp_path, monkeypatch, capsys):
    escribir(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert check_command(['echo', 'prohibido']) == 0
    assert json.loads(capsys.readouterr().out)['decision'] == 'prohibida'


async def test_modelo_real_de_la_peticion_y_subagente_se_comprueban_antes_de_enviar(tmp_path, monkeypatch):
    llamados = []

    async def falso(self, client, endpoint, payload, **kw):
        llamados.append(payload['model'])
        return {'ok': True}

    monkeypatch.setattr(NativeRuntime, '_request', falso)
    with sesion(tmp_path) as (m, _, _):
        rt = m.runtime()
        with pytest.raises(equipo.ErrorDeEquipo, match='Modelo fuera'):
            await rt.request(None, '/api/chat', {'model': 'revisor'})
        with pytest.raises(equipo.ErrorDeEquipo, match='Modelo fuera'):
            await m.subagentes.lanzar(rt, {'message': 'Lee.', 'model': 'otro'})
        assert not llamados and not m.subagentes.agentes
        await rt.request(None, '/api/chat', {'model': 'autor'})
        assert llamados == ['autor']


async def test_tope_rechaza_pago_sin_precio_y_usa_reserva_de_la_sesion(tmp_path, monkeypatch):
    class Simulado(proveedores.Proveedor):
        id = 'openai'

        def precio(self, modelo):
            return None

    async def prohibida(*args, **kw):
        pytest.fail('La petición no debe salir del motor.')

    monkeypatch.setattr(NativeRuntime, '_request', prohibida)
    with sesion(tmp_path, model='openai/revisor') as (m, _, _):
        rt = m.runtime()
        rt._proveedor = Simulado()
        with pytest.raises(equipo.ErrorDeEquipo, match='precio conocido'):
            await rt.request(None, '/api/chat', {'model': 'revisor'})
        rt._precio = {'entrada': 1.0, 'salida': 1.0}
        m.task['coste_usd'] = 2
        with pytest.raises(PresupuestoAgotado, match='equipo.toml'):
            await rt.request(None, '/api/chat', {'model': 'revisor'})


async def test_preguntar_incluso_en_omitir_y_prohibicion_gana_a_permiso_personal(tmp_path):
    hogar = tmp_path / 'hogar'
    hogar.mkdir(mode=0o700)
    (hogar / 'reglas.toml').write_text('[[regla]]\npatron = ["echo"]\ndecision = "permitida"\n')
    with sesion(tmp_path, mode='omitir') as (m, transporte, _):
        rt = m.runtime()
        with pytest.raises(ValueError, match='regla del equipo'):
            await rt.tools.call('run_command', {'argv': ['echo', 'prohibido'], 'purpose': 'Comprobar el bloqueo.'})
        trabajo = asyncio.create_task(rt.tools.call('run_command', {'argv': ['pwd'], 'purpose': 'Ver la carpeta.'}))
        try:
            await responder(transporte, trabajo, aprobado=False)
            with pytest.raises(ValueError, match='rechazado'):
                await trabajo
        finally:
            trabajo.cancel()
            await asyncio.gather(trabajo, return_exceptions=True)
        filas = registros(m)
        assert [r['estado'] for r in filas if r['tipo'] == 'fin'] == ['error', 'error']
        decision, = [r for r in filas if r['tipo'] == 'decision']
        assert decision['estado'] == 'rechazado' and decision['origen'] == 'cliente_stdio'
        assert decision['accion'] in [r['accion'] for r in filas if r['tipo'] == 'inicio']

        trabajo = asyncio.create_task(rt.tools.call('run_command', {'argv': ['pwd'], 'purpose': 'Ver la carpeta.'}))
        try:
            await responder(transporte, trabajo, aprobado=True, sesion=True)
            resultado = await trabajo
        finally:
            trabajo.cancel()
            await asyncio.gather(trabajo, return_exceptions=True)
        assert resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
        assert registros(m)[-1]['operacion'] == resultado['operation_id']
        assert not any(r['tipo'] == 'permiso_guardado' for r in registros(m))


async def test_aprobador_y_reutilizacion_quedan_registrados_sin_contenido(tmp_path):
    with sesion(tmp_path) as (m, transporte, raiz):
        rt = m.runtime()
        secreto = 'contenido-privado-sintetico'
        args = {'path': 'prueba.txt', 'content': secreto, 'expected_sha256': 'NEW'}
        trabajo = asyncio.create_task(rt.tools.call('write_file', args))
        try:
            await responder(transporte, trabajo, aprobado=True, sesion=True)
            escrito = await trabajo
        finally:
            trabajo.cancel()
            await asyncio.gather(trabajo, return_exceptions=True)
        await rt.tools.call('write_file', dict(args, content='dos', expected_sha256=escrito['after']))
        assert (raiz / 'prueba.txt').read_text() == 'dos'
        assert not transporte.pending
        filas = registros(m)
        decision, = [r for r in filas if r['tipo'] == 'decision']
        assert decision['estado'] == 'aprobado' and decision['origen'] == 'cliente_stdio'
        assert decision['uid'] == os.getuid() and decision['cuenta_local']
        guardada, = [r for r in filas if r['tipo'] == 'permiso_guardado']
        reusada, = [r for r in filas if r['tipo'] == 'permiso_reutilizado']
        assert guardada['claves'] == reusada['claves'] and guardada['accion'] == decision['accion']
        assert guardada['permiso'] == decision['permiso']
        log = m.auditoria_equipo.ruta
        assert secreto not in log.read_text() and 'prueba.txt' not in log.read_text()
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_politica_congelada_archivo_protegido_y_reanudacion_revoca_permisos_anteriores(tmp_path):
    with sesion(tmp_path) as (m, _, raiz):
        ruta = raiz / equipo.ORIGEN
        rt = m.runtime()
        with pytest.raises(ValueError, match='configuración de equipo'):
            rt.tools.path(equipo.ORIGEN, escritura=True)
        assert str(ruta) in dict(rt.process_sandbox.policy()[1]).values()
        # Simula una edición del mantenedor fuera de AlfalfaCode.
        ruta.write_text(POLITICA.replace('sesion = 2', 'sesion = 1'))
        assert m.presupuesto.sesion == 2
        m.task['aprobadas_en_sesion'] = ['permiso anterior']
        m.save()
        ident, hogar = m.task['id'], m.home
    m = TerminalManager(resume=ident, cwd=raiz, home=hogar)
    try:
        assert m.presupuesto.sesion == 1
        assert 'aprobadas_en_sesion' not in m.task
    finally:
        m.close()


def test_stdio_informa_y_conserva_ajustes_efectivos_con_un_cambio_prohibido(tmp_path):
    escribir(tmp_path)
    cliente = Cliente(tmp_path, '--modelo', 'ollama/autor', 'Lee una línea.')
    try:
        inicio = cliente.tipo('sesion')
        assert inicio['modelo'] == 'ollama/autor' and inicio['modo'] == 'manual'
        aviso = cliente.tipo('evento')
        assert 'Reglas de equipo activas' in aviso['texto']
        presupuesto = cliente.tipo('presupuesto')
        assert presupuesto['sesion']['tope'] == 2 and presupuesto['mes']['tope'] == 8
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        cliente.enviar(tipo='ajustes', modelo='otro', modo='auto')
        cliente.enviar(tipo='mensaje', texto='Sigue con la lectura.')
        assert 'Modelo fuera' in cliente.tipo('error')['texto']
        efectivo = cliente.tipo('sesion')
        assert efectivo['modelo'] == inicio['modelo'] and efectivo['modo'] == inicio['modo']
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        cliente.cerrar()
        assert cliente.proc.returncode == 0
    finally:
        cliente.matar()


async def test_encargo_paralelo_hereda_la_politica_cargada_por_el_padre(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    git(raiz, 'init', '-q')
    escribir(raiz, POLITICA.replace('sesion = 2', 'sesion = 7'))
    git(raiz, 'add', equipo.ORIGEN)
    git(raiz, 'commit', '-qm', 'Política inicial')
    vistos = []

    async def trabajo(hijo, texto, eco=False):
        vistos.append((hijo.equipo, hijo.presupuesto.sesion, hijo.task['model'], hijo.auditoria_equipo.ruta))
        hijo.task.update(status='completed', summary='Listo.')
        return 0

    monkeypatch.setattr(TerminalManager, '_run', trabajo)
    with sesion(tmp_path) as (m, _, _):
        # El repositorio avanza después de que la sesión carga las reglas.
        escribir(raiz, POLITICA.replace('sesion = 2', 'sesion = 7'))
        cola = m.cola_encargos
        cola.preparar(['Lee la política.'], 'paralelo')
        cola.iniciar()
        await cola.worker
        assert cola.datos['estado'] == 'completada'
        politica, tope, modelo, auditoria = vistos[0]
        assert politica is m.equipo and tope == 2 and modelo == 'ollama/autor'
        assert auditoria.is_file()
