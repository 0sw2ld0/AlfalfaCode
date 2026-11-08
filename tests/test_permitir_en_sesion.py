"""SwiftPM dentro de scripts en el sandbox y aprobaciones «para esta sesión», como Codex."""
import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from alfalfa.permisos import sesion
from alfalfa.permisos.denegacion import is_likely_sandbox_denied
from alfalfa.proveedores.nativo import herramientas
from alfalfa.seatbelt import SANDBOX_EXEC, SeatbeltSandbox, compiladores
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

REAL = sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)
real = pytest.mark.skipif(not REAL, reason='requiere /usr/bin/sandbox-exec (macOS)')
PY = [sys.executable, '-I', '-B', '-c']


@pytest.fixture
def falsos(tmp_path, monkeypatch):
    """Un `swift` y un `xcrun` que sólo dicen con qué argumentos los llamaron."""
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    for nombre in ('swift', 'swift-build', 'xcrun'):
        (bin_ / nombre).write_text('#!/bin/sh\necho "' + nombre + '" "$@"\n')
        (bin_ / nombre).chmod(0o755)
    monkeypatch.setattr(compiladores, 'carpeta_de_envoltorios', lambda: tmp_path / 'envoltorios')
    return bin_


def correr(env, *argv):
    return subprocess.run(list(argv), env=env, capture_output=True, text=True, timeout=30).stdout.strip()


def test_los_envoltorios_quitan_el_sandbox_anidado_de_swiftpm(falsos):
    env = compiladores.entorno_del_sandbox({'PATH': str(falsos) + os.pathsep + '/usr/bin:/bin'})
    carpeta = Path(env['PATH'].split(os.pathsep)[0])
    assert carpeta.parent.name == 'envoltorios' and (carpeta / 'swift').is_file()
    assert correr(env, '/bin/sh', '-c', 'swift build -c release') == 'swift build --disable-sandbox -c release'
    assert correr(env, '/bin/sh', '-c', 'swift package resolve') == 'swift package --disable-sandbox resolve'
    assert correr(env, '/bin/sh', '-c', 'swift test --disable-sandbox') == 'swift test --disable-sandbox'
    assert correr(env, '/bin/sh', '-c', 'swift --version') == 'swift --version'
    assert correr(env, '/bin/sh', '-c', 'swift-build -c debug') == 'swift-build --disable-sandbox -c debug'
    assert correr(env, '/bin/sh', '-c', 'xcrun swift build') == 'swift build --disable-sandbox'
    assert correr(env, '/bin/sh', '-c', 'xcrun --show-sdk-path') == 'xcrun --show-sdk-path'
    # Sin sandbox no hay envoltorios: el PATH de siempre.
    assert correr({'PATH': str(falsos)}, '/bin/sh', '-c', 'swift build') == 'swift build'
    # Otra vez con el mismo PATH: misma carpeta, nada duplicado en el PATH.
    otra = compiladores.entorno_del_sandbox({'PATH': env['PATH']})
    assert otra['PATH'].split(os.pathsep).count(str(carpeta)) == 1


def test_un_envoltorio_cambiado_se_reescribe(falsos):
    env = compiladores.entorno_del_sandbox({'PATH': str(falsos)})
    swift = Path(env['PATH'].split(os.pathsep)[0]) / 'swift'
    swift.write_text('#!/bin/sh\necho cambiado\n')
    env = compiladores.entorno_del_sandbox({'PATH': str(falsos)})
    assert correr(env, '/bin/sh', '-c', 'swift build') == 'swift build --disable-sandbox'


@real
def test_un_script_que_compila_corre_en_el_sandbox_real_con_los_envoltorios(falsos, tmp_path):
    proyecto = tmp_path / 'proyecto'
    proyecto.mkdir()
    (proyecto / 'Abrir app.command').write_text('#!/bin/sh\nswift build -c release\n')
    env = compiladores.entorno_del_sandbox({'PATH': str(falsos) + ':/usr/bin:/bin', 'HOME': os.environ['HOME'],
                                            'TMPDIR': os.environ.get('TMPDIR', '/tmp')})
    orden = asyncio.run(SeatbeltSandbox(proyecto).prepare(['sh', 'Abrir app.command']))
    salida = subprocess.run(orden, cwd=proyecto, env=env, capture_output=True, text=True, timeout=60)
    assert salida.returncode == 0, salida.stderr
    assert salida.stdout.strip() == 'swift build --disable-sandbox -c release'


