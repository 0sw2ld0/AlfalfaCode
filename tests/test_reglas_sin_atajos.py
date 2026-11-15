"""Las reglas de órdenes no se esquivan cambiando la forma de la orden (A9, A10, M5 y M6).

- Una regla `permitida` (la de «No volver a preguntar», que corre fuera del sandbox) casaba también
  `tools/git commit`, `env PATH=… git commit`, `env GIT_EDITOR=… git commit` y `sudo git commit`.
- El modelo podía proponer como prefijo `python3 -B`, `bash -x`, `env -i`, `find .` o `rm -r`, y
  una vez guardado corría cualquier cosa sin preguntar y sin sandbox.
- Una regla `permitida` que cubría sólo un trozo (`git status; rm -rf src`) quitaba la pregunta
  de peligrosa a toda la línea.
- Una regla `prohibida` se esquivaba con `bash -ec`, `dash -c`, `git -C .` o `--har`.
"""
import asyncio
from types import SimpleNamespace

import pytest

from alfalfa import reglas
from alfalfa.permisos.bajo_demanda import OnRequestPolicy
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, WorkspaceTools
from alfalfa.reglas import PERMITIDA, PROHIBIDA

GIT_COMMIT = reglas.leer({'regla': [{'patron': ['git', 'commit'], 'decision': 'permitida',
                                    'fuera_del_sandbox': True}]})


# ---------------------------------------------------------------- A9: la orden tal cual
@pytest.mark.parametrize('argv', [
    ['git', 'commit', '-m', 'x'], ['/usr/bin/git', 'commit', '-m', 'x'],
    ['/opt/homebrew/bin/git', 'commit'], ['bash', '-lc', 'git commit -m x'],
])
def test_la_regla_de_no_volver_a_preguntar_vale_para_la_orden_tal_cual(argv):
    assert GIT_COMMIT.fuera_del_sandbox(argv)


@pytest.mark.parametrize('argv', [
    ['/private/tmp/z/git', 'commit', '-m', 'x'], ['tools/git', 'commit'], ['./git', 'commit'],
    ['env', 'PATH=/private/tmp/z', 'git', 'commit', '-m', 'x'],
    ['env', 'GIT_EDITOR=/private/tmp/z/editor', 'git', 'commit'],
    ['env', 'GIT_DIR=/private/tmp/z/.git', 'git', 'commit', '-m', 'x'],
    ['sudo', 'git', 'commit', '-m', 'x'], ['nice', 'git', 'commit'],
    ['bash', '-lc', 'GIT_EDITOR=/tmp/e git commit'], ['bash', '-lc', 'env PATH=/tmp/z git commit'],
    ['bash', '-ec', 'git commit'], ['bash', '-c', 'git commit', '_'],
])
def test_otra_forma_de_la_orden_ya_no_es_la_que_se_permitio(argv):
    assert not GIT_COMMIT.fuera_del_sandbox(argv)
    assert GIT_COMMIT.evaluar(argv).decision is None


# ---------------------------------------------------------------- A10: prefijos del modelo
@pytest.mark.parametrize('propuesto,argv', [
    (['python3', '-B'], ['python3', '-B', 'tools/x.py']), (['bash', '-x'], ['bash', '-x', 'build.sh']),
    (['env', '-i'], ['env', '-i', 'make']), (['find', '.'], ['find', '.', '-name', 'x']),
    (['nice'], ['nice', 'make']), (['rm', '-r'], ['rm', '-r', 'build']),
    (['python3.12', 'tools/x.py'], ['python3.12', 'tools/x.py']), (['/usr/bin/env', 'make'], ['/usr/bin/env', 'make']),
    (['node', 'scripts/build.js'], ['node', 'scripts/build.js']), (['xargs', 'ls'], ['xargs', 'ls']),
    (['make', '-f'], ['make', '-f', 'otro.mk']), (['git', '-c'], ['git', '-c', 'core.pager=x', 'log']),
])
def test_prefijos_que_permitirian_cualquier_cosa_no_se_ofrecen(propuesto, argv):
    assert reglas.prefijo_para_siempre(propuesto, argv) is None


@pytest.mark.parametrize('propuesto,argv', [
    (['git', 'commit'], ['git', 'commit', '-m', 'x']), (['make', 'test'], ['make', 'test']),
    (['swiftlint'], ['swiftlint', 'lint']), (['git', 'config', 'user.name'], ['git', 'config', 'user.name', 'Yo']),
])
def test_los_prefijos_normales_se_siguen_ofreciendo(propuesto, argv):
    assert reglas.prefijo_para_siempre(propuesto, argv) == propuesto


def test_git_por_defecto_ofrece_la_suborden_y_no_con_una_ruta_cualquiera():
    assert reglas.prefijo_para_siempre(None, ['git', 'commit', '-m', 'x']) == ['git', 'commit']
    assert reglas.prefijo_para_siempre(None, ['tools/git', 'commit']) is None


