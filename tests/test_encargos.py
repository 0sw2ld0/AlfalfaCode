"""Colas del usuario con sesiones y proyectos temporales; nunca proveedores reales."""
import asyncio
import io
import json
import os
import shutil
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
    # `hacer`: objetivo → lo que el encargo deja en su carpeta, en lugar de los archivos de siempre.
    control = {'bloqueado': None, 'esperar': None, 'activos': 0, 'maximos': 0, 'hacer': {}}

    async def trabajo(m, texto, eco=False):
        vistos.append((m, texto))
        control['activos'] += 1
        control['maximos'] = max(control['maximos'], control['activos'])
        try:
            if control['esperar']:
                await control['esperar'].wait()
            await asyncio.sleep(.01)
            root = Path(m.task['cwd'])
            if m.task['objective'] in control['hacer']:
                control['hacer'][m.task['objective']](root)
            elif m.task['objective'] == 'primero':
                (root / 'primero.txt').write_text('primer resultado')
            elif m.task['objective'] == 'segundo':
                assert (root / 'primero.txt').read_text() == 'primer resultado'
            if m.task['objective'] not in control['hacer']:
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
    # Los encargos sólo leyeron el proyecto: su resumen no obliga a pedir permiso extra.
    assert not rt.state.get('contenido_externo')
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
        # En Manual cada edición pide permiso; la cola le pone su título y carpeta.
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
    proceso = subprocess.Popen([sys.executable, '-c', GUION, '--stdio', '--modelo', 'simulado', '--modo', 'manual',
                                '--cola-encargos'],
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
            # El trabajo de cada encargo está sin confirmar: los dos worktrees se conservan.
            assert [f['limpieza']['motivo'] for f in fin['encargos']] == ['tiene 1 cambio sin confirmar'] * 2
            assert 'Se conserva: tiene 1 cambio sin confirmar' in fin['resumen']
            # Descartado a mano el de A, la orden explícita lo quita y deja el de B.
            git(fin['encargos'][0]['carpeta'], 'checkout', '--', 'base.txt')
            enviar({'tipo': 'encargos', 'accion': 'limpiar'})
            limpia = hasta('encargos')['cola']['encargos']
            assert [f['limpieza']['estado'] for f in limpia] == ['quitado', 'conservado']
            assert not Path(limpia[0]['carpeta']).exists()
            assert (Path(limpia[1]['carpeta']) / 'base.txt').read_text() == 'B\n'
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
    resultado = subprocess.run([sys.executable, '-c', GUION, '--modelo', 'simulado', '--modo', 'manual',
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


# Limpieza de los worktrees de la cola: con repos de verdad, nunca se pierde trabajo.

def repo_de_prueba(root, gitignore=False):
    git(root, 'init', '-q', '-b', 'main')
    if gitignore:
        (root / '.gitignore').write_text('build/\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Base de prueba')


def foto_del_repo(root):
    """Lo que el usuario ve de su repositorio: HEAD, rama, índice y `git status`."""
    sin_escribir = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    return [subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, env=sin_escribir).stdout
            for args in (['rev-parse', 'HEAD'], ['symbolic-ref', 'HEAD'], ['ls-files', '--stage'],
                         ['status', '--porcelain', '--untracked-files=all'])]


def hay_rama(root, rama):
    return subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', '-q', 'refs/heads/' + rama],
                          capture_output=True).returncode == 0


def registrados(root):
    salida = git(root, 'worktree', 'list', '--porcelain')
    return [str(Path(linea.split(' ', 1)[1]).resolve()) for linea in salida.splitlines()
            if linea.startswith('worktree ')]


async def cola_en_paralelo(m, textos):
    cola = m.cola_encargos
    cola.preparar(textos, 'paralelo', 2)
    cola.iniciar()
    await cola.worker
    return cola


def nada(_root):
    pass


def commit(texto):
    def hacer(root):
        (root / 'nuevo.txt').write_text(texto)
        git(root, 'add', 'nuevo.txt')
        git(root, 'commit', '-qm', texto)
    return hacer


async def test_limpieza_quita_el_worktree_limpio_sin_commits_y_su_rama(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root, gitignore=True)
    foto = foto_del_repo(root)

    def solo_ignorados(carpeta):
        (carpeta / 'build').mkdir()
        (carpeta / 'build' / 'salida.o').write_text('compilado')
    ejecutor[1]['hacer'] = {'A': solo_ignorados}
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert cola.datos['estado'] == 'completada', cola.datos
    assert cola.datos['rama_base'] == 'main'
    assert fila['limpieza'] == {'estado': 'quitado', 'motivo': None}
    assert not Path(fila['carpeta']).exists() and not Path(fila['carpeta']).parent.exists()
    assert not hay_rama(root, fila['rama'])
    assert registrados(root) == [str(root.resolve())]
    assert 'Worktree y rama quitados' in cola.datos['resumen']
    assert foto_del_repo(root) == foto


async def test_limpieza_conserva_un_worktree_con_cambios_sin_confirmar(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    foto = foto_del_repo(root)
    ejecutor[1]['hacer'] = {'A': lambda carpeta: (carpeta / 'base.txt').write_text('trabajo del agente\n')}
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert fila['limpieza']['estado'] == 'conservado'
    assert fila['limpieza']['motivo'] == 'tiene 1 cambio sin confirmar'
    assert (Path(fila['carpeta']) / 'base.txt').read_text() == 'trabajo del agente\n'
    assert hay_rama(root, fila['rama'])
    assert 'Se conserva: tiene 1 cambio sin confirmar' in cola.datos['resumen']
    assert foto_del_repo(root) == foto


async def test_limpieza_conserva_un_worktree_con_archivos_nuevos_sin_seguimiento(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    foto = foto_del_repo(root)

    def nuevos(carpeta):
        (carpeta / 'informe.md').write_text('resultado')
        (carpeta / 'sub').mkdir()
        (carpeta / 'sub' / 'otro.py').write_text('x = 1\n')
    ejecutor[1]['hacer'] = {'A': nuevos}
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert fila['limpieza'] == {'estado': 'conservado', 'motivo': 'tiene 2 archivos nuevos sin seguimiento'}
    assert (Path(fila['carpeta']) / 'sub' / 'otro.py').read_text() == 'x = 1\n'
    assert hay_rama(root, fila['rama'])
    assert foto_del_repo(root) == foto


async def test_limpieza_conserva_la_rama_con_commits_sin_integrar_y_la_quita_al_integrarla(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    foto = foto_del_repo(root)
    ejecutor[1]['hacer'] = {'A': commit('Trabajo del agente')}
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert fila['limpieza'] == {'estado': 'conservado', 'motivo': 'tiene 1 commit sin integrar en main'}
    punta = git(root, 'rev-parse', fila['rama'])
    assert Path(fila['carpeta']).exists() and hay_rama(root, fila['rama'])
    assert foto_del_repo(root) == foto
    # Pedirlo otra vez no cambia nada mientras no se integre.
    assert 'tiene 1 commit sin integrar' in await cola.limpiar()
    assert git(root, 'rev-parse', fila['rama']) == punta and Path(fila['carpeta']).exists()
    # El usuario lo integra en su rama: entonces sí se quitan.
    git(root, 'merge', '-q', '--ff-only', fila['rama'])
    foto = foto_del_repo(root)
    texto = await cola.limpiar()
    assert 'worktree y rama quitados' in texto
    assert fila['limpieza']['estado'] == 'quitado'
    assert not Path(fila['carpeta']).exists() and not hay_rama(root, fila['rama'])
    assert git(root, 'cat-file', '-t', punta) == 'commit'
    assert foto_del_repo(root) == foto
    eventos = [json.loads(linea) for linea in (m.directory / (m.task['id'] + '.jsonl')).read_text().splitlines()]
    assert any(e['role'] == 'system' and e['text'].startswith('Limpieza de los worktrees') for e in eventos)


async def test_limpieza_conserva_los_worktrees_que_la_cola_va_a_reanudar(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    foto = foto_del_repo(root)
    ejecutor[1]['hacer'] = {'A': nada, 'B': nada}
    ejecutor[1]['bloqueado'] = 'B'
    cola = await cola_en_paralelo(m, ['A', 'B'])
    a, b = cola.datos['encargos']
    assert cola.datos['estado'] == 'pausada' and b['estado'] == 'blocked'
    # A terminó y reanudar lo salta: se quita. B se reanudará en su worktree: se conserva tal cual.
    assert a['limpieza']['estado'] == 'quitado' and not Path(a['carpeta']).exists()
    assert b['limpieza'] == {'estado': 'conservado', 'motivo': 'la cola puede reanudarlo'}
    assert Path(b['carpeta']).exists() and hay_rama(root, b['rama'])
    assert not cola.limpiables()
    await cola.limpiar()
    assert Path(b['carpeta']).exists() and hay_rama(root, b['rama'])
    ejecutor[1]['bloqueado'] = None
    cola.iniciar()
    await cola.worker
    assert cola.datos['estado'] == 'completada'
    assert sorted(h.task['objective'] for h, _ in ejecutor[0]) == ['A', 'B', 'B']  # A no se repite
    assert b['limpieza']['estado'] == 'quitado' and not hay_rama(root, b['rama'])
    assert foto_del_repo(root) == foto


async def test_limpieza_poda_el_registro_huerfano_de_la_cola_y_no_toca_otros(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    ajeno = root.parent / 'ajeno'
    git(root, 'worktree', 'add', '-q', '-b', 'del-usuario', str(ajeno))
    shutil.rmtree(ajeno)
    foto = foto_del_repo(root)
    ejecutor[1]['hacer'] = {'A': lambda carpeta: (carpeta / 'borrador.txt').write_text('x')}
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert fila['limpieza']['estado'] == 'conservado'
    shutil.rmtree(fila['carpeta'])  # El usuario borra a mano la carpeta del worktree.
    assert str(Path(fila['carpeta']).resolve()) in registrados(root)
    await cola.limpiar()
    assert fila['limpieza']['estado'] == 'quitado'
    assert str(Path(fila['carpeta']).resolve()) not in registrados(root) and not hay_rama(root, fila['rama'])
    # El worktree del usuario sin carpeta sigue registrado: no es de la cola.
    assert str(ajeno.resolve()) in registrados(root) and hay_rama(root, 'del-usuario')
    assert foto_del_repo(root) == foto


async def test_limpieza_no_toca_carpetas_que_no_creo_la_cola(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    otro = root.parent / 'otro-worktree'
    git(root, 'worktree', 'add', '-q', '-b', 'otra-rama', str(otro))
    ejecutor[1]['hacer'] = {'A': lambda carpeta: (carpeta / 'borrador.txt').write_text('x')}
    cola = await cola_en_paralelo(m, ['A'])
    cola.datos['encargos'][0].update(carpeta=str(otro), rama='otra-rama', limpieza=None)
    await cola.limpiar()
    assert cola.datos['encargos'][0]['limpieza'] == {'estado': 'conservado',
                                                      'motivo': 'no es un worktree creado por esta cola'}
    assert otro.exists() and hay_rama(root, 'otra-rama')


async def test_cancelar_la_cola_por_stdio_limpia_lo_que_ya_no_se_reanuda(demo, ejecutor):
    m, root = demo
    repo_de_prueba(root)
    ejecutor[1]['hacer'] = {'A': nada}
    ejecutor[1]['bloqueado'] = 'A'
    cola = await cola_en_paralelo(m, ['A'])
    fila = cola.datos['encargos'][0]
    assert fila['limpieza']['estado'] == 'conservado' and Path(fila['carpeta']).exists()
    cola.cancelar()
    assert cola.limpiables()
    await cola.limpiar()
    assert fila['limpieza']['estado'] == 'quitado' and not Path(fila['carpeta']).exists()


def test_cli_limpiar_worktrees_de_una_cola_guardada(tmp_path):
    import os
    import sys

    root = tmp_path / 'proyecto'
    root.mkdir()
    (root / 'base.txt').write_text('base\n')
    repo_de_prueba(root)
    foto = foto_del_repo(root)
    archivo = tmp_path / 'cola.json'
    archivo.write_text(json.dumps({'encargos': ['A', 'B'], 'modo': 'paralelo', 'paralelos': 2}))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               ALFALFA_HOME=str(tmp_path / 'alfalfa'), PRUEBA_APROBAR='1')
    orden = [sys.executable, '-c', GUION, '--modelo', 'simulado', '--modo', 'manual']
    resultado = subprocess.run([*orden, '--cola-encargos', str(archivo)], cwd=root, env=env, capture_output=True,
                               text=True, timeout=90)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    sesion = resultado.stdout.split('Sesión de la cola: ', 1)[1].split()[0]
    filas = json.loads((tmp_path / 'alfalfa' / 'sesiones' / sesion / 'task.json').read_text())['cola_encargos']
    a, b = filas['encargos']
    assert Path(a['carpeta']).exists() and Path(b['carpeta']).exists()
    git(a['carpeta'], 'checkout', '--', 'base.txt')
    resultado = subprocess.run([*orden, '--reanudar', sesion, '--limpiar-worktrees'], cwd=root, env=env,
                               capture_output=True, text=True, timeout=90)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert 'A: worktree y rama quitados.' in resultado.stdout
    assert 'B: se conserva' in resultado.stdout and 'tiene 1 cambio sin confirmar' in resultado.stdout
    assert 'PERMISO EXPLÍCITO SIMULADO' not in resultado.stdout  # No ejecutó ningún encargo.
    assert not Path(a['carpeta']).exists() and not hay_rama(root, a['rama'])
    assert (Path(b['carpeta']) / 'base.txt').read_text() == 'B\n' and hay_rama(root, b['rama'])
    assert foto_del_repo(root) == foto
