"""Órdenes corrientes que se tomaban por peligrosas (hallazgo A8 de la auditoría).

Antes, todo lo que peligrosos.py no sabía leer salía como «peligroso»: cada vez una tarjeta, y nunca
«permitir en esta sesión». Un heredoc para pasarle un guion a python3 o un `bash scripts/test.sh
tests/*.py` preguntaban siempre. `rm -f` sigue preguntando: puede borrar trabajo sin guardar.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from alfalfa.permisos.bajo_demanda import OnRequestPolicy
from alfalfa.permisos.peligrosos import FORCED_RM, OTHER, SUDO, dangerous_command_match
from alfalfa.proveedores.nativo.herramientas import ON_REQUEST_TOOLS, WorkspaceTools
from alfalfa.seatbelt import SANDBOX_EXEC, SeatbeltSandbox

HEREDOC_PY = ['bash', '-lc', "python3 - <<'PY'\nprint(1)\nPY"]
HEREDOC_CAT = ['bash', '-lc', "cat > notas.txt <<'EOF'\nhola\nEOF"]


# ---------------------------------------------------------------- lo que ahora se sabe leer
@pytest.mark.parametrize('argv', [
    HEREDOC_PY, HEREDOC_CAT,
    ['bash', '-lc', 'cat <<EOF\nhola $USER\nEOF'],
    ['bash', '-lc', "cat <<-'FIN'\n\trm -rf /\n\tFIN\nls"],
    ['bash', '-lc', 'bash scripts/test.sh tests/*.py'],
    ['bash', '-lc', 'sh scripts/build.sh --out=$TMPDIR/x'],
])
def test_ordenes_corrientes_ya_no_son_peligrosas(argv):
    assert dangerous_command_match(argv) is None


@pytest.mark.parametrize('argv,esperado', [
    # El cuerpo de un heredoc es el guion cuando lo recibe una shell.
    (['bash', '-lc', "bash <<'EOF'\nrm -rf ~/proyecto\nEOF"], FORCED_RM),
    (['bash', '-lc', 'sh <<EOF\nsudo ls\nEOF'], SUDO),
    (['bash', '-lc', "cat <<'A' && rm -rf x\nhola\nA"], FORCED_RM),
    # Sin comillas, el cuerpo se expande: con $(…) dentro no se sabe leer.
    (['bash', '-lc', 'cat <<EOF\n$(rm -rf x)\nEOF'], OTHER),
    (['bash', '-lc', 'cat <<EOF\nhola'], OTHER),
    (['bash', '-lc', 'bash -c "$X" scripts/a.sh'], OTHER),
    (['bash', '-lc', 'bash -s "$X"'], OTHER),
    (['bash', '-lc', 'echo ${nombres[$i]}'], OTHER),
])
def test_lo_peligroso_o_ilegible_sigue_preguntando_fuera_del_sandbox(argv, esperado):
    assert dangerous_command_match(argv) == esperado


# ---------------------------------------------------------------- dentro del sandbox sin red
@pytest.mark.parametrize('argv', [
    ['bash', '-lc', 'echo ${nombres[$i]}'],
    ['bash', '-lc', 'cat <<EOF\n$(date)\nEOF'],
    ['bash', '-lc', 'echo ${firm[$i]} && ./scripts/rmdir_vacias.sh'],
    ['bash', '-lc', 'bash -c "$X"'],
    ['bash', '-lc', 'eval "$CMD"'],
])
def test_dentro_del_sandbox_lo_ilegible_no_pregunta(argv):
    assert dangerous_command_match(argv) == OTHER
    assert dangerous_command_match(argv, dentro_del_sandbox=True) is None


@pytest.mark.parametrize('argv,esperado', [
    (['rm', '-rf', 'src'], FORCED_RM), (['rm', '-f', '.tmp/salida.log'], FORCED_RM),
    (['bash', '-lc', 'echo ${a[$i]}; rm -rf src'], OTHER), (['bash', '-lc', 'echo ${a[$i]} && sudo ls'], OTHER),
    (['bash', '-lc', 'cat <<E\n$(x)\nE\n/bin/rm -f y'], OTHER), (['sudo', 'ls'], SUDO),
    (['security', 'find-generic-password', '-w'], OTHER),
])
def test_dentro_del_sandbox_lo_que_se_ve_pregunta_igual(argv, esperado):
    # El sandbox deja borrar en la carpeta de trabajo: ahí puede haber trabajo sin guardar.
    assert dangerous_command_match(argv, dentro_del_sandbox=True) == esperado


# ---------------------------------------------------------------- la política de verdad
REAL = sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)


def politica(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'hogar'))
    pedidos = []

    async def approve(args):
        pedidos.append(args)

    runtime = SimpleNamespace(task={'cwd': str(raiz), 'id': 't'}, excluded_tools=(), tool_specs=ON_REQUEST_TOOLS,
                              state={}, approve=approve, approval_policy=None,
                              manager=SimpleNamespace(event=lambda *a, **k: None, save=lambda: None),
                              process_sandbox=SeatbeltSandbox(str(raiz), tmpdir=str(tmp_path / 'no')))
    tools = WorkspaceTools(runtime)

    async def launch(command, label, extra):
        return {'exit_code': 0, 'output': '', 'sandbox': label, **extra}

    def ejecutar(argv, **opciones):
        args = {'argv': argv, 'purpose': 'prueba', **opciones}
        return asyncio.run(OnRequestPolicy().execute(tools, args, argv, launch, unsandboxed_timeout=5))
    return ejecutar, pedidos


@pytest.mark.skipif(not REAL, reason='requiere Seatbelt (macOS)')
def test_la_politica_no_pregunta_por_un_heredoc_ni_lo_ilegible(tmp_path, monkeypatch):
    ejecutar, pedidos = politica(tmp_path, monkeypatch)
    for argv in (HEREDOC_PY, HEREDOC_CAT, ['bash', '-lc', 'echo ${a[$i]}']):
        resultado = ejecutar(argv)
        assert resultado['sandbox'] == 'seatbelt' and resultado['permisos'] == [], argv
    assert pedidos == []


@pytest.mark.skipif(not REAL, reason='requiere Seatbelt (macOS)')
def test_la_politica_sigue_preguntando_un_rm_y_lo_ilegible_con_red(tmp_path, monkeypatch):
    ejecutar, pedidos = politica(tmp_path, monkeypatch)
    ejecutar(['rm', '-rf', 'src'])
    ejecutar(['bash', '-lc', 'echo ${a[$i]}'], needs_network=True)
    assert [p['motivos'] for p in pedidos] == [['peligroso'], ['red', 'peligroso']]