# ---------------------------------------------------------------- M6: prohibida en todas sus formas
RESET = reglas.leer({'regla': [{'patron': ['git', 'reset', '--hard'], 'decision': 'prohibida'}]})


@pytest.mark.parametrize('argv', [
    ['git', 'reset', '--hard'], ['bash', '-lc', 'git reset --hard'], ['bash', '-ec', 'git reset --hard'],
    ['bash', '-c', 'git reset --hard', '_'], ['env', 'bash', '-lc', 'git reset --hard'],
    ['dash', '-c', 'git reset --hard'], ['git', '-C', '.', 'reset', '--hard'],
    ['git', '-c', 'x=y', '--no-pager', 'reset', '--hard'], ['git', 'reset', '--har'],
    ['/usr/local/bin/git', 'reset', '--hard'], ['sudo', 'git', 'reset', '--hard'],
    ['env', 'A=1', 'sh', '-c', 'cd x && git -C . reset --ha'],
])
def test_una_regla_prohibida_casa_todas_las_formas(argv):
    assert RESET.evaluar(argv).decision == PROHIBIDA


@pytest.mark.parametrize('argv', [['git', 'reset', '--keep'], ['git', 'reset', '--'], ['git', 'reset', '--soft'],
                                  ['bash', 'reset.sh']])
def test_y_no_casa_lo_que_no_es(argv):
    assert RESET.evaluar(argv).decision is None


def test_la_permitida_no_gana_por_otra_forma_pero_la_prohibida_si():
    r = reglas.leer({'regla': [{'patron': ['git'], 'decision': 'permitida'},
                               {'patron': ['git', 'push'], 'decision': 'prohibida'}]})
    assert r.evaluar(['git', 'status']).decision == PERMITIDA
    assert r.evaluar(['env', 'X=1', 'git', 'status']).decision is None
    assert r.evaluar(['env', 'X=1', 'git', 'push']).decision == PROHIBIDA


# ---------------------------------------------------------------- M5 y A9 en la política de verdad
def politica(tmp_path, monkeypatch, texto):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'hogar'))
    pedidos = []

    async def approve(args):
        pedidos.append(args)

    class Sandbox:
        async def prepare(self, argv):
            return ['sandbox'] + list(argv)

    runtime = SimpleNamespace(task={'cwd': str(raiz), 'id': 't'}, excluded_tools=(), tool_specs=ON_REQUEST_TOOLS,
                              state={}, approve=approve, approval_policy=None, process_sandbox=Sandbox(),
                              manager=SimpleNamespace(event=lambda *a, **k: None, save=lambda: None))
    tools = WorkspaceTools(runtime)
    politica = OnRequestPolicy(reglas.leer(texto))

    async def launch(command, label, extra):
        return {"exit_code": 0, "sandbox": label, "permisos": extra.get("permisos")}

    def ejecutar(argv):
        pedidos.clear()
        resultado = asyncio.run(politica.execute(tools, {'argv': argv, 'purpose': 'prueba'}, argv, launch,
                                                 unsandboxed_timeout=5))
        return resultado['sandbox'], [p['motivo'] for p in pedidos]
    return ejecutar


def test_una_permitida_que_cubre_un_trozo_no_quita_la_pregunta_de_peligrosa(tmp_path, monkeypatch):
    ejecutar = politica(tmp_path, monkeypatch, {'regla': [{'patron': ['git', 'status'], 'decision': 'permitida'}]})
    assert ejecutar(['bash', '-lc', 'git status; rm -rf ../src'])[1] == ['peligroso']
    assert ejecutar(['git', 'status']) == ('seatbelt', [])


def test_no_volver_a_preguntar_no_saca_del_sandbox_otra_forma_ni_lo_peligroso(tmp_path, monkeypatch):
    ejecutar = politica(tmp_path, monkeypatch, {'regla': [
        {'patron': ['git', 'commit'], 'decision': 'permitida', 'fuera_del_sandbox': True},
        {'patron': ['rm', '-rf', '../build'], 'decision': 'permitida', 'fuera_del_sandbox': True}]})
    assert ejecutar(['git', 'commit', '-m', 'x']) == ('none', [])
    assert ejecutar(['env', 'GIT_EDITOR=/tmp/e', 'git', 'commit'])[0] == 'seatbelt'
    assert ejecutar(['/private/tmp/z/git', 'commit'])[0] == 'seatbelt'
    # Una regla puesta a mano para algo peligroso no lo saca del sandbox: el patrón es un prefijo y
    # también casaría `rm -rf ../build /`. Dentro, como la permitiste entera, no pregunta.
    assert ejecutar(['rm', '-rf', '../build']) == ('seatbelt', [])
    assert ejecutar(['rm', '-rf', '../build', '/']) == ('seatbelt', [])
