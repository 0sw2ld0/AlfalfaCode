"""La app enseña qué hizo el agente con cada llamada: `description` de las órdenes y
una descripción generada para lo demás viajan en `herramienta` de los eventos JSONL."""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

from alfalfa.proveedores.nativo.descripciones import datos_de_fin, datos_de_inicio, descripcion
from alfalfa.proveedores.nativo.herramientas import TOOLS, WorkspaceTools, _on_request


def _spec(name, tools=TOOLS):
    return next(t['function'] for t in tools if t['function']['name'] == name)


def test_las_ordenes_piden_description_sin_hacerla_obligatoria():
    for name in ('run_command', 'run_checks'):
        for tools in (TOOLS, [_on_request(t) for t in TOOLS]):
            function = _spec(name, tools)
            props = function['parameters']['properties']
            assert props['description']['type'] == 'string'
            assert 'Always send it' in props['description']['description']
            assert 'description' not in function['parameters']['required']
            assert 'Always add description' in function['description']
    # El validador la acepta y una orden sin ella sigue siendo válida.
    args = WorkspaceTools.validate('run_command', {'argv': ['git', 'status'], 'purpose': 'Estado',
                                                   'description': 'Ver qué archivos cambiaron'})
    assert args['description'] == 'Ver qué archivos cambiaron'
    assert 'description' not in WorkspaceTools.validate('run_command', {'argv': ['ls'], 'purpose': 'Listar'})


def test_descripcion_de_cada_herramienta():
    assert descripcion('run_command', {'argv': ['rg', 'x'], 'purpose': 'p', 'description': '  Buscar  dónde  '}) \
        == 'Buscar dónde'
    # Sin description, el propósito; sin propósito, el comando.
    assert descripcion('run_command', {'argv': ['rg', 'x'], 'purpose': 'Buscar x'}) == 'Buscar x'
    assert descripcion('run_command', {'argv': ['git', 'log', '-1 x']}) == "Ejecutar git log '-1 x'"
    assert descripcion('run_checks', {'argv': ['pytest']}) == 'Comprobar con pytest'
    assert descripcion('read_file', {'path': 'alfalfa/proveedores/nativo/runtime.py'}) == 'Leer runtime.py'
    assert descripcion('list_files', {}) == 'Ver los archivos del proyecto'
    assert descripcion('search', {'query': 'session.json', 'path': 'alfalfa'}) == 'Buscar «session.json» en alfalfa'
    assert descripcion('write_file', {'path': 'a/nota.txt', 'expected_sha256': 'NEW'}) == 'Crear nota.txt'
    assert descripcion('edit_file', {'path': 'a/b.py'}) == 'Editar b.py'
    parche = '*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** Add File: b.py\n+z\n*** End Patch'
    assert descripcion('apply_patch', {'input': parche}) == 'Editar 2 archivos'
    assert descripcion('mcp__github__create_issue', {}) == 'Usar github · create_issue'
    assert descripcion('herramienta_rara', None) == 'Usar herramienta_rara'
    assert len(descripcion('run_command', {'argv': ['x'], 'description': 'a' * 500})) == 200


def test_datos_del_evento():
    inicio = datos_de_inicio('run_command', {'argv': ['git', 'status'], 'purpose': 'p', 'cwd': 'srv',
                                             'description': 'Ver el estado'})
    assert inicio == {'nombre': 'run_command', 'fase': 'inicio', 'descripcion': 'Ver el estado',
                      'orden': 'git status', 'cwd': 'srv'}
    assert datos_de_inicio('read_file', {'path': 'x.py'}) == {'nombre': 'read_file', 'fase': 'inicio',
                                                               'descripcion': 'Leer x.py', 'objetivo': 'x.py'}
    assert datos_de_fin('run_command', {'exit_code': 2, 'output': ''}) == {'nombre': 'run_command', 'fase': 'fin',
                                                                           'codigo': 2}
    assert datos_de_fin('read_file', {'error': 'no existe'}) == {'nombre': 'read_file', 'fase': 'fin', 'error': True}


SESION = '''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    calls = []
    if not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='read_file', arguments=dict(path='nota.txt'))),
                 dict(function=dict(name='run_command', arguments=dict(
                     argv=[sys.executable, '-c', 'print(40 + 2)'], purpose='Sumar',
                     description='Comprobar que Python suma bien')))]
    return dict(message=dict(role='assistant', content='' if calls else 'Hecho.', tool_calls=calls),
                done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(sys.argv[1:]))
'''


