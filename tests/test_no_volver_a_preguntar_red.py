"""«No volver a preguntar» también en las tarjetas de red y del modo (hallazgo M4 de la auditoría).

Antes sólo lo ofrecían las tarjetas que salen del sandbox y, desde #97, la del modo Manual cuando
el modelo mandaba `prefix_rule`, que casi nunca manda fuera de `require_escalated`:

- la tarjeta de red (`needs_network`) nunca lo ofrecía, así que `npm install` o `git push`
  preguntaban en cada sesión;
- la tarjeta del modo (Manual y Aceptar ediciones: cada orden que no sólo lee) sólo lo ofrecía
  con git; `make test` o `npm test` sin `prefix_rule`, nunca.

Ahora la de red guarda una regla `permitida` con `con_red = true`: esa orden, por su prefijo
estricto (ni tras `env` ni con otro programa), usa la red dentro del sandbox sin preguntar. No la
saca del sandbox, no vale para una orden peligrosa ni tras leer contenido externo, y las reglas
que preguntan o prohíben siguen mandando. La tarjeta de edición del modo Manual no lo ofrece.
"""
import asyncio
import io
import json
import os
import subprocess
import sys

import pytest

from alfalfa import reglas
from alfalfa.contenido_externo import MOTIVO as EXTERNO
from alfalfa.pacai.nombres import en_ingles
from alfalfa.permisos.bajo_demanda import SIEMPRE
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')


# ---------------------------------------------------------------- la regla con red
def test_con_red_solo_con_permitida_y_sin_fuera_del_sandbox():
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.leer({'regla': [{'patron': ['make', 'test'], 'decision': 'preguntar', 'con_red': True}]})
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.leer({'regla': [{'patron': ['make', 'test'], 'con_red': 'sí'}]})
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.leer({'regla': [{'patron': ['make', 'test'], 'con_red': True, 'fuera_del_sandbox': True}]})
    regla, = reglas.leer({'regla': [{'patron': ['make', 'test'], 'con_red': True}]}).reglas
    assert regla.con_red and not regla.sin_sandbox


def test_las_reglas_del_equipo_no_pueden_abrir_la_red(tmp_path):
    from alfalfa import equipo
    (tmp_path / '.alfalfa').mkdir()
    (tmp_path / '.alfalfa' / 'equipo.toml').write_text(
        '[[regla]]\npatron = ["npm", "install"]\ndecision = "permitida"\ncon_red = true\n')
    with pytest.raises(equipo.ErrorDeEquipo):
        equipo.cargar(tmp_path)


def test_guardar_con_red_escribe_una_regla_de_dentro_del_sandbox(tmp_path):
    nuevas = reglas.guardar_para_siempre(tmp_path, ['npm', 'install'], 'x', dentro_del_sandbox=True, con_red=True)
    guardada = nuevas.reglas[-1]
    assert guardada.con_red and not guardada.sin_sandbox
    texto = (tmp_path / reglas.ARCHIVO).read_text()
    assert 'con_red = true' in texto and 'fuera_del_sandbox' not in texto
    assert reglas.desde_hogar(tmp_path).reglas[-1].con_red
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.guardar_para_siempre(tmp_path, ['npm', 'install'], 'x', con_red=True)


