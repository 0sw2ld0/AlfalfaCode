"""Diagnósticos tras editar: el agente ve en el mismo paso los errores que acaba de introducir.

Con un servidor de lenguaje simulado (tests/lsp_simulado.py) en lugar de pyright,
que contesta por «pull» o publica él solo, tarda o se cae; con el respaldo de
sintaxis de Python y con ruff si está instalado; y con sourcekit-lsp de verdad
si este Mac lo tiene.
"""
import asyncio
import io
import json
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import diagnosticos as modulo
from alfalfa.diagnosticos import Diagnosticos, activos, linters, nuevos
from alfalfa.proveedores.nativo.descripciones import datos_de_fin
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager, diagnostics

SIMULADO = Path(__file__).with_name('lsp_simulado.py')


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    """pyright-langserver es el simulado; ni ruff ni otros linters del PATH entran."""
    lanzador = tmp_path / 'bin' / 'pyright-langserver'
    lanzador.parent.mkdir()
    lanzador.write_text('#!/bin/sh\nexec ' + sys.executable + ' ' + str(SIMULADO) + ' "$@"\n')
    lanzador.chmod(0o755)
    original = modulo._ejecutable

    def ejecutable(nombre, env, *extra):
        if nombre == 'pyright-langserver':
            return str(lanzador)
        if nombre == 'basedpyright-langserver':
            return None
        return original(nombre, env, *extra)
    monkeypatch.setattr(modulo, '_ejecutable', ejecutable)
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: None)
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    registro = tmp_path / 'metodos.txt'
    monkeypatch.setenv('LSP_SIMULADO_REGISTRO', str(registro))
    return registro


@pytest.fixture
def sesion(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Edita.', home=tmp_path / 'alfalfa')
    rt = m.runtime()
    yield raiz, rt
    m.close()   # mata lo que siga vivo (cada asyncio.run de la prueba es un bucle distinto)


async def soltar_servidores(rt):
    """Cierra los servidores en el bucle que los lanzó, sin apagar los diagnósticos de la sesión."""
    diagnosticos = rt.manager.diagnosticos
    await asyncio.gather(*(c.cerrar() for c in diagnosticos.servidores.values()))
    diagnosticos.servidores.clear()


def llamar(rt, nombre, args):
    async def una():
        try:
            return await rt.tools.call(nombre, args)
        finally:
            await soltar_servidores(rt)
    return asyncio.run(una())


def editar(rt, ruta, texto):
    """write_file con el SHA que toca, como haría el agente."""
    actual = rt.tools.path(ruta)
    sha = 'NEW' if not actual.exists() else __import__('hashlib').sha256(actual.read_bytes()).hexdigest()
    return llamar(rt, 'write_file', {'path': ruta, 'content': texto, 'expected_sha256': sha})


async def en_un_bucle(rt, pasos):
    """Varias ediciones en el mismo bucle de eventos: así sigue vivo el servidor entre ellas."""
    resultados = []
    for ruta, texto in pasos:
        actual = rt.tools.path(ruta)
        sha = 'NEW' if not actual.exists() else __import__('hashlib').sha256(actual.read_bytes()).hexdigest()
        resultados.append(await rt.tools.call('write_file', {'path': ruta, 'content': texto,
                                                             'expected_sha256': sha}))
    await rt.manager.cerrar_diagnosticos()
    return resultados


# --- piezas sueltas ------------------------------------------------------------


def test_nuevos_no_mira_la_linea():
    viejo = {'fuente': 'x', 'codigo': 'c', 'mensaje': 'm', 'gravedad': 'error', 'linea': 1}
    movido = dict(viejo, linea=5)
    otro = dict(viejo, mensaje='otro')
    assert nuevos([viejo], [movido]) == []
    assert nuevos([viejo], [movido, dict(viejo, linea=9)]) == [dict(viejo, linea=9)]
    assert nuevos([viejo], [otro]) == [otro]


def test_ajuste_de_usuario_proyecto_variable_y_forzado(tmp_path, monkeypatch):
    raiz, hogar = tmp_path / 'p', tmp_path / 'h'
    (raiz / '.alfalfa').mkdir(parents=True)
    hogar.mkdir()
    monkeypatch.delenv('ALFALFA_DIAGNOSTICOS', raising=False)
    assert activos(raiz, hogar) is True
    (hogar / 'ajustes.json').write_text('{"diagnosticos": false}')
    assert activos(raiz, hogar) is False
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": true}')
    assert activos(raiz, hogar) is True, 'el del proyecto manda sobre el del usuario'
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": false}')
    (hogar / 'ajustes.json').write_text('no es json')
    assert activos(raiz, hogar) is False
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    assert activos(raiz, hogar) is True
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '0')
    assert activos(raiz, hogar) is False
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    assert activos(raiz, hogar, forzado=False) is False
    d = Diagnosticos(raiz, hogar)
    d.cambiar(False)
    assert d.activos() is False
    d.cambiar(True)
    assert d.activos() is True, 'encender devuelve la decisión a la variable y los archivos'


