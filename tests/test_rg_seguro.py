"""rg nunca ejecuta otros programas sin permiso, y lee referencias/ sin preguntar.

`rg --pre ./guion patrón .` se concedía como «rg de sólo lectura dentro del
worktree» y ejecutaba el guion sin permiso. Y DeepSeek esperó un permiso por un
`rg -n … referencias/codex/…/manifest.rs` que el ticket le mandaba contrastar
(94b43a3a4d, 06/11), porque rg no estaba entre las lecturas de referencia.
"""
import shlex

import pytest

from alfalfa.permisos import ordenes
from alfalfa.permisos.ordenes import rg_ejecuta, safe_command


@pytest.fixture
def lugar(tmp_path, monkeypatch):
    espacio = tmp_path / 'ticket'
    espacio.mkdir()
    (espacio / 'guion').write_text('#!/bin/sh\ntouch ejecutado\n')
    referencias = tmp_path / 'trabajo' / 'referencias'
    (referencias / 'codex' / 'src').mkdir(parents=True)
    (referencias / 'codex' / 'src' / 'manifest.rs').write_text('fn resolve_manifest_path() {}\n')
    (tmp_path / 'fuera.txt').write_text('privado\n')
    (referencias / 'codex' / 'atajo').symlink_to(tmp_path / 'fuera.txt')
    monkeypatch.setenv('ALFALFA_REFERENCIAS', str(referencias))
    monkeypatch.setattr(ordenes, '_repo_del_integrador', lambda: None)
    return espacio, referencias.resolve()


def juzgar(espacio, argv):
    return safe_command(shlex.join(argv), str(espacio))


@pytest.mark.parametrize('argv', [
    ['rg', '--pre', './guion', 'patron', '.'],
    ['rg', '--pre=./guion', 'patron', '.'],
    ['rg', '--pre-glob', '*.rs', '--pre', './guion', 'patron'],
    ['rg', '--hostname-bin', './guion', '--hyperlink-format', 'default', 'patron'],
    ['rg', '--hostname-bin=./guion', 'patron'],
    ['rg', '-z', 'patron', '.'],
    ['rg', '-nz', 'patron', '.'],
    ['rg', '--search-zip', 'patron', '.'],
])
def test_rg_que_ejecuta_programas_pide_permiso_dentro_del_worktree(lugar, argv):
    espacio, _ = lugar
    aprobable, motivo = juzgar(espacio, argv)
    assert not aprobable and 'ejecuta otros programas' in motivo


@pytest.mark.parametrize('argv', [
    ['rg', '-n', 'patron', '.'],
    ['rg', '-n', '--glob', '*.py', '-A', '3', 'patron', 'server'],
    ['rg', '-e', 'zeta', '.'],
    ['rg', '--files'],
])
def test_rg_de_lectura_sigue_concedido(lugar, argv):
    espacio, _ = lugar
    assert juzgar(espacio, argv)[0]


@pytest.mark.parametrize('argv', [
    ['rg', '-n', 'fn resolve_manifest_path', '-A', '40', '{R}/codex/src/manifest.rs'],
    ['rg', '-n', 'resolve_manifest_path', '{R}/codex'],
    ['rg', '--files', '{R}/codex'],
])
def test_rg_lee_referencias_sin_preguntar(lugar, argv):
    espacio, referencias = lugar
    aprobable, motivo = juzgar(espacio, [a.replace('{R}', str(referencias)) for a in argv])
    assert aprobable and 'carpeta de referencia' in motivo


@pytest.mark.parametrize('argv', [
    ['rg', '-L', 'privado', '{R}/codex'],
    ['rg', '--follow', 'privado', '{R}/codex'],
    ['rg', '-nL', 'privado', '{R}/codex'],
    ['rg', '--pre', 'cat', 'privado', '{R}/codex'],
    ['rg', 'privado', '{R}/codex/atajo'],
    ['rg', 'x', '{R}/../fuera.txt'],
    ['rg', 'x', '{R}/codex/*'],
])
def test_rg_sobre_referencias_que_sigue_enlaces_sale_o_ejecuta_pregunta(lugar, argv):
    espacio, referencias = lugar
    assert not juzgar(espacio, [a.replace('{R}', str(referencias)) for a in argv])[0]


def test_detector_de_opciones_que_ejecutan():
    assert rg_ejecuta('--pre') and rg_ejecuta('--pre=x') and rg_ejecuta('-uz')
    assert not rg_ejecuta('--pre-glob') and not rg_ejecuta('-n') and not rg_ejecuta('zeta')
