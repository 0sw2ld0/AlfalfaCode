"""Colas del usuario con sesiones y proyectos temporales; nunca proveedores reales."""
import asyncio
import io
import json
import subprocess
from pathlib import Path

import pytest

from alfalfa.encargos import ColaEncargos, leer_archivo, validar
from alfalfa.stdio import Transport, history
from alfalfa.terminal import TerminalManager


def git(raiz, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Prueba', '-c',
        'user.email=prueba@users.noreply.github.com', '-C', str(raiz), *args], text=True).strip()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    root = tmp_path / 'proyecto'
    root.mkdir()
    (root / 'base.txt').write_text('base\n')
    monkeypatch.chdir(root)
    manager = TerminalManager('Cola de encargos', home=tmp_path / 'alfalfa', model='simulado',
                              transport=Transport(io.StringIO()), diagnosticos=False)
    yield manager, root
    manager.close()


@pytest.fixture
def ejecutor(monkeypatch):
    vistos = []
    control = {'bloqueado': None, 'esperar': None, 'activos': 0, 'maximos': 0}

    async def trabajo(m, texto, eco=False):
        vistos.append((m, texto))
        control['activos'] += 1
        control['maximos'] = max(control['maximos'], control['activos'])
        try:
            if control['esperar']:
                await control['esperar'].wait()
            await asyncio.sleep(.01)
            root = Path(m.task['cwd'])
            if m.task['objective'] == 'primero':
                (root / 'primero.txt').write_text('primer resultado')
            elif m.task['objective'] == 'segundo':
                assert (root / 'primero.txt').read_text() == 'primer resultado'
            (root / (m.task['id'] + '.txt')).write_text(m.task['objective'])
            m.task.update(status='blocked' if control['bloqueado'] == m.task['objective'] else 'completed',
                          cierre={'resumen': 'Informe de ' + m.task['objective']})
            m.task['total_tokens'] = (m.task.get('total_tokens') or 0) + 100
            m.task['coste_usd'] = (m.task.get('coste_usd') or 0) + .01
            m.presupuesto.sumar(m.task, .01)
            return 0 if m.task['status'] == 'completed' else 1
        finally:
            control['activos'] -= 1
    monkeypatch.setattr(TerminalManager, '_run', trabajo)
    return vistos, control


async def hasta_empezar(cola, cuantos=1):
    for _ in range(300):
        if sum(f.get('sesion') is not None for f in cola.datos['encargos']) >= cuantos:
            return
        await asyncio.sleep(.01)
    raise AssertionError('La cola no arrancó')


async def test_serie_sesiones_separadas_cambios_acumulados_y_resumen(demo, ejecutor):
    m, root = demo
    cola = m.cola_encargos
    cola.preparar(['primero', 'segundo'])
    assert not ejecutor[0]  # Preparar no pide inferencia.
    cola.iniciar()
    await cola.worker
    assert cola.datos['estado'] == 'completada'
    assert ejecutor[1]['maximos'] == 1
    assert len({h.task['id'] for h, _ in ejecutor[0]}) == 2
    assert all(h.task['padre'] == m.task['id'] and h.task['cwd'] == str(root) for h, _ in ejecutor[0])
    assert all(h.presupuesto is m.presupuesto for h, _ in ejecutor[0])
    assert m.task['total_tokens'] == 200
    assert m.task['coste_usd'] == pytest.approx(.02)
    assert 'Informe de primero' in cola.datos['resumen'] and 'Informe de segundo' in cola.datos['resumen']
    rt = m.runtime()
    assert 'wait_agent' in rt.state['contenido_externo']
    assert any('Resumen de la cola' in row.get('content', '') for row in rt.state['messages'])
    assert json.loads((m.directory / 'task.json').read_text())['cola_encargos']['estado'] == 'completada'


async def test_paralelo_con_worktrees_aislados_y_limite(demo, ejecutor):
    m, root = demo
    git(root, 'init', '-q')
    git(root, 'add', 'base.txt')
    git(root, 'commit', '-qm', 'Base de prueba')
    punta = git(root, 'rev-parse', 'HEAD')
    cola = m.cola_encargos
    cola.preparar(['A', 'B', 'C'], 'paralelo', 2)
    cola.iniciar()
    await cola.worker
    assert cola.datos['estado'] == 'completada', cola.datos
    assert ejecutor[1]['maximos'] == 2
    carpetas = [Path(f['carpeta']) for f in cola.datos['encargos']]
    assert len(set(carpetas)) == 3
    for fila, carpeta in zip(cola.datos['encargos'], carpetas, strict=True):
        assert carpeta.exists() and (carpeta / 'base.txt').read_text() == 'base\n'
        assert git(carpeta, 'rev-parse', 'HEAD') == punta
        assert git(carpeta, 'symbolic-ref', '--short', 'HEAD') == fila['rama']
        assert len(list(carpeta.glob('*.txt'))) == 2
    assert sorted(p.name for p in root.glob('*.txt')) == ['base.txt']
    assert git(root, 'rev-parse', 'HEAD') == punta


