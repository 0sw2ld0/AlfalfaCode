"""Diagnósticos tras editar: el agente ve en el mismo paso los errores que acaba de introducir.

Con un servidor de lenguaje simulado (tests/lsp_simulado.py) en lugar de pyright,
sourcekit-lsp o typescript-language-server, que contesta por «pull» o publica él
solo, tarda, no contesta o se cae; con linters simulados (ESLint, un ruff roto);
con el respaldo de sintaxis de Python y con ruff si está instalado; y con
sourcekit-lsp de verdad si este Mac lo tiene. Lo que escribe sourcekit-lsp va a
carpetas temporales, nunca a ~/Library/Caches.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alfalfa import diagnosticos as modulo
from alfalfa.diagnosticos import Diagnosticos, activos, linters, lsp, nuevos, paquete
from alfalfa.proveedores.nativo.descripciones import datos_de_fin
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.stdio import Transport
from alfalfa.terminal import TerminalManager, diagnostics

SIMULADO = Path(__file__).with_name('lsp_simulado.py')
ORIGINAL_CARPETA = modulo.carpeta_de_sourcekit


def lanzador(carpeta, nombre, guion):
    """Un ejecutable `nombre` en `carpeta` que corre el guion de Python `guion` con este intérprete."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre
    ruta.write_text('#!/bin/sh\nexec ' + sys.executable + ' ' + str(guion) + ' "$@"\n')
    ruta.chmod(0o755)
    return ruta


def guion(carpeta, nombre, codigo):
    ruta = carpeta / (nombre + '.py')
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta.write_text(codigo)
    return ruta


@pytest.fixture
def simulados(tmp_path, monkeypatch):
    """Qué servidor simulado responde a cada nombre: {nombre: ruta}. Ningún linter del PATH entra."""
    tabla = {}
    original = modulo._ejecutable

    def ejecutable(nombre, env, *extra, raiz=None):
        if nombre in tabla:
            return tabla[nombre]
        if nombre in ('pyright-langserver', 'basedpyright-langserver', 'typescript-language-server'):
            return original(nombre, {'PATH': ''}, *extra, raiz=raiz)
        return original(nombre, env, *extra, raiz=raiz)
    monkeypatch.setattr(modulo, '_ejecutable', ejecutable)
    monkeypatch.setattr(modulo, '_sourcekit', lambda env: tabla.get('sourcekit-lsp', (None,))[0])
    monkeypatch.setattr(modulo, 'carpeta_de_sourcekit', lambda raiz: tmp_path / 'cache-sourcekit')
    monkeypatch.setattr(linters, '_programa',
                        lambda nombre, env, *carpetas, raiz=None: tabla.get(nombre, (None, False)))

    # La descripción del paquete sin swift: un objetivo por carpeta de Sources/ y Tests/. Las pruebas de
    # la preparación de verdad usan swift.
    async def describir(paquete_, sandbox, env):
        tabla.setdefault('descritos', []).append(str(paquete_))
        objetivos_ = []
        for grupo, tipo in (('Sources', 'library'), ('Tests', 'test')):
            for carpeta in sorted(p for p in [Path(paquete_) / grupo, *(Path(paquete_) / grupo).glob('*')]
                                  if p.is_dir()):
                fuentes = sorted(str(f.relative_to(carpeta)) for f in carpeta.glob('*.swift'))
                if fuentes:
                    objetivos_.append({'name': carpeta.name, 'c99name': carpeta.name, 'module_type': 'SwiftTarget',
                                       'type': tipo, 'path': str(carpeta.relative_to(paquete_)), 'sources': fuentes})
        return {'tools_version': '5.9', 'targets': objetivos_}
    monkeypatch.setattr(paquete, 'describir', describir)
    # Ni xcrun ni swiftc de verdad: con la máquina cargada la primera llamada puede tardar segundos, y estas
    # pruebas usan servidores simulados que no compilan nada.
    monkeypatch.setattr(paquete, 'herramientas', lambda: {
        'swift': '/usr/bin/swift', 'swiftc': '/usr/bin/swiftc', 'sdk': '/SDK', 'plataforma': '/Plataforma',
        'triple': 'arm64-apple-macosx26.0'})
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setenv('LSP_SIMULADO_REGISTRO', str(tmp_path / 'metodos.txt'))
    # Con la suite cargada (sourcekit-lsp de verdad en otras pruebas) los simulados pueden pasar de 3 s;
    # las pruebas del plazo ponen el suyo.
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '15')
    return tabla


@pytest.fixture
def servidor(tmp_path, simulados):
    """pyright-langserver es el simulado. Devuelve el registro de lo que recibe."""
    simulados['pyright-langserver'] = (str(lanzador(tmp_path / 'bin', 'pyright-langserver', SIMULADO)), False)
    return tmp_path / 'metodos.txt'


@pytest.fixture
def sesion(tmp_path, monkeypatch):
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    monkeypatch.chdir(raiz)
    m = TerminalManager('Edita.', home=tmp_path / 'alfalfa')
    rt = m.runtime()
    yield raiz, rt
    m.close()   # mata lo que siga vivo (cada asyncio.run de la prueba es un bucle distinto)


@pytest.fixture
def permisos(sesion):
    """Las peticiones de permiso que llegan al usuario; `respuesta['si']` decide si las concede."""
    raiz, rt = sesion
    pedidas, respuesta = [], {'si': True}

    async def approve(peticion):
        pedidas.append(peticion)
        if not respuesta['si']:
            raise ValueError('El usuario rechazó este comando.')
    rt.approve = approve
    return pedidas, respuesta


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


def sha_de(rt, ruta):
    actual = rt.tools.path(ruta)
    return 'NEW' if not actual.exists() else hashlib.sha256(actual.read_bytes()).hexdigest()


def editar(rt, ruta, texto):
    """write_file con el SHA que toca, como haría el agente."""
    return llamar(rt, 'write_file', {'path': ruta, 'content': texto, 'expected_sha256': sha_de(rt, ruta)})


async def escribir(rt, ruta, texto):
    return await rt.tools.call('write_file', {'path': ruta, 'content': texto, 'expected_sha256': sha_de(rt, ruta)})


async def hasta_que_conteste(rt, ruta, texto, limite=60):
    """Escribe `texto` (alternando con un comentario para que cambie) hasta que sourcekit-lsp conteste."""
    fin = time.monotonic() + limite
    vuelta = 0
    while True:
        resultado = await escribir(rt, ruta, texto if vuelta % 2 == 0 else '// espera\n' + texto)
        diagnosticos = resultado.get('diagnosticos') or {}
        if any(c.startswith('sourcekit-lsp') for c in diagnosticos.get('revisado_con', [])) and vuelta % 2 == 0:
            return resultado
        if time.monotonic() > fin:
            return resultado
        vuelta += 1
        await asyncio.sleep(0.5)


async def en_un_bucle(rt, pasos):
    """Varias ediciones en el mismo bucle de eventos: así sigue vivo el servidor entre ellas."""
    resultados = [await escribir(rt, ruta, texto) for ruta, texto in pasos]
    await rt.manager.cerrar_diagnosticos()
    return resultados


# --- piezas sueltas ------------------------------------------------------------