def test_open_de_una_app_recien_compilada_cuenta_como_bloqueo():
    texto = ('The application cannot be opened for an unexpected reason, error=Error Domain=NSOSStatusErrorDomain '
             'Code=-10827 "kLSNoExecutableErr: The executable is missing"')
    assert is_likely_sandbox_denied('seatbelt', 1, aggregated=texto)
    assert not is_likely_sandbox_denied(None, 1, aggregated=texto)


def test_claves_de_sesion():
    assert sesion.clave_de_orden(['sh', 'a b'], None) == sesion.clave_de_orden(['sh', 'a b'], '.')
    assert sesion.clave_de_orden(['sh'], None, sin_sandbox=True, red=True) == \
        sesion.clave_de_orden(['sh'], None, sin_sandbox=True)
    assert sesion.clave_de_orden(['sh'], 'sub') != sesion.clave_de_orden(['sh'], None)
    task = {}
    assert not sesion.aprobadas(task, []) and not sesion.aprobadas(task, ['x'])
    sesion.recordar(task, ['x', 'y', 'x'])
    assert task[sesion.CAMPO] == ['x', 'y'] and sesion.aprobadas(task, ['y', 'x'])


class Respuesta:
    """Aprobación que contesta por motivo: 'sesion', 'si' o niega."""

    def __init__(self, **respuestas):
        self.respuestas, self.peticiones, self.denied = respuestas, [], 0

    async def __call__(self, args):
        self.peticiones.append(dict(args))
        respuesta = self.respuestas.get(args.get('motivo'), 'no')
        if respuesta == 'sesion':
            return sesion.SESION
        if respuesta == 'si':
            return None
        self.denied += 1
        raise ValueError('Comando rechazado; no se ejecutó.')