async def test_fallo_pausa_y_reanudar_no_repite_los_terminados(demo, ejecutor):
    m, _ = demo
    ejecutor[1]['bloqueado'] = 'B'
    cola = m.cola_encargos
    cola.preparar(['A', 'B', 'C'])
    cola.iniciar()
    await cola.worker
    assert [f['estado'] for f in cola.datos['encargos']] == ['completed', 'blocked', 'pendiente']
    assert cola.datos['estado'] == 'pausada'
    sesion = cola.datos['encargos'][1]['sesion']
    ejecutor[1]['bloqueado'] = None
    cola.iniciar()
    await cola.worker
    assert [h.task['objective'] for h, _ in ejecutor[0]] == ['A', 'B', 'B', 'C']
    assert cola.datos['encargos'][1]['sesion'] == sesion
    assert 'operaciones inciertas' in ejecutor[0][2][1]
    assert m.task['total_tokens'] == 400


async def test_pausar_deja_acabar_el_activo_y_conserva_pendientes(demo, ejecutor):
    cola = demo[0].cola_encargos
    ejecutor[1]['esperar'] = asyncio.Event()
    cola.preparar(['A', 'B'])
    cola.iniciar()
    await hasta_empezar(cola)
    cola.pausar()
    ejecutor[1]['esperar'].set()
    await cola.worker
    assert [f['estado'] for f in cola.datos['encargos']] == ['completed', 'pendiente']
    assert cola.datos['estado'] == 'pausada'


async def test_detener_drena_los_trabajos_y_reabrir_no_lanza_nada(demo, ejecutor):
    m, _ = demo
    cola = m.cola_encargos
    ejecutor[1]['esperar'] = asyncio.Event()
    cola.preparar(['A', 'B'])
    cola.iniciar()
    await hasta_empezar(cola)
    await cola.detener()
    assert cola.datos['estado'] == 'pausada'
    assert ejecutor[1]['activos'] == 0 and not cola.trabajos
    cola.datos['estado'] = 'ejecutando'
    cola.datos['encargos'][0]['estado'] = 'ejecutando'
    reabierta = ColaEncargos(m)
    assert reabierta.datos['estado'] == 'pausada'
    assert reabierta.datos['encargos'][0]['estado'] == 'interrumpido'
    assert not reabierta.activa and len(ejecutor[0]) == 1


async def test_presupuesto_agotado_no_lanza_encargos(demo, ejecutor):
    m, _ = demo
    m.cambiar_presupuesto(sesion=.01)
    m.task['coste_usd'] = .01
    m.cola_encargos.preparar(['A'])
    m.cola_encargos.iniciar()
    await m.cola_encargos.worker
    assert not ejecutor[0]
    assert m.cola_encargos.datos['estado'] == 'pausada'
    assert 'presupuesto' in m.cola_encargos.datos['motivo']


async def test_paralelo_rechaza_arbol_sucio_sin_empezar(demo, ejecutor):
    m, root = demo
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Base de prueba')
    (root / 'base.txt').write_text('cambio del usuario')
    m.cola_encargos.preparar(['A'], 'paralelo')
    m.cola_encargos.iniciar()
    await m.cola_encargos.worker
    assert m.cola_encargos.datos['estado'] == 'pausada'
    assert not ejecutor[0]
    assert (root / 'base.txt').read_text() == 'cambio del usuario'


async def test_no_ejecuta_hooks_ni_filtros_de_git(demo, ejecutor):
    m, root = demo
    git(root, 'init', '-q')
    (root / '.gitattributes').write_text('*.txt filter=peligroso\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Base de prueba')
    marcador = root.parent / 'NO_DEBE_EXISTIR'
    script = 'touch ' + str(marcador) + '; cat'
    git(root, 'config', 'filter.peligroso.clean', script)
    git(root, 'config', 'filter.peligroso.smudge', script)
    hook = root / '.git' / 'hooks' / 'post-checkout'
    hook.write_text('#!/bin/sh\ntouch ' + str(marcador) + '\n')
    hook.chmod(0o700)
    m.cola_encargos.preparar(['A'], 'paralelo')
    m.cola_encargos.iniciar()
    await m.cola_encargos.worker
    assert m.cola_encargos.datos['estado'] == 'completada', m.cola_encargos.datos
    assert not marcador.exists()