def _d(linea, mensaje='m'):
    return {'fuente': 'x', 'codigo': 'c', 'mensaje': mensaje, 'gravedad': 'error', 'linea': linea}


def test_nuevos_alinea_las_lineas_con_difflib():
    antes = 'a\nx = ERROR\nb\n'
    # Una línea añadida encima: el error viejo bajó a la 3 y no es nuevo.
    assert nuevos([_d(2)], [_d(3)], antes, 'nueva\na\nx = ERROR\nb\n') == []
    # Se arregla el de la línea 2 y aparece uno igual en otra: ése sí es nuevo.
    assert nuevos([_d(2)], [_d(4)], antes, 'a\nx = 1\nb\nz = ERROR\n') == [_d(4)]
    # El mismo error en una línea cambiada en su sitio sigue siendo el de antes.
    assert nuevos([_d(2)], [_d(2)], antes, 'a\nx = ERROR + 1\nb\n') == []
    # Un error que aparece en una línea que no se tocó (por un cambio en otra) es nuevo.
    assert nuevos([], [_d(1)], antes, 'a\nx = ERROR\nb\n') == [_d(1)]
    # Sin los textos se compara por clave, sin mirar la línea.
    assert nuevos([_d(1)], [_d(5)]) == []
    assert nuevos([_d(1)], [_d(5), _d(9)]) == [_d(9)]
    assert nuevos([_d(1)], [_d(1, 'otro')]) == [_d(1, 'otro')]


def test_el_proyecto_solo_puede_apagarlo(tmp_path, monkeypatch):
    raiz, hogar = tmp_path / 'p', tmp_path / 'h'
    (raiz / '.alfalfa').mkdir(parents=True)
    hogar.mkdir()
    monkeypatch.delenv('ALFALFA_DIAGNOSTICOS', raising=False)
    assert activos(raiz, hogar) is True
    (hogar / 'ajustes.json').write_text('{"diagnosticos": false}')
    assert activos(raiz, hogar) is False
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": true}')
    assert activos(raiz, hogar) is False, 'el proyecto no enciende lo que el usuario apagó'
    (hogar / 'ajustes.json').write_text('{"diagnosticos": true}')
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": false}')
    assert activos(raiz, hogar) is False, 'el proyecto sí puede apagarlo'
    (hogar / 'ajustes.json').write_text('no es json')
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{}')
    assert activos(raiz, hogar) is True
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '0')
    assert activos(raiz, hogar) is False
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{"diagnosticos": false}')
    assert activos(raiz, hogar) is False, 'ni la variable enciende lo que el proyecto apagó'
    (raiz / '.alfalfa' / 'ajustes.json').write_text('{}')
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


def test_sintaxis_python_nunca_lanza(monkeypatch):
    assert modulo.sintaxis_python('a.py', 'x = (\n')[0]['gravedad'] == 'error'

    def revienta(*a, **k):
        raise MemoryError()
    monkeypatch.setattr(modulo, 'compile', revienta, raising=False)
    assert modulo.sintaxis_python('a.py', 'x = 1\n') == []


def test_la_carpeta_de_sourcekit_no_es_temporal():
    carpeta = ORIGINAL_CARPETA('/proyecto')   # la de verdad, sin la guardia de las pruebas
    assert carpeta.is_relative_to(Path.home() / 'Library' / 'Caches' / 'alfalfa' / 'sourcekit')


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
    # Un solo servidor para toda la sesión, y ningún documento queda abierto entre ediciones.
    metodos = servidor.read_text().split()
    assert metodos.count('initialize') == 1
    assert metodos.count('textDocument/didOpen') == metodos.count('textDocument/didClose')
    # El cambio que guarda la sesión no lleva los diagnósticos.
    assert all('diagnosticos' not in c for c in rt.state['changes'])


def test_lo_que_ya_habia_antes_de_la_primera_edicion_no_es_nuevo(servidor, sesion):
    raiz, rt = sesion
    (raiz / 'viejo.py').write_text('a = ERROR\n')
    resultado = editar(rt, 'viejo.py', 'b = 2\na = ERROR\n')
    assert resultado['diagnosticos']['nuevos'] == []
    assert resultado['diagnosticos']['ya_habia'] == 1


