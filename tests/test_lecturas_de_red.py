"""Consultas con red que sólo leen (gh pr view, git fetch origin): sin tarjeta, en el sandbox con red.

Antes cada `gh pr view` pedía permiso; tras leer contenido externo siguen pidiéndolo.
"""
import io
import json

import pytest

from alfalfa.contenido_externo import MOTIVO as EXTERNO
from alfalfa.permisos.bajo_demanda import lectura_de_red


@pytest.mark.parametrize('argv', [
    ['gh', 'pr', 'view', '75', '--json', 'state,url'], ['gh', 'pr', 'list'], ['gh', 'pr', 'checks', '75'],
    ['gh', 'run', 'view', '123'], ['gh', 'release', 'list'], ['git', 'fetch', 'origin'],
    ['git', 'fetch', 'origin', 'main'], ['/usr/bin/git', 'ls-remote', 'origin'],
])
def test_lecturas_sin_tarjeta(argv):
    assert lectura_de_red(argv)


@pytest.mark.parametrize('argv', [
    ['gh', 'pr', 'create', '--title', 'x'], ['gh', 'pr', 'merge', '75'], ['gh', 'api', 'repos/x/y'],
    ['gh', 'pr', 'view', '75', '--web'], ['gh', 'run', 'view', '1', '--log-failed'], ['git', 'push', 'origin', 'x'],
    ['git', 'pull'], ['git', 'fetch', 'https://ejemplo.com/repo?secreto=1'], ['git', 'fetch', 'git@github.com:a/b'],
    ['git', '-C', 'otra', 'fetch'], ['git', 'fetch', '--upload-pack=sh -c x', 'origin'], ['curl', 'https://x'], [],
])
def test_lo_demas_sigue_preguntando(argv):
    assert not lectura_de_red(argv)


class Runtime:
    def __init__(self, leido=False):
        self.state = {EXTERNO: ['read_web']} if leido else {}


def test_tras_leer_contenido_externo_vuelve_a_preguntar():
    assert lectura_de_red(['gh', 'pr', 'view', '1'], Runtime(leido=False))
    assert not lectura_de_red(['gh', 'pr', 'view', '1'], Runtime(leido=True))


def test_el_uso_de_la_sesion_llega_a_mitad_de_turno(tmp_path, monkeypatch):
    from alfalfa.stdio import Transport
    from alfalfa.terminal import TerminalManager
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    salida = io.StringIO()
    m = TerminalManager('Prueba.', home=tmp_path / 'alfalfa')
    try:
        m.transport = Transport(salida)
        m.task['total_tokens'], m.task['coste_usd'] = 12345, 0.25
        m.context(m.task, 1000, 200000)
        m.context(m.task, 1000, 200000)  # nada cambió: no se repite
        m.task['total_tokens'] = 20000
        m.context(m.task, 1500, 200000)
    finally:
        m.close()
    usos = [json.loads(linea) for linea in salida.getvalue().splitlines()
            if json.loads(linea)['tipo'] == 'uso_sesion']
    assert [(u['tokens'], u.get('coste')) for u in usos] == [(12345, 0.25), (20000, 0.25)]