def test_la_red_solo_para_la_orden_con_su_prefijo_estricto():
    r = reglas.leer({'regla': [
        {'patron': ['npm', 'install'], 'con_red': True},
        {'patron': ['make', 'test']},
        {'patron': ['git', 'push'], 'fuera_del_sandbox': True},
        {'patron': ['npm', 'install', '--global'], 'decision': 'preguntar'}]})
    assert r.con_red(['npm', 'install', 'lodash'])
    assert r.con_red(['bash', '-lc', 'cd web && npm install'])
    # Otra orden, otro programa con el mismo nombre o la misma tras un envoltorio: no.
    assert not r.con_red(['npm', 'ci'])
    assert not r.con_red(['./npm', 'install'])
    assert not r.con_red(['tools/npm', 'install'])
    for envoltorio in (['env'], ['env', 'PATH=/tmp'], ['sudo'], ['command'], ['nice']):
        assert not r.con_red(envoltorio + ['npm', 'install']), envoltorio
    # Una regla sin red no abre la red; una que sale del sandbox ya la lleva.
    assert not r.con_red(['make', 'test'])
    assert r.con_red(['git', 'push', 'origin', 'main'])
    # Lo que una regla manda preguntar sigue preguntando, y sólo un trozo de la línea no basta.
    assert not r.con_red(['npm', 'install', '--global', 'x'])
    assert not r.con_red(['bash', '-lc', 'npm install && curl x'])
    # Y no saca del sandbox.
    assert not r.fuera_del_sandbox(['npm', 'install'])


# ---------------------------------------------------------------- el prefijo que se ofrece
def test_dentro_del_sandbox_se_ofrece_la_suborden_de_programas_conocidos():
    dentro = dict(dentro_del_sandbox=True)
    assert reglas.prefijo_para_siempre(None, ['make', 'test'], **dentro) == ['make', 'test']
    assert reglas.prefijo_para_siempre(None, ['npm', 'install', 'lodash'], **dentro) == ['npm', 'install']
    assert reglas.prefijo_para_siempre(None, ['bash', '-lc', 'cd web && npm test'], **dentro) == ['npm', 'test']
    assert reglas.prefijo_para_siempre(None, ['git', 'push', 'origin', 'x'], **dentro) == ['git', 'push']
    # Fuera del sandbox, como antes: sólo git.
    assert reglas.prefijo_para_siempre(None, ['make', 'test']) is None
    # Nada de opciones, rutas, programas que no se conocen ni prefijos que ejecutan cualquier paquete.
    for argv in (['make', '-f', 'x.mk'], ['make', 'build/x'], ['npm', 'Install'], ['tools/npm', 'install'],
                 ['mv', 'src', 'dst'], ['npm', 'exec', 'cowsay'], ['pnpm', 'dlx', 'x'], ['uv', 'run', 'x.py'],
                 ['npm', 'run', 'build'], ['make'], ['bash', '-lc', 'make test && npm test']):
        assert reglas.prefijo_para_siempre(None, argv, **dentro) is None, argv
    # Lo que proponga el modelo sigue sus reglas de siempre.
    assert reglas.prefijo_para_siempre(['npm', 'exec'], ['npm', 'exec', 'x'], **dentro) is None
    assert reglas.prefijo_para_siempre(['uv', 'run', 'pytest'], ['uv', 'run', 'pytest', '-q'], **dentro) == \
        ['uv', 'run', 'pytest']


# ---------------------------------------------------------------- transporte y PACAI
def test_el_permiso_dice_lo_que_concede_no_volver_a_preguntar():
    async def ejercicio():
        salida = io.StringIO()
        aprobacion = Transport(salida).approval('/tmp', False)
        tarea = asyncio.create_task(aprobacion(dict(argv=['npm', 'install'], purpose='Instalar', motivo='red',
                                                    prefijo=['npm', 'install'], prefijo_alcance='red')))
        await asyncio.sleep(0)
        fila = json.loads(salida.getvalue().splitlines()[-1])
        tarea.cancel()
        return fila
    fila = asyncio.run(ejercicio())
    assert fila['prefijo'] == ['npm', 'install'] and fila['prefijo_alcance'] == 'red'
    assert en_ingles({'prefijo_alcance': 'red'}) == {'prefix_scope': 'network'}
    assert en_ingles({'prefijo_alcance': 'sin_sandbox'}) == {'prefix_scope': 'outside_sandbox'}
    assert en_ingles({'prefijo_alcance': 'sandbox'}) == {'prefix_scope': 'sandbox'}


# ---------------------------------------------------------------- el motor con el sandbox real
class Respuesta:
    def __init__(self, respuesta):
        self.respuesta, self.peticiones, self.denied = respuesta, [], 0

    async def __call__(self, args):
        self.peticiones.append(dict(args))
        return self.respuesta