def test_servidor_que_publica_el_solo(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_MODO', 'push')
    raiz, rt = sesion
    primero, segundo = asyncio.run(en_un_bucle(rt, [('b.py', 'ok = ERROR\n'), ('b.py', 'ok = ERROR\n# TODO\n')]))
    assert primero['diagnosticos']['nuevos'] == ['b.py:1:6 error [simulado error] ERROR en la línea']
    assert segundo['diagnosticos']['nuevos'] == ['b.py:2:3 aviso [simulado todo] TODO en la línea']
    # Pidió una vez por «pull», el servidor no sabía, y desde ahí espera lo que publica.
    assert servidor.read_text().split().count('textDocument/diagnostic') == 1


def test_apply_patch_mira_cada_archivo(servidor, sesion):
    raiz, rt = sesion
    (raiz / 'c.py').write_text('c = 1\n')
    (raiz / 'nota.txt').write_text('sin servidor\n')
    resultado = llamar(rt, 'apply_patch', {'input': '*** Begin Patch\n*** Update File: c.py\n@@\n-c = 1\n+c = ERROR\n'
                                                     '*** Add File: d.py\n+# TODO\n*** Update File: nota.txt\n@@\n'
                                                     '-sin servidor\n+ERROR pero no es código\n*** End Patch'})
    assert sorted(resultado['diagnosticos']['nuevos']) == ['c.py:1:5 error [simulado error] ERROR en la línea',
                                                           'd.py:1:3 aviso [simulado todo] TODO en la línea']
    assert resultado['summary'].startswith('Parche aplicado')


def test_demasiados_archivos_se_dice(servidor, sesion, monkeypatch):
    monkeypatch.setattr(modulo, 'MAX_ARCHIVOS', 1)
    raiz, rt = sesion
    resultado = llamar(rt, 'apply_patch', {'input': '*** Begin Patch\n*** Add File: e1.py\n+a = 1\n'
                                                     '*** Add File: e2.py\n+b = ERROR\n*** End Patch'})
    assert resultado['diagnosticos']['archivos_sin_mirar'] == 1
    assert any('primeros 1 archivos' in s for s in resultado['diagnosticos']['sin_respuesta'])


def test_un_servidor_lento_no_frena_el_turno(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_RETRASO', '1.5')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '0.5')
    raiz, rt = sesion

    async def dos_ediciones():
        inicio = time.monotonic()
        primero = await escribir(rt, 'e.py', 'e = ERROR\n')
        tardo = time.monotonic() - inicio
        # El servidor sigue arrancando mientras el agente trabaja. Esperar su señal,
        # no el tiempo nominal del simulado: la carga del Mac también cuenta.
        (cliente,) = rt.manager.diagnosticos.servidores.values()
        await asyncio.wait_for(asyncio.shield(cliente.arrancar()), 15)
        segundo = await escribir(rt, 'f.py', 'f = ERROR\n')
        await rt.manager.cerrar_diagnosticos()
        return primero, tardo, segundo
    primero, tardo, segundo = asyncio.run(dos_ediciones())
    assert tardo < 1.2, 'la edición esperó al servidor más de su plazo'
    assert primero['path'] == 'e.py' and primero['diagnosticos']['nuevos'] == []
    (aviso,) = primero['diagnosticos']['sin_respuesta']
    assert aviso.startswith('pyright no contestó en 0.5 s (sigue arrancando')
    assert segundo['diagnosticos']['nuevos'] == ['f.py:1:5 error [simulado error] ERROR en la línea']


def test_un_arranque_eterno_se_abandona(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_RETRASO', '30')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '0.3')
    monkeypatch.setattr(lsp, 'MAX_ARRANQUE', 0.6)
    raiz, rt = sesion

    async def ediciones():
        primero = await escribir(rt, 'g.py', 'g = 1\n')
        await asyncio.sleep(0.8)
        inicio = time.monotonic()
        segundo = await escribir(rt, 'g.py', 'g = (\n')
        tardo = time.monotonic() - inicio
        tercero = await escribir(rt, 'g.py', 'g = 2\n')
        await rt.manager.cerrar_diagnosticos()
        return primero, segundo, tardo, tercero
    primero, segundo, tardo, tercero = asyncio.run(ediciones())
    assert 'no contestó' in primero['diagnosticos']['sin_respuesta'][0]
    assert tardo < 0.25, 'un servidor perdido no debe costar el plazo en cada edición'
    assert any('no terminó de arrancar' in s for s in segundo['diagnosticos'].get('sin_respuesta', []))
    # Sin pyright queda la sintaxis de Python.
    assert segundo['diagnosticos']['revisado_con'] == [modulo.SOLO_SINTAXIS]
    assert segundo['diagnosticos']['errores'] == 1
    assert tercero['diagnosticos']['revisado_con'] == [modulo.SOLO_SINTAXIS]


def test_cancelar_el_turno_no_deja_nada_colgado(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_MODO', 'mudo')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '5')
    raiz, rt = sesion

    async def cancelada():
        tarea = asyncio.ensure_future(escribir(rt, 'h.py', 'h = 1\n'))
        async def esperando_diagnosticos():
            while not rt.manager.diagnosticos.servidores:
                await asyncio.sleep(0.01)
            (cliente,) = rt.manager.diagnosticos.servidores.values()
            await asyncio.shield(cliente.arrancar())
            while 'textDocument/diagnostic' not in servidor.read_text():
                await asyncio.sleep(0.01)
        # La cancelación se prueba durante una petición muda, con el arranque acabado.
        await asyncio.wait_for(esperando_diagnosticos(), 15)
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)
        (cliente,) = rt.manager.diagnosticos.servidores.values()
        bloqueado = cliente.lock.locked()
        sueltas = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                   and '_con_' in repr(t.get_coro())]
        monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '0.3')
        inicio = time.monotonic()
        otra = await escribir(rt, 'h.py', 'h = 2\n')
        tardo = time.monotonic() - inicio
        await rt.manager.cerrar_diagnosticos()
        return bloqueado, sueltas, tardo, otra
    bloqueado, sueltas, tardo, otra = asyncio.run(cancelada())
    assert not bloqueado and sueltas == []
    assert tardo < 0.8
    assert 'no contestó en 0.3 s' in otra['diagnosticos']['sin_respuesta'][0]


def test_un_servidor_que_se_cae_se_dice_una_vez(servidor, sesion, monkeypatch):
    monkeypatch.setenv('LSP_SIMULADO_MODO', 'caida')
    raiz, rt = sesion
    primero, segundo = asyncio.run(en_un_bucle(rt, [('g.py', 'g = 1\n'), ('g.py', 'g = 2\n')]))
    (aviso,) = primero['diagnosticos']['sin_respuesta']
    assert aviso.startswith('pyright falló')
    # No se reintenta ni se repite el aviso: queda el respaldo de sintaxis de Python.
    assert 'sin_respuesta' not in segundo['diagnosticos']
    assert segundo['diagnosticos']['revisado_con'] == [modulo.SOLO_SINTAXIS]
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
        await escribir(rt, 'k.py', 'k = 1\n')
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


def test_un_fallo_de_los_diagnosticos_no_tumba_la_edicion(servidor, sesion, monkeypatch):
    raiz, rt = sesion

    async def revienta(*a, **k):
        raise RecursionError('demasiado hondo')
    monkeypatch.setattr(Diagnosticos, 'tras_editar', revienta)
    resultado = editar(rt, 'm.py', 'm = 1\n')
    assert resultado['path'] == 'm.py' and (raiz / 'm.py').read_text() == 'm = 1\n'
    assert 'demasiado hondo' in resultado['diagnosticos']['sin_respuesta'][0]


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


# --- permisos según el modo ------------------------------------------------------


def test_pyright_pide_permiso_en_manual_y_no_en_auto(servidor, sesion, permisos):
    raiz, rt = sesion
    pedidas, respuesta = permisos
    assert editar(rt, 'n.py', 'n = ERROR\n')['diagnosticos']['revisado_con'] == ['pyright']
    assert pedidas == [], 'en Auto pyright corre en el sandbox sin preguntar'
    rt.task['modo'] = 'ediciones'
    respuesta['si'] = False
    primero = editar(rt, 'o.py', 'o = 1\n')
    segundo = editar(rt, 'o.py', 'o = (\n')
    assert len(pedidas) == 1 and pedidas[0]['motivo'] == 'modo' and 'sin_sandbox' not in pedidas[0]
    assert 'no se dio permiso' in primero['diagnosticos']['sin_respuesta'][0]
    # Sin permiso para pyright, la sintaxis; y el «no» no se vuelve a preguntar.
    assert segundo['diagnosticos']['revisado_con'] == [modulo.SOLO_SINTAXIS]
    assert segundo['diagnosticos']['errores'] == 1


def test_typescript_prefiere_el_global_y_pide_permiso_para_el_del_proyecto(tmp_path, simulados, sesion, permisos):
    raiz, rt = sesion
    pedidas, _ = permisos
    del_proyecto = lanzador(raiz / 'node_modules' / '.bin', 'typescript-language-server', SIMULADO)
    (raiz / 'tsconfig.json').write_text('{}')
    resultado = editar(rt, 'a.ts', 'const a = ERROR\n')
    assert len(pedidas) == 1 and str(del_proyecto) in pedidas[0]['argv'][0], 'el de node_modules pide permiso'
    assert resultado['diagnosticos']['nuevos'] == ['a.ts:1:11 error [simulado error] ERROR en la línea']
    pedidas.clear()
    global_ = lanzador(tmp_path / 'global', 'typescript-language-server', SIMULADO)
    simulados['typescript-language-server'] = (str(global_), False)
    rt.manager.diagnosticos.servidores.clear()
    otro = editar(rt, 'b.ts', 'const b = ERROR\n')
    assert pedidas == [], 'el del PATH, en Auto y en el sandbox, no pregunta'
    assert otro['diagnosticos']['revisado_con'] == ['typescript-language-server']


class SandboxDePrueba:
    """Apunta cada orden que envuelve; `disponible = False` hace como un sistema sin Seatbelt."""

    def __init__(self, disponible=True):
        self.disponible = disponible
        self.envueltas = []

    async def envolver(self, argv):
        from alfalfa.seatbelt import SandboxUnavailable
        if not self.disponible:
            raise SandboxUnavailable('no hay sandbox Seatbelt en este sistema')
        self.envueltas.append(list(argv))
        return list(argv)


ESLINT_FALSO = '''import json, sys
texto = sys.stdin.read()
mensajes = [{"ruleId": "no-undef", "severity": 2, "message": "ERROR no definido", "line": n + 1, "column": 1}
            for n, linea in enumerate(texto.splitlines()) if "ERROR" in linea]
print(json.dumps([{"messages": mensajes}]))
sys.exit(1 if mensajes else 0)
'''


def test_eslint_en_el_sandbox_y_con_permiso_segun_el_modo(tmp_path, simulados, sesion, permisos):
    raiz, rt = sesion
    pedidas, _ = permisos
    sandbox = rt.manager.diagnosticos.sandbox = SandboxDePrueba()
    (raiz / 'eslint.config.js').write_text('export default []\n')
    programa = lanzador(tmp_path / 'global', 'eslint', guion(tmp_path, 'eslint_falso', ESLINT_FALSO))
    simulados['eslint'] = (str(programa), False)
    resultado = editar(rt, 'c.js', 'const c = ERROR\n')
    assert resultado['diagnosticos']['nuevos'] == ['c.js:1:1 error [eslint no-undef] ERROR no definido']
    assert pedidas == [] and any(str(programa) in argv for argv in sandbox.envueltas)
    rt.task['modo'] = 'manual'
    editar(rt, 'd.js', 'const d = 1\n')
    editar(rt, 'd.js', 'const d = 2\n')
    de_eslint = [p for p in pedidas if p['motivo'] != 'edicion']   # Manual también pregunta cada edición
    assert len(de_eslint) == 1 and de_eslint[0]['motivo'] == 'modo', 'en Manual pregunta una vez por sesión'
    # Sin sandbox no corre: cuenta como fallido, nunca como limpio.
    rt.manager.diagnosticos.sandbox = SandboxDePrueba(disponible=False)
    sin = editar(rt, 'e.js', 'const e = ERROR\n')
    assert 'revisado_con' not in sin['diagnosticos']
    assert 'eslint falló' in sin['diagnosticos']['sin_respuesta'][0]


def test_eslint_del_proyecto_pide_permiso_tambien_en_auto(tmp_path, simulados, sesion, permisos):
    raiz, rt = sesion
    pedidas, respuesta = permisos
    rt.manager.diagnosticos.sandbox = SandboxDePrueba()
    (raiz / '.eslintrc.json').write_text('{}')
    propio = lanzador(raiz / 'node_modules' / '.bin', 'eslint', guion(tmp_path, 'eslint_falso', ESLINT_FALSO))
    simulados['eslint'] = (str(propio), True)   # sólo el de node_modules/.bin
    respuesta['si'] = False
    resultado = editar(rt, 'f.js', 'const f = ERROR\n')
    assert len(pedidas) == 1 and 'node_modules' in pedidas[0]['argv'][0]
    assert 'no se dio permiso' in resultado['diagnosticos']['sin_respuesta'][0]


RUFF_ROTO = 'import sys\nsys.stderr.write("error: regla desconocida XYZ\\n")\nsys.exit(2)\n'


def test_un_linter_roto_no_es_limpio(tmp_path, simulados, sesion):
    raiz, rt = sesion
    (raiz / 'ruff.toml').write_text('[lint]\nselect = ["XYZ"]\n')
    simulados['ruff'] = (str(lanzador(tmp_path / 'global', 'ruff', guion(tmp_path, 'ruff_roto', RUFF_ROTO))), False)
    resultado = editar(rt, 'p.py', 'p = (\n')
    diagnosticos = resultado['diagnosticos']
    assert 'ruff falló (código 2: error: regla desconocida XYZ)' in diagnosticos['sin_respuesta'][0]
    # Sin pyright y con ruff roto, la sintaxis de Python sigue mirando.
    assert diagnosticos['revisado_con'] == [modulo.SOLO_SINTAXIS] and diagnosticos['errores'] == 1


# --- sourcekit-lsp (simulado y de verdad) --------------------------------------------


@pytest.fixture
def sourcekit(tmp_path, simulados):
    simulados['sourcekit-lsp'] = (str(lanzador(tmp_path / 'bin', 'sourcekit-lsp', SIMULADO)), False)
    return tmp_path / 'metodos.txt'


@pytest.mark.parametrize('trampa', ['buildServer.json', '.bsp/servidor.json', '.sourcekit-lsp/config.json',
                                    'compile_commands.json', 'compile_flags.txt', 'build/compile_commands.json'])
def test_sourcekit_no_arranca_con_archivos_trampa(sourcekit, sesion, permisos, trampa):
    raiz, rt = sesion
    pedidas, _ = permisos
    (raiz / 'Fuentes').mkdir()
    (raiz / trampa).parent.mkdir(parents=True, exist_ok=True)
    (raiz / trampa).write_text('{"argv": ["/bin/sh", "-c", "touch /tmp/trampa"]}')
    resultado = editar(rt, 'Fuentes/a.swift', 'let a: Int = ERROR\n')
    (aviso,) = resultado['diagnosticos']['sin_respuesta']
    assert 'no se lanza porque el proyecto trae ' + trampa.split('/')[0] in aviso
    assert pedidas == [] and not sourcekit.exists(), 'ni se pregunta ni se arranca'


def test_sourcekit_se_cierra_si_aparece_una_trampa(sourcekit, sesion, permisos):
    raiz, rt = sesion

    async def ediciones():
        primero = await escribir(rt, 'a.swift', 'let a = ERROR\n')
        (cliente,) = rt.manager.diagnosticos.servidores.values()
        (raiz / 'buildServer.json').write_text('{}')
        segundo = await escribir(rt, 'a.swift', 'let a = 1\n')
        await rt.manager.cerrar_diagnosticos()
        return primero, cliente, segundo
    primero, cliente, segundo = asyncio.run(ediciones())
    assert primero['diagnosticos']['errores'] == 1
    assert cliente.proc.returncode is not None and not rt.manager.diagnosticos.servidores
    assert 'buildServer.json' in segundo['diagnosticos']['sin_respuesta'][0]


def test_sourcekit_pide_salir_del_sandbox_una_vez_por_raiz(sourcekit, sesion, permisos):
    raiz, rt = sesion
    pedidas, respuesta = permisos
    for nombre in ('A', 'B'):
        (raiz / nombre / 'Sources').mkdir(parents=True)
        (raiz / nombre / 'Package.swift').write_text('// swift-tools-version:5.9\n')
    editar(rt, 'A/Sources/a.swift', 'let a = 1\n')
    editar(rt, 'A/Sources/a.swift', 'let a = 2\n')
    assert len(pedidas) == 1 and pedidas[0]['motivo'] == 'salir_sandbox' and pedidas[0]['sin_sandbox']
    assert str(raiz / 'A') in pedidas[0]['purpose']
    respuesta['si'] = False
    tercero = editar(rt, 'B/Sources/b.swift', 'let b = ERROR\n')
    cuarto = editar(rt, 'B/Sources/b.swift', 'let b = 2\n')
    assert len(pedidas) == 2, 'otra raíz, otra pregunta; el «no» no se repite'
    assert 'no se dio permiso' in tercero['diagnosticos']['sin_respuesta'][0]
    assert 'diagnosticos' not in cuarto


def test_sourcekit_en_omitir_permisos_no_pregunta(sourcekit, sesion, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '30')   # holgado: aquí no se prueba el plazo
    raiz, rt = sesion
    rt.task['modo'] = 'omitir'
    resultado = editar(rt, 'a.swift', 'let a = ERROR\n')
    assert resultado['diagnosticos']['errores'] == 1, resultado
    assert resultado['diagnosticos']['revisado_con'] == ['sourcekit-lsp'], resultado


def test_dos_paquetes_swift_en_un_parche(sourcekit, sesion, permisos):
    raiz, rt = sesion
    for nombre in ('A', 'B'):
        (raiz / nombre / 'Sources').mkdir(parents=True)
        (raiz / nombre / 'Package.swift').write_text('// swift-tools-version:5.9\n')
    resultado = llamar(rt, 'apply_patch', {'input': '*** Begin Patch\n*** Add File: A/Sources/a.swift\n+let a = ERROR\n'
                                                     '*** Add File: B/Sources/b.swift\n+let b = ERROR\n*** End Patch'})
    diagnosticos = resultado['diagnosticos']
    assert diagnosticos['errores'] == 2
    assert sorted(diagnosticos['revisado_con']) == ['sourcekit-lsp (A)', 'sourcekit-lsp (B)']


SOURCEKIT = modulo._sourcekit(None) if sys.platform == 'darwin' else None


@pytest.mark.skipif(SOURCEKIT is None, reason='sourcekit-lsp no está instalado')
def test_sourcekit_lsp_de_verdad(sesion, permisos, monkeypatch, tmp_path):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '20')
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: (None, False))
    monkeypatch.setattr(modulo, 'carpeta_de_sourcekit', lambda raiz: tmp_path / 'cache-sourcekit')
    raiz, rt = sesion
    bueno, malo = asyncio.run(en_un_bucle(rt, [
        ('main.swift', 'let x: Int = 1\nprint(x)\n'),
        ('main.swift', 'let x: Int = "hola"\nprint(x)\n'),
    ]))
    assert bueno['diagnosticos']['nuevos'] == [], bueno
    assert malo['diagnosticos']['errores'] >= 1, malo
    assert malo['diagnosticos']['nuevos'][0].startswith('main.swift:1:'), malo
    assert 'Int' in malo['diagnosticos']['nuevos'][0]