@pytest.fixture
def motor(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Prueba de sesión.', home=tmp_path / 'alfalfa')
    lanzadas = []
    original = asyncio.create_subprocess_exec

    async def espia(*argv, **kwargs):
        lanzadas.append(list(argv))
        return await original(*argv, **kwargs)

    monkeypatch.setattr(herramientas.asyncio, 'create_subprocess_exec', espia)
    rt = m.runtime()
    eventos = []
    evento = m.event
    m.event = lambda task, rol, texto: (eventos.append(texto), evento(task, rol, texto))

    def llamar(aprobacion, **argumentos):
        rt.approval_policy = aprobacion
        lanzadas.clear()
        return asyncio.run(rt.tools.call('run_command', dict(purpose='Prueba', **argumentos)))

    try:
        yield rt, llamar, lanzadas, eventos
    finally:
        m.close()


def ordenes(lanzadas):
    return [a for a in lanzadas if a[-1] != '/usr/bin/true']


@real
def test_reintento_aprobado_para_la_sesion_sale_del_sandbox_sin_preguntar(motor):
    rt, llamar, lanzadas, eventos = motor
    # Dice «Operation not permitted» y falla: dentro del sandbox parece un bloqueo.
    argv = PY + ['import sys; print("Operation not permitted"); sys.exit(3)']
    aprobacion = Respuesta(reintento='sesion')
    llamar(aprobacion, argv=argv)
    peticion, = aprobacion.peticiones
    assert peticion['motivo'] == 'reintento' and peticion['para_sesion'] is True
    assert len(ordenes(lanzadas)) == 2  # en el sandbox y el reintento
    assert sesion.clave_de_orden(argv, None, sin_sandbox=True) in rt.task[sesion.CAMPO]
    # La segunda vez: directamente sin sandbox, sin preguntar ni intentar dentro.
    otra = Respuesta()
    resultado = llamar(otra, argv=argv)
    assert otra.peticiones == []
    assert ordenes(lanzadas) == [argv] and resultado['sandbox'] == 'none'
    assert any(t.startswith('Aprobado en esta sesión') and '(sin sandbox)' in t for t in eventos)
    # Otra orden distinta sigue preguntando.
    llamar(otra, argv=argv + ['x'])
    assert [p['motivo'] for p in otra.peticiones] == ['reintento']


def test_lo_peligroso_nunca_se_aprueba_para_la_sesion(motor):
    rt, llamar, lanzadas, eventos = motor
    aprobacion = Respuesta(peligroso='sesion')
    llamar(aprobacion, argv=['rm', '-rf', 'no-existe'])
    peticion, = aprobacion.peticiones
    assert 'para_sesion' not in peticion and not rt.task.get(sesion.CAMPO)


def test_una_bifurcacion_no_hereda_las_aprobaciones(tmp_path, monkeypatch):
    from alfalfa.sesiones import bifurcar
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Hola', home=tmp_path / 'alfalfa')
    try:
        m.task['turnos'] = [dict(id='t1', inicio=0, fin=1, desde=1, hasta=1, mensaje='Hola', estado='completed')]
        m.task[sesion.CAMPO] = [sesion.clave_de_orden(['sh', 'x'])]
        m.save()
    finally:
        m.close()
    nueva = bifurcar(tmp_path / 'alfalfa' / 'sesiones', m.task['id'], 't1')
    copia = json.loads((tmp_path / 'alfalfa' / 'sesiones' / nueva / 'task.json').read_text())
    assert sesion.CAMPO not in copia


def test_el_transporte_solo_concede_la_sesion_si_se_ofrecio():
    async def ejercicio():
        salida = io.StringIO()
        t = Transport(salida)
        aprobacion = t.approval('/tmp', False)

        async def pedir(**extra):
            tarea = asyncio.create_task(aprobacion(dict(argv=['make'], purpose='Compilar', motivo='red', **extra)))
            await asyncio.sleep(0)
            fila = [json.loads(linea) for linea in salida.getvalue().splitlines() if json.loads(linea)['tipo'] == 'permiso'][-1]
            t.receive(json.dumps({'tipo': 'permiso', 'id': fila['id'], 'aprobado': True, 'sesion': True}))
            return fila, await tarea

        fila, respuesta = await pedir(para_sesion=True)
        assert fila['para_sesion'] is True and respuesta == sesion.SESION
        resuelto = [json.loads(linea) for linea in salida.getvalue().splitlines()][-1]
        assert resuelto['tipo'] == 'permiso_resuelto' and resuelto['sesion'] is True
        # Sin para_sesion, «sesion» del cliente no amplía nada: es un sí normal.
        fila, respuesta = await pedir()
        assert 'para_sesion' not in fila and respuesta is None
        assert 'sesion' not in [json.loads(linea) for linea in salida.getvalue().splitlines()][-1]
    asyncio.run(ejercicio())


@real
def test_un_reintento_sin_sandbox_que_funciona_no_deja_el_turno_bloqueado(motor):
    import shutil
    import tempfile
    rt, llamar, lanzadas, eventos = motor
    fuera = Path(tempfile.mkdtemp(prefix='alfalfa-sesion-prueba ', dir=Path.home() / 'Library' / 'Caches'))
    try:
        destino = fuera / 'abierto.txt'
        # Dentro del sandbox no puede escribir fuera de la carpeta; fuera, sí.
        argv = PY + ['open(__import__("sys").argv[1], "w").write("x")', str(destino)]
        resultado = llamar(Respuesta(reintento='si'), argv=argv)
        assert resultado['sandbox'] == 'none' and resultado['exit_code'] == 0 and destino.read_text() == 'x'
        assert rt.state['unresolved'] == {}
        dentro, = [f for f in rt.state['unresolved_records'].values() if f.get('repetida_sin_sandbox')]
        assert dentro['repetida_sin_sandbox'] == resultado['operation_id']
        # Si el reintento también falla, su propia incidencia sí bloquea.
        malo = PY + ['import sys; print("Permission denied"); sys.exit(1)']
        llamar(Respuesta(reintento='si'), argv=malo)
        assert len(rt.state['unresolved']) == 1
    finally:
        shutil.rmtree(fuera, ignore_errors=True)


def test_con_la_app_el_permiso_sin_sandbox_no_caduca_y_una_espera_cancelada_lo_dice():
    import tempfile

    from alfalfa.proveedores.nativo.herramientas import UNSANDBOXED_APPROVAL_SECONDS
    salida = io.StringIO()
    m = TerminalManager('x', home=tempfile.mkdtemp(), transport=Transport(salida))
    try:
        assert m.unsandboxed_approval_seconds is None
    finally:
        m.close()
    assert UNSANDBOXED_APPROVAL_SECONDS == 600.0  # sin cliente que conteste, sigue el plazo

    async def ejercicio():
        t = Transport(salida)
        tarea = asyncio.create_task(t.approval('/tmp', False)(dict(argv=['x'], purpose='p', motivo='salir_sandbox')))
        await asyncio.sleep(0)
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)
        resuelto = [json.loads(linea) for linea in salida.getvalue().splitlines()][-1]
        assert resuelto['tipo'] == 'permiso_resuelto' and resuelto['motivo'] == 'Sin respuesta a tiempo'
    asyncio.run(ejercicio())
