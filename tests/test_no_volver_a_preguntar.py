"""«No volver a preguntar por órdenes que empiecen por …», como el prefix_rule de Codex.

Para una orden que necesita salir del sandbox (p. ej. `git config`, que escribe
.git/config), la tarjeta ofrece guardar un prefijo (`ApprovedExecpolicyAmendment`) como regla `allow`, que
desde entonces ejecuta esas órdenes fuera del sandbox (core/src/exec_policy.rs).
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
from alfalfa.permisos import sesion
from alfalfa.permisos.bajo_demanda import SIEMPRE
from alfalfa.proveedores.nativo import herramientas
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager

REAL = sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)
real = pytest.mark.skipif(not REAL, reason='requiere /usr/bin/sandbox-exec (macOS)')


# --- el prefijo que se ofrece ---------------------------------------------------

def test_git_ofrece_la_suborden_nunca_git_a_secas():
    assert reglas.prefijo_para_siempre(None, ['git', 'switch', '-c', 'x', 'main']) == ['git', 'switch']
    assert reglas.prefijo_para_siempre(None, ['/usr/bin/git', 'commit', '-m', 'x']) == ['git', 'commit']
    assert reglas.prefijo_para_siempre(['git'], ['git', 'commit', '-m', 'x']) is None
    assert reglas.prefijo_para_siempre(None, ['git', '-C', 'otra', 'commit']) is None


def test_el_prefijo_del_modelo_debe_cubrir_cada_parte_y_no_estar_prohibido():
    assert reglas.prefijo_para_siempre(['git', 'commit'], ['git', 'commit', '-m', 'x']) == ['git', 'commit']
    assert reglas.prefijo_para_siempre(['git', 'push'], ['git', 'commit', '-m', 'x']) is None
    for prohibido in (['bash', '-lc'], ['python3'], ['sh', '-c'], ['sudo'], ['rm'], ['env']):
        assert reglas.prefijo_para_siempre(prohibido, prohibido + ['algo']) is None
    # Un script con dos órdenes distintas: un solo prefijo no cubre las dos.
    assert reglas.prefijo_para_siempre(['git', 'add'], ['bash', '-lc', 'git add -A && git commit -m x']) is None
    assert reglas.prefijo_para_siempre(['git', 'add'], ['bash', '-lc', 'git add a && git add b']) == ['git', 'add']
    # Scripts no analizables, tokens vacíos o con caracteres de shell: nada.
    assert reglas.prefijo_para_siempre(['git', 'add'], ['bash', '-lc', 'git add $(cat x)']) is None
    assert reglas.prefijo_para_siempre(['git', 'a;b'], ['git', 'a;b']) is None
    assert reglas.prefijo_para_siempre('git commit', ['git', 'commit']) is None


# --- la regla guardada ----------------------------------------------------------

def test_guardar_escribe_una_regla_valida_y_conserva_las_anteriores(tmp_path):
    archivo = tmp_path / reglas.ARCHIVO
    archivo.write_text('[[regla]]\npatron = ["rm"]\ndecision = "prohibida"\n')
    nuevas = reglas.guardar_para_siempre(tmp_path, ['git', 'commit'], 'Aprobada "hoy".')
    assert [r.decision for r in nuevas.reglas] == ['prohibida', 'permitida']
    assert nuevas.reglas[-1].sin_sandbox is True
    assert reglas.desde_hogar(tmp_path).reglas[-1].texto() == 'git commit'
    assert oct(archivo.stat().st_mode & 0o777) == '0o600'


def test_no_escribe_si_el_archivo_actual_es_invalido(tmp_path):
    archivo = tmp_path / reglas.ARCHIVO
    archivo.write_text('esto no es toml [')
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.guardar_para_siempre(tmp_path, ['git', 'commit'], 'x')
    assert archivo.read_text() == 'esto no es toml ['


def test_fuera_del_sandbox_solo_con_permitida():
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.leer({'regla': [{'patron': ['git', 'commit'], 'decision': 'preguntar', 'fuera_del_sandbox': True}]})
    with pytest.raises(reglas.ReglasInvalidas):
        reglas.leer({'regla': [{'patron': ['git', 'commit'], 'fuera_del_sandbox': 'sí'}]})


def test_sale_del_sandbox_solo_si_cada_parte_esta_permitida_y_nada_es_mas_estricto():
    r = reglas.leer({'regla': [
        {'patron': ['git', 'commit'], 'decision': 'permitida', 'fuera_del_sandbox': True},
        {'patron': ['git', 'add'], 'decision': 'permitida'},
        {'patron': ['git', 'commit', '--amend'], 'decision': 'preguntar'}]})
    assert r.fuera_del_sandbox(['git', 'commit', '-m', 'x'])
    assert r.fuera_del_sandbox(['bash', '-lc', 'git commit -m x'])
    # `git add` está permitida pero sin `fuera_del_sandbox`: sigue en el sandbox.
    assert not r.fuera_del_sandbox(['bash', '-lc', 'git add -A && git commit -m x'])
    assert not r.fuera_del_sandbox(['git', 'add', '-A'])
    # Una regla más estricta manda.
    assert not r.fuera_del_sandbox(['git', 'commit', '--amend'])
    assert not r.fuera_del_sandbox(['bash', '-lc', 'git commit -m "$(date)"'])


def test_las_reglas_del_equipo_no_pueden_sacar_nada_del_sandbox(tmp_path):
    from alfalfa import equipo
    (tmp_path / '.alfalfa').mkdir()
    (tmp_path / '.alfalfa' / 'equipo.toml').write_text(
        '[[regla]]\npatron = ["git", "commit"]\ndecision = "permitida"\nfuera_del_sandbox = true\n')
    with pytest.raises(equipo.ErrorDeEquipo):
        equipo.cargar(tmp_path)


# --- la tarjeta, el transporte y el motor -------------------------------------------

def test_el_transporte_solo_concede_siempre_si_se_ofrecio():
    async def ejercicio():
        salida = io.StringIO()
        t = Transport(salida)
        aprobacion = t.approval('/tmp', False)

        async def pedir(respuesta, **extra):
            tarea = asyncio.create_task(aprobacion(dict(argv=['git', 'commit'], purpose='Commit',
                                                        motivo='salir_sandbox', **extra)))
            await asyncio.sleep(0)
            fila = [json.loads(linea) for linea in salida.getvalue().splitlines()
                    if json.loads(linea)['tipo'] == 'permiso'][-1]
            t.receive(json.dumps(dict(tipo='permiso', id=fila['id'], aprobado=True, **respuesta)))
            await asyncio.sleep(0)
            return fila, tarea

        fila, tarea = await pedir({'siempre': True}, prefijo=['git', 'commit'])
        assert fila['prefijo'] == ['git', 'commit'] and await tarea == SIEMPRE
        resuelto = [json.loads(linea) for linea in salida.getvalue().splitlines()][-1]
        assert resuelto['tipo'] == 'permiso_resuelto' and resuelto['siempre'] is True
        # Sin prefijo ofrecido, «siempre» es un mensaje inválido y el permiso sigue esperando.
        fila, tarea = await pedir({'siempre': True})
        assert 'prefijo' not in fila and not tarea.done()
        assert 'inválido' in [json.loads(linea) for linea in salida.getvalue().splitlines()][-1]['texto']
        t.receive(json.dumps({'tipo': 'permiso', 'id': fila['id'], 'aprobado': True}))
        assert await tarea is None
    asyncio.run(ejercicio())


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
    for orden in (['git', 'init', '-q', '-b', 'main'], ['git', 'config', 'user.email', 'p@example.com'],
                  ['git', 'config', 'user.name', 'Prueba'], ['git', 'commit', '-q', '--allow-empty', '-m', 'x']):
        subprocess.run(orden, cwd=raiz, check=True, capture_output=True)
    monkeypatch.chdir(raiz)
    hogar = tmp_path / 'alfalfa'
    m = TerminalManager('Prueba.', home=hogar)
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
        yield rt, llamar, lanzadas, eventos, raiz, hogar
    finally:
        m.close()


def ramas(raiz):
    return subprocess.run(['git', 'branch', '--format=%(refname:short)'], cwd=raiz, capture_output=True,
                          text=True).stdout.split()


def config(raiz, clave):
    return subprocess.run(['git', 'config', '--get', clave], cwd=raiz, capture_output=True, text=True).stdout.strip()


@real
def test_crear_rama_no_pide_permiso(motor):
    """Desde que el sandbox sólo protege hooks y config de .git, como Claude Code."""
    rt, llamar, lanzadas, eventos, raiz, hogar = motor
    aprobacion = Respuesta(None)
    resultado = llamar(aprobacion, argv=['git', 'switch', '-c', 'uno'])
    assert aprobacion.peticiones == [] and resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
    assert 'uno' in ramas(raiz)


@real
def test_no_volver_a_preguntar_y_la_siguiente_no_pregunta(motor):
    rt, llamar, lanzadas, eventos, raiz, hogar = motor
    # git config escribe .git/config, que sigue protegido: pide salir del sandbox.
    primera = Respuesta(SIEMPRE)
    resultado = llamar(primera, argv=['git', 'config', 'alfalfa.uno', 'x'])
    peticion, = primera.peticiones
    assert peticion['prefijo'] == ['git', 'config'] and resultado['exit_code'] == 0
    assert config(raiz, 'alfalfa.uno') == 'x'
    guardada = reglas.desde_hogar(hogar).reglas[-1]
    assert guardada.texto() == 'git config' and guardada.sin_sandbox
    # La siguiente: sin tarjeta, directamente fuera del sandbox.
    otra = Respuesta(None)
    resultado = llamar(otra, argv=['git', 'config', 'alfalfa.dos', 'y'])
    assert otra.peticiones == [] and resultado['sandbox'] == 'none' and resultado['exit_code'] == 0
    assert config(raiz, 'alfalfa.dos') == 'y'
    assert any(t.startswith('Permitida siempre por') for t in eventos)
    # Otra orden que también necesita salir sigue preguntando, y ofrece la suya.
    tercera = Respuesta(None)
    llamar(tercera, argv=['git', 'init', '-q', 'sub'])
    assert [p.get('prefijo') for p in tercera.peticiones] == [['git', 'init']]


@real
def test_tras_leer_contenido_externo_vuelve_a_preguntar_y_no_ofrece_siempre(motor):
    rt, llamar, lanzadas, eventos, raiz, hogar = motor
    reglas.guardar_para_siempre(hogar, ['git', 'config'], 'prueba')
    rt.command_policy.reglas.reglas.append(reglas.desde_hogar(hogar).reglas[-1])
    rt.state[EXTERNO] = ['read_web']
    aprobacion = Respuesta(None)
    resultado = llamar(aprobacion, argv=['git', 'config', 'alfalfa.tres', 'z'])
    peticion, = aprobacion.peticiones
    assert 'contenido_externo' in peticion['motivos'] and 'prefijo' not in peticion
    assert resultado['exit_code'] == 0


@real
def test_permitir_en_esta_sesion_no_guarda_regla(motor):
    rt, llamar, lanzadas, eventos, raiz, hogar = motor
    llamar(Respuesta(sesion.SESION), argv=['git', 'config', 'alfalfa.cuatro', 'w'])
    assert not (hogar / reglas.ARCHIVO).exists()