@pytest.mark.skipif(SOURCEKIT is None, reason='sourcekit-lsp no está instalado')
def test_sourcekit_de_verdad_en_un_paquete_cabe_en_el_plazo(sesion, permisos, monkeypatch, tmp_path):
    """La primera edición de un paquete (que hay que preparar en el sandbox y donde sourcekit-lsp arranca) no
    espera a los diagnósticos más del plazo que se le da, y la siguiente trae el error. Se mide lo que tarda
    `tras_editar` frente a ese plazo, con margen para una máquina cargada, no un tiempo absoluto."""
    plazo = 1.0   # más corto que preparar el paquete: si se esperara a la preparación, se notaría
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', str(plazo))
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: (None, False))
    duraciones = []
    original = Diagnosticos.tras_editar

    async def cronometrada(self, *args, **kwargs):
        inicio = time.monotonic()
        try:
            return await original(self, *args, **kwargs)
        finally:
            duraciones.append(time.monotonic() - inicio)
    monkeypatch.setattr(Diagnosticos, 'tras_editar', cronometrada)
    raiz, rt = sesion
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'Package.swift').write_text('// swift-tools-version:5.9\nimport PackageDescription\n'
                                        'let package = Package(name: "Demo", targets: [.target(name: "Demo")])\n')
    ruta = 'Sources/Demo/Suma.swift'

    async def ediciones():
        valido = 'public func suma(_ a: Int) -> Int {\n    return a + 1\n}\n'
        primero = await escribir(rt, ruta, valido)
        # Ya no se mide el plazo: hasta que el paquete esté preparado y el servidor arrancado, cada edición
        # dice que no contestó. Se espera con código válido para fijar la línea base antes de crear el error.
        monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '20')
        listo = await hasta_que_conteste(rt, ruta, valido)
        assert 'sourcekit-lsp' in listo['diagnosticos'].get('revisado_con', []), listo
        assert listo['diagnosticos']['errores'] == 0, listo
        assert listo['diagnosticos'].get('ya_habia', 0) == 0, listo
        segundo = await escribir(rt, ruta, 'public func suma(_ a: Int) -> Int {\n    return a + "b"\n}\n')
        await rt.manager.cerrar_diagnosticos()
        return primero, segundo
    primero, segundo = asyncio.run(ediciones())
    assert duraciones[0] < plazo + 2.5, ('la primera edición esperó ' + str(round(duraciones[0], 2))
                                         + ' s a los diagnósticos con un plazo de ' + str(plazo) + ' s')
    diagnosticos = primero['diagnosticos']
    assert 'errores' in diagnosticos and 'avisos' in diagnosticos, primero
    assert diagnosticos.get('revisado_con') or 'no contestó en 1 s' in ' '.join(diagnosticos['sin_respuesta'])
    assert segundo['diagnosticos']['errores'] >= 1, segundo
    assert segundo['diagnosticos']['nuevos'][0].startswith(ruta + ':2:'), segundo
    assert not (raiz / '.build').exists() and not (raiz / '.swiftpm').exists(), 'nadie abrió el paquete con SwiftPM'