def _lanzar(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), ALFALFA_HOME=str(tmp_path / 'home'))
    return subprocess.Popen([sys.executable, '-c', SESION, *args], cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_description_viaja_en_los_eventos_y_en_el_historial(tmp_path):
    (tmp_path / 'nota.txt').write_text('hola\n')
    proc = _lanzar(tmp_path, '--stdio', 'Lee la nota y suma')
    filas = []
    try:
        while True:
            assert select.select([proc.stdout], [], [], 20)[0], 'stdout timeout'
            fila = json.loads(proc.stdout.readline())
            filas.append(fila)
            if fila['tipo'] == 'permiso':
                proc.stdin.write(json.dumps(dict(tipo='permiso', id=fila['id'], aprobado=True)) + '\n')
                proc.stdin.flush()
            if fila['tipo'] == 'turno_terminado':
                break
        proc.stdin.close()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    herramientas = [f['herramienta'] for f in filas if f['tipo'] == 'evento' and f['rol'] == 'tool']
    assert herramientas[0] == {'nombre': 'read_file', 'fase': 'inicio', 'descripcion': 'Leer nota.txt',
                               'objetivo': 'nota.txt'}
    assert herramientas[1] == {'nombre': 'read_file', 'fase': 'fin'}
    assert herramientas[2] == {'nombre': 'run_command', 'fase': 'inicio',
                               'descripcion': 'Comprobar que Python suma bien',
                               'orden': sys.executable + " -c 'print(40 + 2)'"}
    assert herramientas[3] == {'nombre': 'run_command', 'fase': 'fin', 'codigo': 0,
                               'clasificacion': {'disposition': 'pass', 'status': 'passed'}}
    # El texto de siempre sigue igual para las apps anteriores.
    textos = [f['texto'] for f in filas if f['tipo'] == 'evento' and f['rol'] == 'tool']
    assert textos[2].startswith('Inicio run_command · ') and textos[3].startswith('run_command\n')

    sesion = next(f['id'] for f in filas if f['tipo'] == 'sesion')
    historial = subprocess.run([sys.executable, '-c', SESION, '--json', '--historial', sesion], cwd=tmp_path,
                               env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                                        ALFALFA_HOME=str(tmp_path / 'home')),
                               capture_output=True, text=True, timeout=30)
    fila = json.loads(historial.stdout.splitlines()[-1])
    guardadas = [e['herramienta'] for e in fila['eventos'] if e['rol'] == 'tool']
    assert guardadas == herramientas


def test_description_que_no_es_texto_no_tumba_la_orden():
    for valor in (None, 7, ['a'], {'x': 1}, True):
        args = WorkspaceTools.validate('run_command', {'argv': ['ls'], 'purpose': 'Listar', 'description': valor})
        assert args == {'argv': ['ls'], 'purpose': 'Listar'}
        assert descripcion('run_command', {'argv': ['ls'], 'purpose': 'Listar', 'description': valor}) == 'Listar'


def test_el_parche_dice_sus_archivos():
    parche = ('*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** Add File: b/c.py\n+z\n'
              '*** Update File: a.py\n@@\n-y\n+w\n*** End Patch')
    assert datos_de_inicio('apply_patch', {'input': parche})['objetivos'] == ['a.py', 'b/c.py']
    assert 'objetivos' not in datos_de_inicio('apply_patch', {'input': 'basura'})


def test_la_clasificacion_viaja_en_el_fin():
    # rg sin coincidencias que el propósito esperaba: código 1, pero no es un fallo.
    resultado = {'exit_code': 1, 'output': '', 'classification': {
        'status': 'expected_absence', 'disposition': 'pass', 'cause': 'rg_no_matches_expected', 'message': 'x'}}
    assert datos_de_fin('run_command', resultado) == {
        'nombre': 'run_command', 'fase': 'fin', 'codigo': 1,
        'clasificacion': {'disposition': 'pass', 'status': 'expected_absence'}}
    assert 'clasificacion' not in datos_de_fin('run_command', {'exit_code': 0, 'classification': {'x': 1}})


def test_herramienta_pasa_por_la_limpieza_de_secretos(tmp_path):
    import io

    from alfalfa.stdio import Transport
    from alfalfa.terminal import TerminalManager
    salida = io.StringIO()
    transporte = Transport(salida)
    transporte.secretos.add('sk-muy-secreto-123')
    gestor = TerminalManager.__new__(TerminalManager)
    gestor.transport, gestor.directory, gestor.mcp = transporte, tmp_path, None
    tarea = {'id': 't1'}
    args = {'argv': ['curl', '-H', 'Authorization: Bearer sk-muy-secreto-123', 'https://x'], 'purpose': 'p',
            'description': 'Probar la clave sk-muy-secreto-123'}
    gestor.tool_event(tarea, 'Inicio run_command · ' + str(args), datos_de_inicio('run_command', args))
    emitido = json.loads(salida.getvalue())
    guardado = json.loads((tmp_path / 't1.jsonl').read_text())
    for fila in (emitido['herramienta'], guardado['herramienta'], emitido['texto'], guardado['text']):
        assert 'sk-muy-secreto-123' not in json.dumps(fila)
    assert '[REDACTADO]' in emitido['herramienta']['orden'] and '[REDACTADO]' in emitido['herramienta']['descripcion']