@pytest.mark.parametrize('textos,modo,limite', [([], 'serie', 1), ([''], 'serie', 1),
    (['A'] * 21, 'serie', 1), (['A'], 'inventado', 2), (['A'], 'paralelo', True), (['A'], 'paralelo', 5)])
def test_valida_la_cola_antes_de_guardarla(textos, modo, limite):
    with pytest.raises(ValueError):
        validar(textos, modo, limite)


def test_no_sobrescribe_una_cola_pendiente_y_se_puede_cancelar(demo):
    cola = demo[0].cola_encargos
    cola.preparar(['A'])
    with pytest.raises(ValueError, match='pendiente'):
        cola.preparar(['B'])
    cola.cancelar()
    assert cola.datos['estado'] == 'cancelada'
    cola.preparar(['B'])
    assert cola.datos['encargos'][0]['texto'] == 'B'


def test_archivo_cli_y_el_historial_conservan_la_cola(demo, tmp_path, monkeypatch):
    m, _ = demo
    archivo = tmp_path / 'cola.json'
    archivo.write_text(json.dumps({'encargos': ['A', 'B'], 'modo': 'paralelo', 'paralelos': 2}))
    m.cola_encargos.preparar(*leer_archivo(archivo))
    monkeypatch.setattr('alfalfa.terminal.home_dir', lambda: m.home)
    assert history(m.task['id'])['encargos']['encargos'][0]['texto'] == 'A'


async def test_detener_inmediatamente_despues_de_iniciar_no_deja_la_cola_activa(demo, ejecutor):
    cola = demo[0].cola_encargos
    cola.preparar(['A'])
    cola.iniciar()
    await cola.detener()
    assert cola.datos['estado'] == 'pausada'
    assert not ejecutor[0] and not cola.activa


async def test_la_cola_cancela_el_repaso_anterior_antes_de_trabajar(demo, ejecutor):
    m, _ = demo
    terminado = asyncio.Event()

    async def repaso():
        try:
            await asyncio.Event().wait()
        finally:
            terminado.set()
    m.transport.repaso = asyncio.create_task(repaso())
    await asyncio.sleep(0)
    m.cola_encargos.preparar(['A'])
    m.cola_encargos.iniciar()
    await m.cola_encargos.worker
    assert terminado.is_set() and m.transport.repaso is None
    assert m.cola_encargos.datos['estado'] == 'completada'


GUION = r'''
import json, sys, os
from pathlib import Path
import httpx
from alfalfa import primera_vez
from alfalfa.proveedores.ollama import Ollama
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.proveedores.nativo.herramientas import digest
from alfalfa.terminal import main
primera_vez.ollama_instalado = lambda: True
def cliente(self, timeout):
    self._precio = self._ficha = self._tarifa = None
    self._proveedor = Ollama()
    return self._proveedor, self.task['model'], httpx.AsyncClient(base_url='https://simulado.test')
async def pedir(self, client, endpoint, payload, **kw):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    assert self.task.get('padre'), 'La sesión de la cola no debe pedir inferencia para el resumen'
    root = Path(self.task['cwd'])
    paso = sum(m['role'] == 'assistant' for m in self.state['messages'][self.attempt_start_index:])
    funcion = None
    if paso == 0:
        funcion = {'name': 'read_file', 'arguments': {'path': 'base.txt'}}
    elif paso == 1:
        funcion = {'name': 'write_file', 'arguments': {'path': 'base.txt', 'content': self.task['objective'] + '\n',
                    'expected_sha256': digest((root / 'base.txt').read_bytes())}}
    elif paso == 2:
        funcion = {'name': 'wrap_up', 'arguments': {
            'recap': 'Hecho ' + self.task['objective'] + '; pruebas no ejecutadas.'}}
    return {'message': {'role': 'assistant', 'content': '' if funcion else 'Terminado; pruebas no ejecutadas.',
                        'tool_calls': [{'function': funcion}] if funcion else []},
            'done': True, 'done_reason': 'stop', 'prompt_eval_count': 100, 'eval_count': 20}
NativeRuntime._cliente = cliente
NativeRuntime._request = pedir
if os.environ.get('PRUEBA_APROBAR') == '1':
    from alfalfa.permisos.terminal import TerminalApproval
    async def aprobar(self, args):
        assert args['motivo'] == 'contenido_externo'
        assert 'Encargo «' in args['purpose']
        print('PERMISO EXPLÍCITO SIMULADO')
    TerminalApproval.__call__ = aprobar
sys.exit(main(sys.argv[1:]))
'''