@pytest.mark.parametrize('diagnostico,gravedad,codigo,mensaje', [
    ({'code': None, 'message': 'SyntaxError: unexpected EOF while parsing'},
     'error', None, 'SyntaxError: unexpected EOF while parsing'),
    ({'code': 'invalid-syntax', 'severity': 'error', 'message': 'unexpected EOF while parsing'},
     'error', None, 'SyntaxError: unexpected EOF while parsing'),
    ({'code': 'invalid-syntax', 'severity': 'error', 'message': 'SyntaxError: unexpected EOF while parsing'},
     'error', None, 'SyntaxError: unexpected EOF while parsing'),
    ({'code': 'F401', 'severity': 'error', 'message': '`os` imported but unused'},
     'aviso', 'F401', '`os` imported but unused'),
])
def test_ruff_conserva_clasificacion_y_rotulo_del_json(tmp_path, monkeypatch, diagnostico, gravedad, codigo, mensaje):
    async def simular(*_args, **_kwargs):
        salida = dict(diagnostico, location={'row': 1, 'column': 6})
        return json.dumps([salida]).encode(), b'', 1

    monkeypatch.setattr(linters, 'correr', simular)
    linter = linters.Ruff('ruff', '/ruff', tmp_path, {}, [])
    filas = asyncio.run(linter.lint('o.py', 'o = (\n', None))
    assert filas == [{'path': 'o.py', 'linea': 1, 'columna': 6, 'gravedad': gravedad,
                      'fuente': 'ruff', 'codigo': codigo, 'mensaje': mensaje}]
    fuente = 'ruff ' + codigo if codigo else 'ruff'
    assert modulo.texto_de(filas[0]) == 'o.py:1:6 ' + gravedad + ' [' + fuente + '] ' + mensaje


