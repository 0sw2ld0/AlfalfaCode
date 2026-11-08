"""Reglas de órdenes al estilo de execpolicy (arnes/reglas) en AlfalfaCode.

Las órdenes corren de verdad en el sandbox Seatbelt; sólo la aprobación es un
registrador que responde por motivo, como en test_alfalfa_bajo_demanda.py.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sys

import pytest

from alfalfa import reglas
from alfalfa.permisos.bajo_demanda import MOTIVOS, REGLA_USUARIO
from alfalfa.permisos.terminal import MOTIVO_TERMINAL, describe
from alfalfa.proveedores.nativo import herramientas
from alfalfa.reglas import PERMITIDA, PREGUNTAR, PROHIBIDA, ReglasInvalidas, leer
from alfalfa.seatbelt import SANDBOX_EXEC
from alfalfa.terminal import TerminalManager, main

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')

REGLAS = '''
[[regla]]
patron = ["touch", "prohibido"]
decision = "prohibida"
justificacion = "Usa otro nombre."
coincide = ["touch prohibido", ["touch", "prohibido", "otro"]]
no_coincide = ["touch permitido"]

[[regla]]
patron = ["touch", ["preguntado", "consultado"]]
decision = "preguntar"
justificacion = "Avísame antes."

[[regla]]
pattern = ["rm", "-rf", "build"]
decision = "allow"
match = ["rm -rf build"]
'''


def cargar(texto):
    import tomllib
    return leer(tomllib.loads(texto))


# ---------------------------------------------------------------- lectura

def test_nombres_propios_y_de_codex_y_ejemplos_que_pasan():
    r = cargar(REGLAS)
    assert [x.decision for x in r.reglas] == [PROHIBIDA, PREGUNTAR, PERMITIDA]
    assert r.reglas[1].patron == (('touch',), ('preguntado', 'consultado'))
    assert r.reglas[0].justificacion == 'Usa otro nombre.'


@pytest.mark.parametrize('texto,error', [
    ('[[regla]]\npatron = ["git"]\ncoincide = ["ls"]', 'debería coincidir'),
    ('[[regla]]\npatron = ["git"]\nno_coincide = ["git status"]', 'no debería coincidir'),
    ('[[regla]]\npatron = ["git"]\ndecision = "quizá"', 'decision'),
    ('[[regla]]\npatron = []', 'patron'),
    ('[[regla]]\npatron = ["git", 3]', 'patron'),
    ('[[regla]]\ndecision = "allow"', 'falta patron'),
    ('[[regla]]\npatron = ["git"]\nnota = "x"', 'campo desconocido'),
    ('[[regla]]\npatron = ["git"]\npattern = ["ls"]', 'repetido'),
    ('[[regla]]\npatron = ["git"]\ncoincide = ["git \'abierta"]', 'comillas'),
    ('[otra]\nx = 1', 'claves desconocidas'),
])
def test_reglas_invalidas_se_rechazan_al_cargar(texto, error):
    with pytest.raises(ReglasInvalidas, match=error):
        cargar(texto)


def test_archivo_del_hogar(tmp_path):
    assert not reglas.desde_hogar(tmp_path)
    (tmp_path / 'reglas.toml').write_text('[[regla\n')
    with pytest.raises(ReglasInvalidas, match='TOML'):
        reglas.desde_hogar(tmp_path)
    (tmp_path / 'reglas.toml').unlink()
    (tmp_path / 'otro.toml').write_text(REGLAS)
    (tmp_path / 'reglas.toml').symlink_to(tmp_path / 'otro.toml')
    with pytest.raises(ReglasInvalidas, match='sin enlaces'):
        reglas.desde_hogar(tmp_path)


# ---------------------------------------------------------------- evaluación

R = cargar('''
[[regla]]
patron = ["git", "push"]
decision = "prohibida"
[[regla]]
patron = [["git", "hg"], "commit"]
decision = "preguntar"
[[regla]]
patron = ["git"]
decision = "permitida"
''')


@pytest.mark.parametrize('argv,decision', [
    (['git', 'push', 'origin'], PROHIBIDA),
    (['/usr/bin/git', 'push'], PROHIBIDA),          # ruta absoluta: coincide con el nombre
    (['env', 'A=1', 'B=2', 'git', 'push'], PROHIBIDA),
    (['sudo', 'git', 'push'], PROHIBIDA),
    (['nohup', 'git', 'push'], PROHIBIDA),
    (['bash', '-lc', 'git status && git push'], PROHIBIDA),
    (['bash', '-lc', 'X=1 git push'], PROHIBIDA),
    (['bash', '-lc', 'x=1; git push'], PROHIBIDA),
    (['sh', '-c', "git log | head -n 1; git commit -m 'x y'"], PREGUNTAR),
    (['hg', 'commit'], PREGUNTAR),
    (['git', 'status'], PERMITIDA),                 # sólo la permitida
    (['git'], PERMITIDA),
    (['ls'], None),
    (['gitk'], None),
])
def test_gana_la_mas_estricta_de_todas_las_partes(argv, decision):
    assert R.evaluar(argv).decision == decision


@pytest.mark.parametrize('script', ['git push > log', 'echo $(git push)', 'git push &', 'git push "$REMOTO"',
                                    'ls *.py', '(git push)', 'echo `git push`'])
def test_shell_no_analizable_pregunta_si_hay_reglas_estrictas(script):
    evaluacion = R.evaluar(['bash', '-lc', script])
    assert evaluacion.no_analizable and evaluacion.decision in (PREGUNTAR, PROHIBIDA)
    solo_permitidas = cargar('[[regla]]\npatron = ["ls"]')
    assert solo_permitidas.evaluar(['bash', '-lc', script]).decision is None


def test_comprobar_orden_desde_la_linea_de_ordenes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path))
    (tmp_path / 'reglas.toml').write_text(REGLAS)
    assert main(['--comprobar-orden', 'touch', 'prohibido']) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida['tipo'] == 'reglas' and salida['decision'] == PROHIBIDA
    assert salida['coincidencias'] == [dict(prefijo=['touch', 'prohibido'], decision=PROHIBIDA,
                                            patron='touch prohibido', justificacion='Usa otro nombre.')]
    assert main(['--comprobar-orden', 'ls']) == 0
    assert 'decision' not in json.loads(capsys.readouterr().out)
    (tmp_path / 'reglas.toml').write_text('[[regla]]\npatron = 1')
    assert main(['--comprobar-orden', 'ls']) == 1


# ---------------------------------------------------------------- en la política on-request

class Recorder:
    def __init__(self, **answers):
        self.answers, self.calls, self.denied = answers, [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))
        if self.answers.get(args.get('motivo'), 'no') == 'yes':
            return
        self.denied += 1
        raise ValueError('Comando rechazado; no se ejecutó.')


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    home = tmp_path / 'alfalfa'
    home.mkdir()
    (home / 'reglas.toml').write_text(REGLAS)
    m = TerminalManager('Prueba de reglas.', home=home)
    spawned = []
    original = asyncio.create_subprocess_exec

    async def spy(*argv, **kwargs):
        spawned.append(list(argv))
        return await original(*argv, **kwargs)
    monkeypatch.setattr(herramientas.asyncio, 'create_subprocess_exec', spy)
    rt = m.runtime()

    def call(recorder, tool='run_command', **arguments):
        rt.approval_policy = recorder
        spawned.clear()
        return asyncio.run(rt.tools.call(tool, dict(purpose='Prueba', **arguments)))
    try:
        yield rt, root, call, spawned
    finally:
        m.close()


def lanzadas(spawned):
    return [a for a in spawned if a[-1] != '/usr/bin/true']


@real
@pytest.mark.parametrize('tool', ['run_command', 'run_checks'])
def test_prohibida_no_se_ejecuta_ni_pregunta(session, tool):
    rt, root, call, spawned = session
    recorder = Recorder(regla='yes', peligroso='yes')
    with pytest.raises(ValueError, match=re.escape('regla del usuario «touch prohibido»: Usa otro nombre. No se')):
        call(recorder, tool, argv=['touch', 'prohibido'])
    with pytest.raises(ValueError, match='prohibida'):
        call(recorder, tool, argv=['bash', '-lc', 'true && touch prohibido'])
    assert recorder.calls == [] and lanzadas(spawned) == [] and not (root / 'prohibido').exists()


@real
@pytest.mark.parametrize('respuesta', ['no', 'yes'])
def test_preguntar_pide_permiso_aunque_la_orden_sea_normal(session, respuesta):
    rt, root, call, spawned = session
    recorder = Recorder(regla=respuesta)
    if respuesta == 'no':
        with pytest.raises(ValueError, match='rechazado'):
            call(recorder, argv=['touch', 'consultado'])
        assert lanzadas(spawned) == []
    else:
        resultado = call(recorder, argv=['touch', 'consultado'])
        assert resultado['exit_code'] == 0 and resultado['sandbox'] == 'seatbelt'
        assert resultado['permisos'] == [REGLA_USUARIO]
    [peticion] = recorder.calls
    assert peticion['motivo'] == REGLA_USUARIO and peticion['motivos'] == [REGLA_USUARIO]
    assert peticion['regla'] == 'regla del usuario «touch [preguntado|consultado]»: Avísame antes'
    assert (root / 'consultado').exists() is (respuesta == 'yes')
    # Sin regla, la misma orden normal no pregunta.
    recorder = Recorder()
    assert call(recorder, argv=['touch', 'libre'])['exit_code'] == 0 and recorder.calls == []


@real
def test_permitida_quita_la_pregunta_de_peligrosa_pero_no_el_sandbox_ni_la_red(session, tmp_path):
    rt, root, call, spawned = session
    (root / 'build').mkdir()
    (root / 'build' / 'x.o').write_text('x')
    recorder = Recorder()
    resultado = call(recorder, argv=['rm', '-rf', 'build'])
    assert resultado['exit_code'] == 0 and not (root / 'build').exists() and recorder.calls == []
    [lanzada] = lanzadas(spawned)
    assert lanzada[0] == SANDBOX_EXEC and resultado['sandbox'] == 'seatbelt'
    # La red sigue preguntando aunque la orden esté permitida.
    recorder = Recorder()
    (root / 'build').mkdir()
    with pytest.raises(ValueError, match='rechazado'):
        call(recorder, argv=['rm', '-rf', 'build'], needs_network=True)
    assert [c['motivo'] for c in recorder.calls] == ['red'] and (root / 'build').exists()
    # Otra orden peligrosa sigue preguntando.
    recorder = Recorder()
    with pytest.raises(ValueError, match='rechazado'):
        call(recorder, argv=['rm', '-rf', 'otra'])
    assert [c['motivo'] for c in recorder.calls] == ['peligroso']


def test_un_archivo_de_reglas_invalido_no_arranca_la_sesion(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / 'alfalfa'
    home.mkdir()
    (home / 'reglas.toml').write_text('[[regla]]\npatron = ["git"]\ncoincide = ["ls"]')
    with pytest.raises(ReglasInvalidas, match='debería coincidir'):
        TerminalManager('x', home=home)
    monkeypatch.setenv('ALFALFA_HOME', str(home))
    assert main(['x']) == 1
    assert 'debería coincidir' in capsys.readouterr().err


def test_el_motivo_regla_esta_en_todas_partes():
    assert REGLA_USUARIO == 'regla' and REGLA_USUARIO in MOTIVOS and MOTIVO_TERMINAL['regla'] == 'regla del usuario'
    texto = describe(dict(argv=['touch', 'x'], purpose='P', motivo='regla', motivos=['regla'],
                          regla='regla del usuario «touch»'), 'touch x')
    assert texto.startswith('AlfalfaCode · Permiso: regla del usuario') and 'Regla: regla del usuario «touch»' in texto


def test_licencia_y_procedencia():
    carpeta = Path(reglas.__file__).resolve().parent
    assert re.search(r'Copyright\s2025\sOpenAI', (carpeta / 'NOTICE').read_text())
    assert 'execpolicy' in (carpeta / 'PROCEDENCIA.md').read_text()
