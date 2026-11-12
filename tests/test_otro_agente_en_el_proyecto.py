"""Otro agente trabajando en el mismo proyecto no deja la sesión bloqueada.

Pasó en CuyWorld (11/11) con «dime la version de python que tengo»: Codex
exportaba con Blender en el mismo proyecto, así que cada huella de run_checks
fallaba («El workspace cambió mientras se tomaba su huella»). Un `python
--version` fallido (no hay `python` sin el 3) quedó como pendiente; el aviso
de «Antes de terminar» hizo al modelo repetir `run_checks python3 --version`,
cada verde sin verificar se volvió otro pendiente, y la sesión acabó
«bloqueada» con «Quedaron 2 operaciones sin resolver: terminó con código 0» y
la revisión de código ofrecida sobre los cambios de Codex.
"""
import asyncio
import hashlib
import subprocess
import sys
from unittest.mock import patch

from alfalfa.evidencia import espacio
from alfalfa.proveedores.nativo import historial
from alfalfa.proveedores.nativo.runtime import NativeRuntime
from alfalfa.terminal import TerminalManager

CAMBIO = 'El workspace cambió mientras se tomaba su huella.'


def _repo(raiz):
    raiz.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=raiz, check=True)
    (raiz / 'mundo.txt').write_text('hola\n')
    subprocess.run(['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com', 'add', '-A'],
                   cwd=raiz, check=True)
    subprocess.run(['git', '-c', 'user.name=prueba', '-c', 'user.email=prueba@example.com', 'commit', '-qm',
                    'inicio'], cwd=raiz, check=True)
    return raiz


def _llamada(nombre, argv, proposito):
    return {'function': {'name': nombre, 'arguments': {'argv': argv, 'purpose': proposito}}}


def _sesion(tmp_path, raiz, monkeypatch, pasos):
    monkeypatch.chdir(raiz)
    vistos = []

    async def request(self, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        vistos.append(payload)
        return dict(message=pasos.pop(0) if pasos else {'role': 'assistant', 'content': 'Tienes Python 3.'},
                    done=True, done_reason='stop', prompt_eval_count=900, eval_count=10)
    m = TerminalManager('dime la version de python que tengo', home=tmp_path / 'alfalfa')
    try:
        with patch.object(NativeRuntime, 'request', request):
            asyncio.run(asyncio.wait_for(m.run('dime la version de python que tengo'), 120))
        estado = historial.leer(m.directory / 'native' / m.task['id'] / 'session.json')
        return m.task, estado, vistos
    finally:
        m.close()


def _otro_agente_cambiando(monkeypatch):
    def cambia(root):
        raise ValueError(CAMBIO)
    monkeypatch.setattr(espacio, '_capture', cambia)


def test_una_pregunta_con_otro_agente_en_el_proyecto_termina_sin_bloquearse(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    _otro_agente_cambiando(monkeypatch)
    pasos = [
        {'role': 'assistant', 'content': '', 'tool_calls': [
            _llamada('run_command', ['no-existe-este-python-alfalfa', '--version'], 'Ver si hay python sin el 3')]},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            _llamada('run_checks', [sys.executable, '--version'], 'Ver la versión de Python')]},
        {'role': 'assistant', 'content': 'Tienes Python 3; `python` sin el 3 no existe.'},
    ]
    tarea, estado, vistos = _sesion(tmp_path, raiz, monkeypatch, pasos)
    assert tarea['status'] == 'completed', tarea.get('summary')
    # El `python --version` fallido sigue apuntado, pero el verde sin verificar ya no es un pendiente.
    assert list(estado.get('unresolved') or {}) == ['finding-000001']
    # Sin el aviso de «Antes de terminar»: no había nada que arreglar.
    assert not any('Antes de terminar' in str(m.get('content')) for m in vistos[-1]['messages']
                   if m.get('role') == 'user')
    check, = estado['checks']
    assert check['exit_code'] == 0 and check['verification_status'] == 'workspace_unverified'
    # Y al modelo se le dice que repetirla no sirve.
    resultado = [str(m['content']) for m in vistos[-1]['messages'] if m.get('role') == 'tool']
    assert any('No la repitas' in r for r in resultado)
    # Nada suyo que revisar: la revisión de código no se ofrece.
    assert not ((tarea.get('cierre') or {}).get('revision_codigo'))


def test_si_la_sesion_cambio_archivos_una_orden_fallida_sigue_pendiente(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    pasos = [
        {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'write_file', 'arguments': {
            'path': 'mundo.txt', 'content': 'adiós\n',
            'expected_sha256': hashlib.sha256(b'hola\n').hexdigest()}}}]},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            _llamada('run_command', [sys.executable, '-c', 'raise SystemExit(3)'], 'Compilar el mundo')]},
        {'role': 'assistant', 'content': 'Hecho.'},
        {'role': 'assistant', 'content': 'Hecho, aunque la compilación falló.'},
    ]
    tarea, estado, vistos = _sesion(tmp_path, raiz, monkeypatch, pasos)
    assert estado.get('changes')
    assert any('Antes de terminar' in str(m.get('content')) for m in vistos[-1]['messages']
               if m.get('role') == 'user')
    assert tarea['status'] == 'blocked' and estado.get('unresolved')


def test_si_otro_agente_cambia_lo_ya_comprobado_se_avisa_sin_bloquear(tmp_path, monkeypatch):
    raiz = _repo(tmp_path / 'repo')
    pasos = [
        {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'write_file', 'arguments': {
            'path': 'mundo.txt', 'content': 'adiós\n',
            'expected_sha256': hashlib.sha256(b'hola\n').hexdigest()}}}]},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            _llamada('run_checks', [sys.executable, '-c', "assert open('mundo.txt').read() == 'adiós\\n'"],
                     'Comprobar el mundo')]},
        {'role': 'assistant', 'content': 'Cambié mundo.txt y lo comprobé.'},
    ]

    class ConOtroAgente(list):
        def pop(self, i=-1):
            if len(self) == 1:
                # Mientras el modelo escribe su respuesta, otro agente edita el mismo archivo.
                (raiz / 'mundo.txt').write_text('lo cambió Codex\n')
            return super().pop(i)
    pasos = ConOtroAgente(pasos)
    tarea, estado, _ = _sesion(tmp_path, raiz, monkeypatch, pasos)
    assert estado['checks'][-1]['verification_status'] == 'sufficient'
    assert tarea['status'] == 'completed', tarea.get('summary')