RUFF = shutil.which('ruff')


@pytest.mark.skipif(RUFF is None, reason='ruff no está instalado')
def test_ruff_de_respaldo_y_del_proyecto(sesion, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setattr(modulo, '_ejecutable', lambda nombre, *a, **k: (None, False))
    raiz, rt = sesion
    # Sin pyright y sin configuración: ruff con sus reglas por defecto.
    malo = editar(rt, 'o.py', 'o = (\n')
    assert malo['diagnosticos']['revisado_con'] == ['ruff']
    assert malo['diagnosticos']['errores'] == 1 and ' error [ruff] SyntaxError' in malo['diagnosticos']['nuevos'][0]
    # Un nombre sin definir es un error, no un aviso.
    indefinido = editar(rt, 'q.py', 'print(no_existe)\n')
    assert indefinido['diagnosticos']['nuevos'] == ['q.py:1:7 error [ruff F821] Undefined name `no_existe`']
    # Con la configuración del proyecto: sus reglas, y sólo lo nuevo.
    (raiz / 'ruff.toml').write_text('[lint]\nselect = ["F401"]\n')
    (raiz / 'p.py').write_text('import os\n')
    resultado = editar(rt, 'p.py', 'import os\nimport sys\n')
    assert resultado['diagnosticos']['nuevos'] == ['p.py:2:8 aviso [ruff F401] `sys` imported but unused']
    assert resultado['diagnosticos']['ya_habia'] == 1
    # Una regla que no existe hace fallar a ruff: no cuenta como limpio.
    (raiz / 'ruff.toml').write_text('[lint]\nselect = ["NOEXISTE999"]\n')
    roto = editar(rt, 'p.py', 'import os\nimport sys\nx = (\n')
    assert 'ruff falló' in roto['diagnosticos']['sin_respuesta'][0]
    assert roto['diagnosticos']['revisado_con'] == [modulo.SOLO_SINTAXIS]


# --- paquetes que harían ejecutar algo a sourcekit-lsp (segunda revisión) ----------------------------


def test_un_programa_del_path_dentro_del_proyecto_es_del_proyecto(tmp_path):
    raiz = tmp_path / 'proyecto'
    bins = raiz / 'node_modules' / '.bin'
    bins.mkdir(parents=True)
    programa = bins / 'typescript-language-server'
    programa.write_text('#!/bin/sh\n')
    programa.chmod(0o755)
    # direnv o una entrada relativa ponen el node_modules/.bin del repo en el PATH.
    assert modulo._ejecutable('typescript-language-server', {'PATH': str(bins)}, raiz=raiz) == (str(programa), True)
    # También a través de un enlace desde fuera.
    fuera = tmp_path / 'fuera'
    fuera.mkdir()
    (fuera / 'eslint').symlink_to(programa)
    assert linters._programa('eslint', {'PATH': str(fuera)}, raiz=raiz) == (str(fuera / 'eslint'), True)
    otro = tmp_path / 'global'
    otro.mkdir()
    (otro / 'eslint').write_text('#!/bin/sh\n')
    (otro / 'eslint').chmod(0o755)
    assert linters._programa('eslint', {'PATH': str(otro)}, raiz=raiz) == (str(otro / 'eslint'), False)
    assert modulo.dentro_de(str(raiz), raiz) and not modulo.dentro_de(str(tmp_path / 'proyecto2'), raiz)


def test_typescript_del_path_dentro_del_proyecto_pide_permiso(tmp_path, monkeypatch, sesion, permisos):
    raiz, rt = sesion
    pedidas, respuesta = permisos
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: (None, False))
    bins = raiz / 'node_modules' / '.bin'
    lanzador(bins, 'typescript-language-server', SIMULADO)
    monkeypatch.setenv('PATH', str(bins) + ':' + '/usr/bin:/bin')
    respuesta['si'] = False
    resultado = editar(rt, 'a.ts', 'const a = ERROR\n')
    assert len(pedidas) == 1 and pedidas[0]['motivo'] == 'modo' and 'del proyecto' in pedidas[0]['purpose']
    assert 'no se dio permiso' in resultado['diagnosticos']['sin_respuesta'][0]


SWIFT = shutil.which('swift') if sys.platform == 'darwin' else None


# --- tercera revisión: mayúsculas, dependencias sin paréntesis, confianza en el paquete ---------------


def _disco_sin_mayusculas(carpeta):
    prueba = carpeta / 'Mayusculas'
    prueba.mkdir(exist_ok=True)
    return (carpeta / 'mayusculas').exists()


def test_los_manifiestos_se_buscan_sin_distinguir_mayusculas(tmp_path):
    (tmp_path / 'package.swift').write_text('// a\n')
    (tmp_path / 'PACKAGE@swift-6.0.SWIFT').write_text('// b\n')
    (tmp_path / 'otro.swift').write_text('// c\n')
    assert [p.name for p in modulo.manifiestos(tmp_path)] == ['PACKAGE@swift-6.0.SWIFT', 'package.swift']
    assert modulo.es_paquete(tmp_path)
    huella = modulo.huella_de_manifiestos(tmp_path)
    (tmp_path / 'package.swift').write_text('// otro\n')
    assert modulo.huella_de_manifiestos(tmp_path) != huella, 'un cambio en package.swift se nota'


def test_dentro_de_no_distingue_mayusculas_si_el_disco_no_lo_hace(tmp_path):
    if not _disco_sin_mayusculas(tmp_path):
        pytest.skip('este disco distingue mayúsculas')
    raiz = tmp_path / 'proyecto'
    bins = raiz / 'node_modules' / '.bin'
    bins.mkdir(parents=True)
    (bins / 'eslint').write_text('#!/bin/sh\n')
    (bins / 'eslint').chmod(0o755)
    otra_forma = tmp_path / 'PROYECTO' / 'node_modules' / '.bin'
    assert modulo.dentro_de(str(otra_forma / 'eslint'), raiz)
    assert linters._programa('eslint', {'PATH': str(otra_forma)}, raiz=raiz)[1] is True
    assert not modulo.dentro_de(str(tmp_path / 'otro'), raiz)




# --- cuarta revisión: sourcekit-lsp nunca evalúa Package.swift ---------------------------------------


def test_las_opciones_son_fijas(tmp_path):
    """Lo único que llega al compilador de sourcekit-lsp: nada sale del manifiesto."""
    h = {'swiftc': '/usr/bin/swiftc', 'sdk': '/SDK', 'triple': 'arm64-apple-macosx26.0', 'plataforma': '/Plat'}
    (tmp_path / 'Sources' / 'A').mkdir(parents=True)
    (tmp_path / 'Sources' / 'A' / 'a.swift').write_text('')
    objetivos_ = paquete.objetivos({'targets': [{'name': 'A', 'c99name': 'A', 'module_type': 'SwiftTarget',
                                                 'type': 'library', 'path': 'Sources/A', 'sources': ['a.swift']}]},
                                   tmp_path)
    cubiertos = paquete.escribir_base(tmp_path / 'db', objetivos_, h, '5', tmp_path / 'mod')
    (fila,) = json.loads((tmp_path / 'db' / 'compile_commands.json').read_text())
    opciones = [a for a in fila['arguments'][1:] if a.startswith('-')]
    assert set(opciones) <= {'-module-name', '-sdk', '-target', '-swift-version', '-I', '-F', '-parse-as-library'}
    assert cubiertos == {str((tmp_path / 'Sources' / 'A' / 'a.swift').resolve())}


