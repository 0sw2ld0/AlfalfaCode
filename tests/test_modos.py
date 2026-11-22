"""Modos de permisos (Auto, Manual, Aceptar ediciones, Plan, Turbo, Omitir permisos), con sandbox real."""
import asyncio
import io
import json
import os
import sys

import pytest

from alfalfa.seatbelt import SANDBOX_EXEC, nombre_del_sandbox
from alfalfa.stdio import Transport
from alfalfa.terminal import ESCRITURA, TerminalManager
from tests.sandbox_del_sistema import motivo_sin_sandbox

con_sandbox = pytest.mark.skipif(motivo_sin_sandbox() is not None, reason=str(motivo_sin_sandbox()))

real = pytest.mark.skipif(not (sys.platform == 'darwin' and os.access(SANDBOX_EXEC, os.X_OK)),
                          reason='requiere /usr/bin/sandbox-exec (macOS)')


class Recorder:
    def __init__(self, answer='yes'):
        self.answer, self.calls, self.denied = answer, [], 0

    async def __call__(self, args):
        self.calls.append(dict(args))
        if self.answer == 'yes':
            return
        self.denied += 1
        raise ValueError('Comando rechazado; no se ejecutó.')


@pytest.fixture
def sesion(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'nota.txt').write_text('hola\n')
    monkeypatch.chdir(root)
    m = TerminalManager('Prueba de modos.', home=tmp_path / 'alfalfa')

    def call(modo, recorder, tool, **arguments):
        m.task['modo'] = modo
        rt = m.runtime()
        rt.approval_policy = recorder
        return asyncio.run(rt.tools.call(tool, arguments))

    try:
        yield m, root, call
    finally:
        m.close()


def test_modo_por_defecto_y_desconocido(tmp_path, monkeypatch):
    (tmp_path / 'p').mkdir()
    monkeypatch.chdir(tmp_path / 'p')
    m = TerminalManager('x', home=tmp_path / 'a')
    try:
        assert m.task['modo'] == 'auto'
    finally:
        m.close()
    with pytest.raises(ValueError, match='Modo desconocido'):
        TerminalManager('x', home=tmp_path / 'b', mode='libre')


@real
@pytest.mark.parametrize('modo, pregunta', [('auto', False), ('manual', True), ('ediciones', True)])
def test_ordenes_que_escriben_segun_el_modo(sesion, modo, pregunta):
    m, root, call = sesion
    recorder = Recorder()
    result = call(modo, recorder, 'run_command', argv=['touch', 'nuevo.txt'], purpose='Crear un archivo')
    assert result['exit_code'] == 0
    assert bool(recorder.calls) is pregunta
    if pregunta:
        assert recorder.calls[0]['motivo'] == 'modo'


@con_sandbox
def test_turbo_borra_archivo_del_proyecto_sin_preguntar(sesion):
    _, root, call = sesion
    recorder = Recorder('no')
    archivo = root / 'nota.txt'
    orden = {'argv': ['/bin/rm', '-f', archivo.name], 'purpose': 'Limpiar un archivo de prueba'}
    with pytest.raises(ValueError, match='rechazado'):
        call('auto', recorder, 'run_command', **orden)
    assert archivo.exists() and recorder.calls[0]['motivo'] == 'peligroso'

    recorder.calls.clear()
    result = call('turbo', recorder, 'run_command', **orden)
    assert result['exit_code'] == 0 and result['sandbox'] == nombre_del_sandbox().lower()
    assert not archivo.exists() and recorder.calls == []


@con_sandbox
@pytest.mark.parametrize('opciones, motivo', [
    ({'needs_network': True}, 'red'),
    ({'sandbox_permissions': 'require_escalated', 'justification': 'Prueba de salida del sandbox'}, 'salir_sandbox'),
])
def test_turbo_conserva_permisos_de_red_y_salida(sesion, opciones, motivo):
    _, root, call = sesion
    recorder = Recorder('no')
    with pytest.raises(ValueError, match='rechazado'):
        call('turbo', recorder, 'run_command', argv=['/bin/rm', '-f', 'nota.txt'],
             purpose='Limpiar un archivo de prueba', **opciones)
    assert (root / 'nota.txt').exists()
    pedido, = recorder.calls
    assert pedido['motivo'] == motivo