def test_espera_acotada(monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '0')
    assert modulo.espera() == 0.1
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '1000')
    assert modulo.espera() == modulo.MAX_ESPERA
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', 'rápido')
    assert modulo.espera() == modulo.ESPERA


def test_cli_y_stdio_lo_apagan():
    assert diagnostics(type('A', (), {'sin_diagnosticos': True})()) is False
    assert diagnostics(type('A', (), {'sin_diagnosticos': False})()) is None
    out = io.StringIO()
    t = Transport(out)
    t.receive('{"tipo":"diagnosticos","activo":false}')   # sin sesión aún: inválido
    pedidos = []
    t.diagnostics = pedidos.append
    t.receive('{"tipo":"diagnosticos","activo":false}')
    t.receive('{"tipo":"diagnosticos","activo":"no"}')
    assert pedidos == [False]
    assert out.getvalue().count('inválido') == 2


def test_la_terminal_ofrece_sin_diagnosticos(capsys):
    from alfalfa.terminal import MANUAL, main
    assert 'diagnosticos' in MANUAL and 'run_checks' in MANUAL
    with pytest.raises(SystemExit):
        main(['--help'])
    assert '--sin-diagnosticos' in capsys.readouterr().out


def test_la_app_ve_cuantos_hay_en_el_fin_de_la_llamada():
    datos = datos_de_fin('edit_file', {'path': 'a.py', 'diagnosticos': {'nuevos': ['x'], 'errores': 2,
                                                                       'sin_respuesta': ['y']}})
    assert datos['diagnosticos'] == {'errores': 2, 'incompleto': True}
    assert 'diagnosticos' not in datos_de_fin('edit_file', {'path': 'a.py'})


# --- con el servidor simulado ----------------------------------------------------


def test_el_error_nuevo_sale_en_el_resultado_de_la_edicion(servidor, sesion):
    raiz, rt = sesion
    limpio, con_error, con_otro = asyncio.run(en_un_bucle(rt, [
        ('a.py', 'x = 1\n'),
        ('a.py', 'x = 1\ny = ERROR\n'),
        ('a.py', '# TODO\nx = 1\ny = ERROR\nz = ERROR\n'),
    ]))
    assert limpio['diagnosticos']['nuevos'] == []
    assert limpio['diagnosticos']['revisado_con'] == ['pyright']
    assert 'no es una comprobación' in limpio['diagnosticos']['nota'].lower()
    assert con_error['diagnosticos']['nuevos'] == ['a.py:2:5 error [simulado error] ERROR en la línea']
    assert con_error['diagnosticos']['errores'] == 1
    # El que ya estaba (ahora en la línea 3) no se repite: sólo el nuevo y el aviso.
    otro = con_otro['diagnosticos']
    assert otro['nuevos'] == ['a.py:4:5 error [simulado error] ERROR en la línea',
                              'a.py:1:3 aviso [simulado todo] TODO en la línea']
    assert (otro['errores'], otro['avisos'], otro['ya_habia']) == (1, 1, 1)
    # Un solo servidor para toda la sesión: un initialize y el documento abierto una vez.
    metodos = servidor.read_text().split()
    assert metodos.count('initialize') == 1 and metodos.count('textDocument/didOpen') == 1
    # El cambio que guarda la sesión no lleva los diagnósticos.
    assert all('diagnosticos' not in c for c in rt.state['changes'])


def test_lo_que_ya_habia_antes_de_la_primera_edicion_no_es_nuevo(servidor, sesion):
    raiz, rt = sesion
    (raiz / 'viejo.py').write_text('a = ERROR\n')
    resultado = editar(rt, 'viejo.py', 'a = ERROR\nb = 2\n')
    assert resultado['diagnosticos']['nuevos'] == []
    assert resultado['diagnosticos']['ya_habia'] == 1