def test_los_objetivos_se_comprueban(tmp_path):
    raiz = tmp_path / 'paquete'
    (raiz / 'Sources' / 'A').mkdir(parents=True)
    (raiz / 'Sources' / 'A' / 'a.swift').write_text('')
    fuera = tmp_path / 'fuera.swift'
    fuera.write_text('')
    (raiz / 'Sources' / 'A' / 'enlace.swift').symlink_to(fuera)
    def objetivo(**cambios):
        return dict({'name': 'A', 'c99name': 'A', 'module_type': 'SwiftTarget', 'type': 'library',
                     'path': 'Sources/A', 'sources': ['a.swift']}, **cambios)
    def nombres(*objetivos_):
        return [o['nombre'] for o in paquete.objetivos({'targets': list(objetivos_)}, raiz)]
    assert nombres(objetivo()) == ['A']
    assert nombres(objetivo(path='../..')) == [], 'una carpeta fuera del paquete'
    assert nombres(objetivo(sources=['a.swift', 'enlace.swift'])) == [], 'una fuente que sale por un enlace'
    assert nombres(objetivo(sources=['../../fuera.swift'])) == []
    assert nombres(objetivo(c99name='A; rm -rf')) == [], 'un nombre de módulo raro'
    assert nombres(objetivo(product_dependencies=['Otro'])) == [], 'depende de otro paquete'
    assert nombres(objetivo(module_type='ClangTarget')) == []
    con_dependencia = paquete.objetivos({'targets': [objetivo(), objetivo(name='B', c99name='B',
                                                                        target_dependencies=['A', 'Z'])]}, raiz)
    assert con_dependencia[1]['dependencias'] == ['A']


def test_sourcekit_en_modo_base_de_datos(sourcekit, simulados, sesion, permisos):
    raiz, rt = sesion
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'Package.swift').write_text('// swift-tools-version:5.9\n')

    async def ediciones():
        primero = await escribir(rt, 'Sources/Demo/a.swift', 'let a = ERROR\n')
        (cliente,) = rt.manager.diagnosticos.servidores.values()
        manifiesto = await escribir(rt, 'Package.swift', '// swift-tools-version:5.9\n// cambiado\n')
        await rt.manager.cerrar_diagnosticos()
        return primero, cliente, manifiesto
    primero, cliente, manifiesto = asyncio.run(ediciones())
    assert primero['diagnosticos']['errores'] == 1
    assert cliente.argv[1:] == ['--default-workspace-type', 'compilationDatabase']
    assert cliente.opciones['defaultWorkspaceType'] == 'compilationDatabase' and 'swiftPM' not in cliente.opciones
    assert cliente.envolver is None   # sourcekitd es XPC: fuera de Seatbelt, con el permiso de salir
    cache = modulo.carpeta_de_sourcekit(raiz.resolve())
    assert Path(cliente.raiz) == cache / 'db' and (cache / 'db' / 'compile_commands.json').is_file()
    assert cliente.env['TMPDIR'].startswith(str(cache / 'tmp'))
    assert cliente.env['CLANG_MODULE_CACHE_PATH'] == str(cache.parent / 'modulos-del-sdk')
    # Package.swift nunca se le manda: no está en la base y le haría abrir el paquete.
    abiertos = [linea for linea in sourcekit.read_text().split() if linea == 'textDocument/didOpen']
    assert len(abiertos) == 1
    assert 'Package.swift no está en ningún objetivo' in manifiesto['diagnosticos']['sin_respuesta'][0]
    assert 'No lee Package.swift' in permisos[0][0]['purpose']


def test_un_cambio_del_paquete_prepara_de_nuevo_y_reinicia(sourcekit, simulados, sesion, permisos):
    raiz, rt = sesion
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'Package.swift').write_text('// swift-tools-version:5.9\n')

    async def ediciones():
        await escribir(rt, 'Sources/Demo/a.swift', 'let a = 1\n')
        await escribir(rt, 'Sources/Demo/a.swift', 'let a = 2\n')
        (viejo,) = rt.manager.diagnosticos.servidores.values()
        (raiz / 'Package.swift').write_text('// swift-tools-version:5.9\n// otro\n')
        nuevo_archivo = await escribir(rt, 'Sources/Demo/b.swift', 'let b = ERROR\n')
        (nuevo,) = rt.manager.diagnosticos.servidores.values()
        await rt.manager.cerrar_diagnosticos()
        return viejo, nuevo, nuevo_archivo
    viejo, nuevo, nuevo_archivo = asyncio.run(ediciones())
    assert len(simulados['descritos']) == 2, 'una vez por versión del manifiesto, no por edición'
    assert viejo is not nuevo and viejo.proc.returncode is not None
    assert nuevo_archivo['diagnosticos']['errores'] == 1, 'el archivo nuevo entra en la base'


def test_sin_ajustes_seguros_no_hay_diagnosticos_de_swift(sourcekit, simulados, sesion, permisos, monkeypatch):
    raiz, rt = sesion
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'Package.swift').write_text('// swift-tools-version:5.9\n')

    async def falla(paquete_, sandbox, env):
        raise paquete.PaqueteNoPreparable('código 1: error: el manifiesto no compila')
    monkeypatch.setattr(paquete, 'describir', falla)
    resultado = editar(rt, 'Sources/Demo/a.swift', 'let a = ERROR\n')
    assert 'el manifiesto no compila' in resultado['diagnosticos']['sin_respuesta'][0]
    assert not sourcekit.exists(), 'nunca se cae al modo SwiftPM'


@pytest.mark.skipif(SOURCEKIT is None or SWIFT is None, reason='sourcekit-lsp o swift no están instalados')
def test_preparacion_de_verdad_con_varios_objetivos(sesion, permisos, monkeypatch):
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS_ESPERA', '30')
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: (None, False))
    raiz, rt = sesion
    for carpeta in ('Sources/Base', 'Sources/App', 'Tests/AppTests'):
        (raiz / carpeta).mkdir(parents=True)
    (raiz / 'Package.swift').write_text(
        '// swift-tools-version:5.9\nimport PackageDescription\nlet package = Package(name: "Multi", targets: ['
        '.target(name: "Base"), .executableTarget(name: "App", dependencies: ["Base"]), '
        '.testTarget(name: "AppTests", dependencies: ["Base"])])\n')
    (raiz / 'Sources/Base/Punto.swift').write_text('public struct Punto { public let x: Int\n'
                                                   '  public init(x: Int) { self.x = x } }\n')
    (raiz / 'Tests/AppTests/T.swift').write_text('import XCTest\nimport Base\nfinal class T: XCTestCase {\n'
                                                 '  func testA() { XCTAssertEqual(Punto(x: 1).x, 1) } }\n')
    app, pruebas = asyncio.run(en_un_bucle(rt, [
        ('Sources/App/main.swift', 'import Base\nlet p = Punto(x: "uno")\nprint(p.x)\n'),
        ('Tests/AppTests/T.swift', 'import XCTest\nimport Base\nfinal class T: XCTestCase {\n'
                                   '  func testA() { XCTAssertEqual(Punto(x: 1).y, 1) } }\n'),
    ]))
    assert app['diagnosticos']['nuevos'] == ["Sources/App/main.swift:2:18 error [SourceKit] Cannot convert value of "
                                             "type 'String' to expected argument type 'Int'"], app
    assert pruebas['diagnosticos']['errores'] == 1 and "no member 'y'" in pruebas['diagnosticos']['nuevos'][0]
    assert not (raiz / '.build').exists() and not (raiz / '.swiftpm').exists()