@pytest.fixture
def motor(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    (raiz / 'Makefile').write_text('test:\n\t@echo probado\nlint:\n\t@echo limpio\n')
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    monkeypatch.chdir(raiz)
    hogar = tmp_path / 'alfalfa'
    m = TerminalManager('Prueba.', home=hogar)
    rt = m.runtime()
    eventos = []
    evento = m.event
    m.event = lambda task, rol, texto: (eventos.append(texto), evento(task, rol, texto))

    def llamar(aprobacion, **argumentos):
        rt.approval_policy = aprobacion
        return asyncio.run(rt.tools.call('run_command', dict(purpose='Prueba', **argumentos)))

    try:
        yield m, rt, llamar, hogar, eventos
    finally:
        m.close()


def _siempre_con_red(llamar, hogar):
    """«No volver a preguntar» en la tarjeta de red de `make test`."""
    primera = Respuesta(SIEMPRE)
    resultado = llamar(primera, argv=['make', 'test'], needs_network=True)
    peticion, = primera.peticiones
    assert peticion['motivos'] == ['red'] and peticion['prefijo'] == ['make', 'test']
    assert peticion['prefijo_alcance'] == 'red'
    assert resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
    guardada = reglas.desde_hogar(hogar).reglas[-1]
    assert guardada.texto() == 'make test' and guardada.con_red and not guardada.sin_sandbox


@real
def test_la_tarjeta_de_red_ofrece_no_volver_a_preguntar_y_la_siguiente_no_pregunta(motor):
    m, rt, llamar, hogar, eventos = motor
    _siempre_con_red(llamar, hogar)
    assert any('siguen dentro del sandbox, con red' in t for t in eventos)
    otra = Respuesta(None)
    resultado = llamar(otra, argv=['make', 'test'], needs_network=True)
    assert otra.peticiones == [] and resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
    assert 'probado' in resultado['output']
    assert any(t.startswith('Red permitida siempre por') for t in eventos)


@real
@pytest.mark.parametrize('argumentos, motivo', [
    (dict(argv=['make', 'lint'], needs_network=True), 'red'),
    (dict(argv=['env', 'make', 'test'], needs_network=True), 'red'),
    (dict(argv=['env', 'FOO=1', 'make', 'test'], needs_network=True), 'red'),
    (dict(argv=['make', 'test'], needs_network=True, sandbox_permissions='require_escalated',
          justification='Necesita escribir fuera de la carpeta.'), 'salir_sandbox'),
    (dict(argv=['make', 'test'], sandbox_permissions='require_escalated',
          justification='Necesita escribir fuera de la carpeta.'), 'salir_sandbox'),
])
def test_lo_que_no_casa_la_regla_de_red_sigue_preguntando(motor, argumentos, motivo):
    m, rt, llamar, hogar, eventos = motor
    _siempre_con_red(llamar, hogar)
    otra = Respuesta(None)
    llamar(otra, **argumentos)
    peticion, = otra.peticiones
    assert peticion['motivo'] == motivo
    if motivo == 'salir_sandbox':
        # La regla de red no deja salir del sandbox, ni ofrece una regla de salir encima de la suya.
        assert 'prefijo' not in peticion


@real
def test_una_regla_que_pregunta_sigue_mandando_sobre_la_de_red(motor):
    m, rt, llamar, hogar, eventos = motor
    _siempre_con_red(llamar, hogar)
    rt.command_policy.reglas.reglas += reglas.leer({'regla': [
        {'patron': ['make', 'test'], 'decision': 'preguntar'}]}).reglas
    otra = Respuesta(None)
    llamar(otra, argv=['make', 'test'], needs_network=True)
    peticion, = otra.peticiones
    assert 'regla' in peticion['motivos'] and 'prefijo' not in peticion


@real
def test_tras_leer_contenido_externo_la_red_vuelve_a_preguntar_y_no_lo_ofrece(motor):
    m, rt, llamar, hogar, eventos = motor
    _siempre_con_red(llamar, hogar)
    rt.state[EXTERNO] = ['read_web']
    otra = Respuesta(None)
    llamar(otra, argv=['make', 'test'], needs_network=True)
    peticion, = otra.peticiones
    assert 'red' in peticion['motivos'] and EXTERNO in peticion['motivos'] and 'prefijo' not in peticion


@real
def test_una_orden_peligrosa_con_red_no_lo_ofrece(motor):
    m, rt, llamar, hogar, eventos = motor
    aprobacion = Respuesta(None)
    llamar(aprobacion, argv=['rm', '-rf', 'no-existe'], needs_network=True, prefix_rule=['rm', '-rf'])
    peticion, = aprobacion.peticiones
    assert 'peligroso' in peticion['motivos'] and 'prefijo' not in peticion


@real
def test_la_tarjeta_del_modo_lo_ofrece_sin_prefix_rule(motor):
    m, rt, llamar, hogar, eventos = motor
    m.task['modo'] = 'manual'
    primera = Respuesta(SIEMPRE)
    resultado = llamar(primera, argv=['make', 'test'])
    peticion, = primera.peticiones
    assert peticion['motivos'] == ['modo'] and peticion['prefijo'] == ['make', 'test']
    assert peticion['prefijo_alcance'] == 'sandbox' and resultado['sandbox'] == 'seatbelt'
    guardada = reglas.desde_hogar(hogar).reglas[-1]
    assert not guardada.con_red and not guardada.sin_sandbox
    otra = Respuesta(None)
    assert llamar(otra, argv=['make', 'test'])['exit_code'] == 0 and otra.peticiones == []
    # La regla del modo no abre la red: con red pregunta, y su tarjeta sí ofrece la regla con red.
    tercera = Respuesta(SIEMPRE)
    llamar(tercera, argv=['make', 'test'], needs_network=True)
    peticion, = tercera.peticiones
    assert peticion['motivos'] == ['red'] and peticion['prefijo_alcance'] == 'red'
    cuarta = Respuesta(None)
    llamar(cuarta, argv=['make', 'test'], needs_network=True)
    assert cuarta.peticiones == []


@real
def test_la_tarjeta_de_edicion_del_modo_manual_no_lo_ofrece(motor):
    """Una regla de reglas.toml vale para todas las carpetas y casa órdenes, no archivos: «no volver a
    preguntar por src/app.py» valdría en cualquier proyecto. Para eso está «Aceptar ediciones»."""
    m, rt, llamar, hogar, eventos = motor
    m.task['modo'] = 'manual'
    aprobacion = Respuesta(None)
    rt.approval_policy = aprobacion
    asyncio.run(rt.tools.call('write_file', dict(path='nota.txt', content='hola\n', expected_sha256='NEW')))
    peticion, = aprobacion.peticiones
    assert peticion['motivo'] == 'edicion' and peticion.get('para_sesion')
    assert 'prefijo' not in peticion and 'prefijo_alcance' not in peticion


def test_pacai_lleva_el_prefijo_y_su_alcance_en_ingles():
    from tests.pacai_comun import Cliente, abrir, herramienta

    async def ejercicio():
        c = Cliente()
        await abrir(c)
        herramienta(c, 'run_command', {'argv': ['npm', 'install'], 'purpose': 'p'})
        tarea = asyncio.create_task(c.t.approval('.', False)(dict(
            argv=['npm', 'install'], purpose='p', motivo='red', motivos=['red'], red=True, para_sesion=True,
            prefijo=['npm', 'install'], prefijo_alcance='red')))
        await asyncio.sleep(0.02)
        peticion = c.del_agente('approval_request')[-1]['payload']
        tarea.cancel()
        c.t._cerrar_sesion()
        return peticion
    peticion = asyncio.run(ejercicio())
    assert peticion['reason'] == 'network'
    assert peticion['alfalfa']['prefix'] == ['npm', 'install'] and peticion['alfalfa']['prefix_scope'] == 'network'