def test_servidor_que_publica_el_solo(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_MODO', 'push')
    raiz, rt = sesion
    (primero,) = asyncio.run(en_un_bucle(rt, [('b.py', 'ok = ERROR\n')]))
    assert primero['diagnosticos']['nuevos'] == ['b.py:1:6 error [simulado error] ERROR en la línea']
    # Pidió una vez por «pull», el servidor no sabía, y desde ahí espera lo que publica.
    assert servidor.read_text().split().count('textDocument/diagnostic') == 1


def test_apply_patch_mira_cada_archivo_y_olvida_los_borrados(servidor, sesion):
    raiz, rt = sesion
    (raiz / 'c.py').write_text('c = 1\n')
    (raiz / 'nota.txt').write_text('sin servidor\n')
    resultado = llamar(rt, 'apply_patch', {'input': '*** Begin Patch\n*** Update File: c.py\n@@\n-c = 1\n+c = ERROR\n'
                                                     '*** Add File: d.py\n+# TODO\n*** Update File: nota.txt\n@@\n'
                                                     '-sin servidor\n+ERROR pero no es código\n*** End Patch'})
    assert sorted(resultado['diagnosticos']['nuevos']) == ['c.py:1:5 error [simulado error] ERROR en la línea',
                                                           'd.py:1:3 aviso [simulado todo] TODO en la línea']
    assert resultado['summary'].startswith('Parche aplicado')


def test_un_servidor_lento_no_frena_el_turno(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_RETRASO', '1.5')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '0.5')
    raiz, rt = sesion

    async def dos_ediciones():
        inicio = time.monotonic()
        primero = await rt.tools.call('write_file', {'path': 'e.py', 'content': 'e = ERROR\n',
                                                     'expected_sha256': 'NEW'})
        tardo = time.monotonic() - inicio
        await asyncio.sleep(1.5)  # el servidor sigue arrancando mientras el agente trabaja
        segundo = await rt.tools.call('write_file', {'path': 'f.py', 'content': 'f = ERROR\n',
                                                     'expected_sha256': 'NEW'})
        await rt.manager.cerrar_diagnosticos()
        return primero, tardo, segundo
    primero, tardo, segundo = asyncio.run(dos_ediciones())
    assert tardo < 1.2, 'la edición esperó al servidor más de su plazo'
    assert primero['path'] == 'e.py' and primero['diagnosticos']['nuevos'] == []
    (aviso,) = primero['diagnosticos']['sin_respuesta']
    assert aviso.startswith('pyright no contestó en 0.5 s (sigue arrancando')
    assert segundo['diagnosticos']['nuevos'] == ['f.py:1:5 error [simulado error] ERROR en la línea']


def test_un_servidor_que_se_cae_se_dice_una_vez(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_MODO', 'caida')
    raiz, rt = sesion
    primero, segundo = asyncio.run(en_un_bucle(rt, [('g.py', 'g = 1\n'), ('g.py', 'g = 2\n')]))
    (aviso,) = primero['diagnosticos']['sin_respuesta']
    assert aviso.startswith('pyright falló')
    # No se reintenta ni se repite el aviso: queda el respaldo de sintaxis de Python.
    assert 'sin_respuesta' not in segundo['diagnosticos']
    assert segundo['diagnosticos']['revisado_con'] == ['python (sólo sintaxis)']
    assert servidor.read_text().split().count('initialize') == 1


def test_apagado_no_lanza_nada(servidor, sesion, monkeypatch):
    raiz, rt = sesion
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '0')
    assert 'diagnosticos' not in editar(rt, 'h.py', 'h = ERROR\n')
    monkeypatch.delenv('ALFALFA_DIAGNOSTICOS')
    (raiz / '.alfalfa').mkdir()
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": false}')
    assert 'diagnosticos' not in editar(rt, 'i.py', 'i = ERROR\n')
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": true}')
    rt.manager.diagnosticos.cambiar(False)   # el interruptor de la app
    assert 'diagnosticos' not in editar(rt, 'j.py', 'j = ERROR\n')
    assert not servidor.exists(), 'se lanzó el servidor con los diagnósticos apagados'


def test_la_sesion_cierra_sus_servidores(servidor, sesion):
    raiz, rt = sesion

    async def editar_y_cerrar():
        await rt.tools.call('write_file', {'path': 'k.py', 'content': 'k = 1\n', 'expected_sha256': 'NEW'})
        (cliente,) = rt.manager.diagnosticos.servidores.values()
        proc = cliente.proc
        assert proc.returncode is None
        await rt.manager.cerrar_diagnosticos()
        return proc
    proc = asyncio.run(editar_y_cerrar())
    assert proc.returncode is not None
    assert 'shutdown' in servidor.read_text().split()
    assert rt.manager.diagnosticos.activos() is False


def test_los_diagnosticos_no_son_una_comprobacion(servidor, sesion):
    raiz, rt = sesion
    resultado = editar(rt, 'l.py', 'l = 1\n')
    assert resultado['diagnosticos']['nuevos'] == []
    informe = rt.evidence.report()
    assert informe['status'] == 'not_run' and informe['checks_run'] == 0


def test_el_agente_lo_ve_en_el_mismo_paso(servidor, sesion):
    raiz, rt = sesion
    payloads = []
    pasos = [{'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'write_file', 'arguments': {
        'path': 'm.py', 'content': 'def f():\n    return ERROR\n', 'expected_sha256': 'NEW'}}}]},
        {'role': 'assistant', 'content': 'Hecho.'}]

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        payloads.append(json.loads(json.dumps(payload)))
        return dict(message=pasos.pop(0), done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    async def turno():
        try:
            return await rt.manager.run('Edita.')
        finally:
            await soltar_servidores(rt)
    with patch.object(NativeRuntime, 'request', request):
        assert asyncio.run(turno()) == 0
    resultado = json.loads([m for m in payloads[1]['messages'] if m['role'] == 'tool'][-1]['content'])
    assert resultado['diagnosticos']['nuevos'] == ['m.py:2:12 error [simulado error] ERROR en la línea']


# --- sin servidor de Python, y con los linters del proyecto ------------------------


def test_sin_pyright_ni_ruff_al_menos_la_sintaxis(sesion, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setattr(modulo, '_ejecutable', lambda *a, **k: None)
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: None)
    raiz, rt = sesion
    bueno = editar(rt, 'n.py', 'n = 1\n')
    malo = editar(rt, 'n.py', 'n = (\n')
    assert bueno['diagnosticos']['nuevos'] == [] and bueno['diagnosticos']['revisado_con'] == ['python (sólo sintaxis)']
    (linea,) = malo['diagnosticos']['nuevos']
    assert linea.startswith('n.py:1:') and ' error [python] ' in linea


RUFF = shutil.which('ruff')


@pytest.mark.skipif(RUFF is None, reason='ruff no está instalado')
def test_ruff_de_respaldo_y_del_proyecto(sesion, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setattr(modulo, '_ejecutable', lambda nombre, *a, **k: None)
    raiz, rt = sesion
    # Sin pyright y sin configuración: ruff con sus reglas por defecto.
    malo = editar(rt, 'o.py', 'o = (\n')
    assert malo['diagnosticos']['revisado_con'] == ['ruff']
    assert malo['diagnosticos']['errores'] == 1 and ' error [ruff] SyntaxError' in malo['diagnosticos']['nuevos'][0]
    # Con la configuración del proyecto: sus reglas, y sólo lo nuevo.
    (raiz / 'ruff.toml').write_text('[lint]\nselect = ["F401"]\n')
    (raiz / 'p.py').write_text('import os\n')
    resultado = editar(rt, 'p.py', 'import os\nimport sys\n')
    assert resultado['diagnosticos']['nuevos'] == ['p.py:2:8 aviso [ruff F401] `sys` imported but unused']
    assert resultado['diagnosticos']['ya_habia'] == 1


# --- sourcekit-lsp de verdad ------------------------------------------------------------


SOURCEKIT = modulo._sourcekit(None) if sys.platform == 'darwin' else None


@pytest.mark.skipif(SOURCEKIT is None, reason='sourcekit-lsp no está instalado')
def test_sourcekit_lsp_de_verdad(sesion, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '20')
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: None)
    raiz, rt = sesion
    bueno, malo = asyncio.run(en_un_bucle(rt, [
        ('main.swift', 'let x: Int = 1\nprint(x)\n'),
        ('main.swift', 'let x: Int = "hola"\nprint(x)\n'),
    ]))
    assert bueno['diagnosticos']['nuevos'] == [], bueno
    assert malo['diagnosticos']['errores'] >= 1, malo
    assert malo['diagnosticos']['nuevos'][0].startswith('main.swift:1:'), malo
    assert 'Int' in malo['diagnosticos']['nuevos'][0]