MANIFIESTOS_MALICIOSOS = Path(__file__).with_name('fixtures') / 'manifiestos_maliciosos'


@pytest.mark.skipif(SOURCEKIT is None or SWIFT is None, reason='sourcekit-lsp o swift no están instalados')
@pytest.mark.parametrize('manifiesto', ['dlsym', 'silgen'])
@pytest.mark.parametrize('modo', ['auto', 'manual', 'ediciones', 'omitir'])
def test_los_manifiestos_del_verificador_no_ejecutan_nada(sesion, permisos, monkeypatch, tmp_path, manifiesto, modo):
    """Los dos manifiestos de la cuarta revisión: piden `-load-plugin-executable <repo>/herr/evil` sin escribir
    unsafeFlags (dlsym, @_silgen_name) y sólo fuera del temporal de AlfalfaCode. sourcekit-lsp no evalúa el
    manifiesto, así que evil no se ejecuta en ningún modo; los diagnósticos siguen llegando."""
    monkeypatch.setenv('ALFALFA_DIAGNOSTICOS', '1')
    monkeypatch.setattr(linters, '_programa', lambda *a, **k: (None, False))
    raiz, rt = sesion
    rt.task['modo'] = modo
    pedidas, _ = permisos
    if modo == 'omitir':
        del rt.approve   # la aprobación de verdad: Omitir permisos concede sin preguntar a nadie
    marca = tmp_path / 'marca-de-evil'
    (raiz / 'herr').mkdir()
    (raiz / 'herr' / 'evil').write_text('#!/bin/sh\ntouch ' + str(marca) + '\nexit 1\n')
    (raiz / 'herr' / 'evil').chmod(0o755)
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'package.swift').write_text((MANIFIESTOS_MALICIOSOS / (manifiesto + '.swift')).read_text()
                                        .replace('EVIL', str(raiz / 'herr' / 'evil')))
    cache = Path.home() / 'Library' / 'Caches'
    antes = set(os.listdir(cache)) if cache.is_dir() else set()

    async def ediciones():
        resultado = await hasta_que_conteste(rt, 'Sources/Demo/a.swift',
                                             '@freestanding(expression) public macro evil() -> Int = '
                                             '#externalMacro(module: "Evil", type: "Evil")\n'
                                             'public let x: String = #evil()\n')
        await rt.manager.cerrar_diagnosticos()
        return resultado
    resultado = asyncio.run(ediciones())
    time.sleep(0.5)
    assert not marca.exists(), 'evil se ejecutó'
    assert any(c.startswith('sourcekit-lsp') for c in resultado['diagnosticos'].get('revisado_con', [])), resultado
    assert not (raiz / '.build').exists() and not (raiz / '.swiftpm').exists()
    nuevas = (set(os.listdir(cache)) if cache.is_dir() else set()) - antes
    assert not [n for n in nuevas if 'alfalfa' in n.lower() or 'evil' in n.lower() or 'marca' in n.lower()]
    de_sourcekit = [p for p in pedidas if p['motivo'] != 'edicion']   # Manual también pregunta cada edición
    assert len(de_sourcekit) == (0 if modo == 'omitir' else 1)


@pytest.mark.skipif(SOURCEKIT is None or SWIFT is None, reason='sourcekit-lsp o swift no están instalados')
@pytest.mark.parametrize('manifiesto', ['dlsym', 'silgen'])
def test_control_los_manifiestos_atacan_si_sourcekit_los_evaluara(tmp_path, manifiesto):
    """Control de la prueba anterior: con el diseño viejo (sourcekit-lsp en modo SwiftPM, que evalúa el
    manifiesto) y un TMPDIR como el de producción (…/Library/Caches/alfalfa/sourcekit/…/tmp), evil SÍ se
    ejecuta. Si un día deja de hacerlo (otra cadena de Swift, otro símbolo), la prueba anterior ya no
    demostraría nada y hay que rehacer los manifiestos. Sólo escribe su marca en un temporal."""
    raiz = tmp_path / 'paquete'
    (raiz / 'Sources' / 'Demo').mkdir(parents=True)
    (raiz / 'herr').mkdir()
    marca = tmp_path / 'marca-de-evil'
    (raiz / 'herr' / 'evil').write_text('#!/bin/sh\ntouch ' + str(marca) + '\nexit 1\n')
    (raiz / 'herr' / 'evil').chmod(0o755)
    (raiz / 'Package.swift').write_text((MANIFIESTOS_MALICIOSOS / (manifiesto + '.swift')).read_text()
                                        .replace('EVIL', str(raiz / 'herr' / 'evil')))
    fuente = raiz / 'Sources' / 'Demo' / 'a.swift'
    texto = ('@freestanding(expression) public macro evil() -> Int = #externalMacro(module: "Evil", type: "Evil")\n'
             'public let x: Int = #evil()\n')
    fuente.write_text(texto)
    cache = tmp_path / 'Library' / 'Caches' / 'alfalfa' / 'sourcekit' / 'control'
    (cache / 'tmp').mkdir(parents=True)
    entorno = dict(os.environ, TMPDIR=str(cache / 'tmp') + os.sep, CLANG_MODULE_CACHE_PATH=str(cache / 'modulos'),
                   SWIFTPM_MODULECACHE_OVERRIDE=str(cache / 'modulos'))
    assert 'alfalfa' in entorno['TMPDIR'] and 'alfalfa-paquete' not in entorno['TMPDIR']

    async def con_el_diseno_viejo():
        cliente = lsp.ClienteLSP('sourcekit-lsp', [SOURCEKIT], raiz, env=entorno, idioma_de=lambda r: 'swift',
                                 opciones={'backgroundIndexing': False,
                                           'swiftPM': {'scratchPath': str(cache / 'compilacion')}}, carga=True)
        try:
            await cliente.arrancar()
            await cliente.abrir(fuente, texto)
            fin = asyncio.get_running_loop().time() + 90
            while not marca.exists() and asyncio.get_running_loop().time() < fin:
                with contextlib.suppress(asyncio.TimeoutError, lsp.ServidorCaido):
                    await cliente.diagnosticos(fuente, asyncio.get_running_loop().time() + 10)
                await asyncio.sleep(0.5)
        finally:
            await cliente.cerrar()
    asyncio.run(con_el_diseno_viejo())
    assert marca.exists(), 'con el diseño viejo evil debería ejecutarse: el manifiesto de prueba ya no ataca'