@pytest.mark.parametrize('modo', ['serie', 'paralelo'])
def test_stdio_guardar_iniciar_permisos_y_resumen_con_runtime_real(tmp_path, modo):
    import os
    import queue
    import sys
    import threading

    root = tmp_path / 'proyecto'
    root.mkdir()
    (root / 'base.txt').write_text('base\n')
    if modo == 'paralelo':
        git(root, 'init', '-q')
        git(root, 'add', '.')
        git(root, 'commit', '-qm', 'Base de prueba')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               ALFALFA_HOME=str(tmp_path / 'alfalfa'))
    proceso = subprocess.Popen([sys.executable, '-c', GUION, '--stdio', '--modelo', 'simulado', '--cola-encargos'],
        cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    recibidos = queue.Queue()
    threading.Thread(target=lambda: [recibidos.put(json.loads(linea)) for linea in proceso.stdout], daemon=True).start()
    permisos = []

    def enviar(valor):
        proceso.stdin.write(json.dumps(valor) + '\n')
        proceso.stdin.flush()

    def hasta(tipo, estado=None):
        while True:
            fila = recibidos.get(timeout=90)
            if fila['tipo'] == 'error':
                raise AssertionError(fila)
            if fila['tipo'] == 'permiso':
                permisos.append(fila)
                enviar({'tipo': 'permiso', 'id': fila['id'], 'aprobado': True})
            if fila['tipo'] == tipo and (estado is None or fila.get('cola', {}).get('estado') == estado):
                return fila
            if fila['tipo'] == 'encargos' and fila['cola']['estado'] == 'pausada':
                raise AssertionError(fila)
    try:
        sesion = hasta('sesion')['id']
        enviar({'tipo': 'encargos', 'accion': 'preparar', 'encargos': ['A', 'B'], 'modo': modo, 'paralelos': 2})
        lista = hasta('encargos', 'lista')['cola']
        assert all(not f.get('sesion') for f in lista['encargos'])
        assert (root / 'base.txt').read_text() == 'base\n'
        enviar({'tipo': 'encargos', 'accion': 'iniciar'})
        fin = hasta('encargos', 'completada')['cola']
        assert [f['estado'] for f in fin['encargos']] == ['completed', 'completed']
        assert len({f['sesion'] for f in fin['encargos']}) == 2
        assert permisos and all('Encargo «' in p['proposito'] for p in permisos)
        assert 'Hecho A' in fin['resumen'] and 'Hecho B' in fin['resumen']
        if modo == 'serie':
            assert (root / 'base.txt').read_text() == 'B\n'
        else:
            assert (root / 'base.txt').read_text() == 'base\n'
            assert [(Path(f['carpeta']) / 'base.txt').read_text() for f in fin['encargos']] == ['A\n', 'B\n']
        enviar({'tipo': 'cancelar'})
        proceso.wait(timeout=20)
        guardado = json.loads((tmp_path / 'alfalfa' / 'sesiones' / sesion / 'task.json').read_text())
        assert guardado['cola_encargos']['estado'] == 'completada'
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
        stderr = proceso.stderr.read()
        for tubo in (proceso.stdin, proceso.stdout, proceso.stderr):
            tubo.close()
    assert proceso.returncode == 0, stderr


@pytest.mark.parametrize('aprobar', [False, True])
def test_cli_archivo_y_resumen_sin_proveedor_real(tmp_path, aprobar):
    import os
    import sys

    root = tmp_path / 'proyecto'
    root.mkdir()
    (root / 'base.txt').write_text('base\n')
    archivo = tmp_path / 'cola.json'
    archivo.write_text(json.dumps({'encargos': ['A', 'B']}))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               ALFALFA_HOME=str(tmp_path / 'alfalfa'), PRUEBA_APROBAR='1' if aprobar else '0')
    resultado = subprocess.run([sys.executable, '-c', GUION, '--modelo', 'simulado', '--modo', 'omitir',
        '--cola-encargos', str(archivo)], cwd=root, env=env, capture_output=True, text=True, timeout=90)
    assert 'Resumen de la cola' in resultado.stdout
    assert 'Sesión de la cola: ' in resultado.stdout
    if aprobar:
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr
        assert 'Hecho A' in resultado.stdout and 'Hecho B' in resultado.stdout
        assert (root / 'base.txt').read_text() == 'B\n'
        assert resultado.stdout.count('PERMISO EXPLÍCITO SIMULADO') == 2
    else:
        assert resultado.returncode == 1, resultado.stdout + resultado.stderr
        assert 'B: pendiente' in resultado.stdout
        assert (root / 'base.txt').read_text() == 'base\n'