@con_sandbox
@pytest.mark.parametrize('ruta', ['ruta absoluta', 'enlace intermedio'])
def test_turbo_conserva_confirmacion_de_borrado_fuera_del_proyecto(sesion, tmp_path, ruta):
    _, root, call = sesion
    fuera = tmp_path / 'fuera'
    fuera.mkdir()
    protegido = fuera / 'nota.txt'
    protegido.write_text('conservar')
    if ruta == 'enlace intermedio':
        (root / 'enlace').symlink_to(fuera, target_is_directory=True)
    recorder = Recorder('no')
    with pytest.raises(ValueError, match='rechazado'):
        call('turbo', recorder, 'run_command',
             argv=['/bin/rm', '-f', str(protegido) if ruta == 'ruta absoluta' else 'enlace/nota.txt'],
             purpose='Probar un borrado fuera del proyecto')
    assert protegido.read_text() == 'conservar'
    pedido, = recorder.calls
    assert pedido['motivo'] == 'peligroso'


@con_sandbox
def test_turbo_conserva_reintento_de_escritura_fuera_del_sandbox(sesion, fuera_del_sandbox):
    _, _, call = sesion
    protegido = fuera_del_sandbox / 'nuevo.txt'
    recorder = Recorder('no')
    result = call('turbo', recorder, 'run_command', argv=['/usr/bin/touch', str(protegido)],
                  purpose='Probar una escritura fuera del sandbox')
    assert result['exit_code'] != 0 and result['sandbox'] == nombre_del_sandbox().lower()
    assert not protegido.exists()
    pedido, = recorder.calls
    assert pedido['motivo'] == 'reintento' and pedido['sin_sandbox']


@real
@pytest.mark.parametrize('modo', ['manual', 'ediciones'])
def test_las_que_solo_leen_no_preguntan(sesion, modo):
    m, root, call = sesion
    recorder = Recorder()
    result = call(modo, recorder, 'run_command', argv=['cat', 'nota.txt'], purpose='Leer la nota')
    assert result['exit_code'] == 0 and recorder.calls == []


@pytest.mark.parametrize('modo, pregunta', [('auto', False), ('ediciones', False), ('manual', True)])
def test_ediciones_segun_el_modo(sesion, modo, pregunta):
    m, root, call = sesion
    recorder = Recorder()
    call(modo, recorder, 'apply_patch',
         input='*** Begin Patch\n*** Update File: nota.txt\n@@\n-hola\n+adiós\n*** End Patch')
    assert (root / 'nota.txt').read_text() == 'adiós\n'
    assert bool(recorder.calls) is pregunta
    if pregunta:
        assert recorder.calls[0]['motivo'] == 'edicion' and recorder.calls[0]['argv'] == ['apply_patch', 'nota.txt']


def test_manual_negado_no_edita(sesion):
    m, root, call = sesion
    with pytest.raises(ValueError, match='rechazado'):
        call('manual', Recorder('no'), 'write_file', path='otra.txt', content='x', expected_sha256='NEW')
    assert not (root / 'otra.txt').exists()


def test_plan_solo_lee_y_lo_dice(sesion):
    m, root, call = sesion
    m.task['modo'] = 'plan'
    rt = m.runtime()
    nombres = {t['function']['name'] for t in rt.tool_specs if t['function']['name'] not in rt.excluded_tools}
    assert not nombres & set(ESCRITURA) and 'read_file' in nombres
    assert 'Modo plan' in rt.manual(rt.task.get('model'))
    with pytest.raises(ValueError):
        call('plan', Recorder(), 'write_file', path='x.txt', content='x', expected_sha256='NEW')


def test_omitir_aprueba_y_lo_anota(sesion):
    m, root, call = sesion
    interior = Recorder('no')
    m.approval.interior = interior
    m.task['modo'] = 'omitir'
    asyncio.run(m.approval({'argv': ['rm', '-rf', 'algo'], 'purpose': 'x'}))
    assert interior.calls == []
    eventos = [json.loads(linea)['text']
               for linea in (m.directory / (m.task['id'] + '.jsonl')).read_text().splitlines()]
    assert any('Omitir permisos' in e and 'rm -rf algo' in e for e in eventos)
    m.task['modo'] = 'auto'
    with pytest.raises(ValueError):
        asyncio.run(m.approval({'argv': ['rm', '-rf', 'algo'], 'purpose': 'x'}))
    assert len(interior.calls) == 1


def test_ajustes_por_stdio_aceptan_el_modo():
    out = io.StringIO()
    t = Transport(out)
    t.receive('{"tipo":"ajustes","modo":"plan"}')
    t.receive('{"tipo":"ajustes","modo":"libre"}')
    assert t.take_settings() == {'modo': 'plan'}
    assert 'inválido' in out.getvalue()
