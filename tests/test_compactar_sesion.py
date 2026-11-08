"""Compactar una sesión como en Claude Code: al momento entre turnos, con instrucciones,
con su marca en la conversación y con el panel de contexto al día.

El proceso es el de producción (`alfalfa --stdio`); sólo se sustituye la inferencia.
"""
import asyncio
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from alfalfa.compactacion import PREFIJO, PROMPT, prompt_de
from alfalfa.permisos.denegacion import is_likely_sandbox_denied
from alfalfa.proveedores.nativo.herramientas import argv_sin_comillas_de_shell
from alfalfa.stdio import COMPACTAR, Transport, history

SESION = '''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.compactacion import PROMPT
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
    with open('requests.jsonl', 'a') as stream:
        stream.write(json.dumps(payload['messages']) + '\\n')
    last = payload['messages'][-1]['content']
    if last.startswith(PROMPT):
        return dict(message=dict(role='assistant', content='Resumen: el usuario quiere tres pasos.'),
                    done=True, done_reason='stop', prompt_eval_count=500, eval_count=20)
    return dict(message=dict(role='assistant', content='Respuesta a: ' + last[:40]), done=True,
                done_reason='stop', prompt_eval_count=4000, eval_count=10)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--stdio'] + sys.argv[1:]))
'''


class Cliente:
    def __init__(self, tmp_path, *argumentos):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                   ALFALFA_HOME=str(tmp_path / 'home'))
        self.cwd = tmp_path
        self.filas = []
        self.proc = subprocess.Popen([sys.executable, '-c', SESION, *argumentos], cwd=tmp_path, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)

    def hasta(self, condicion):
        while True:
            assert select.select([self.proc.stdout], [], [], 15)[0], 'stdout sin respuesta'
            fila = json.loads(self.proc.stdout.readline())
            self.filas.append(fila)
            if condicion(fila):
                return fila

    def tipo(self, tipo):
        return self.hasta(lambda f: f['tipo'] == tipo)

    def enviar(self, **valor):
        self.proc.stdin.write(json.dumps(valor) + '\n')
        self.proc.stdin.flush()

    def cerrar(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=15)
        self.filas.extend(json.loads(l) for l in self.proc.stdout.read().splitlines())
        return self.proc.stderr.read()

    def peticiones(self):
        ruta = self.cwd / 'requests.jsonl'
        return [json.loads(l) for l in ruta.read_text().splitlines()] if ruta.exists() else []

    def matar(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def compactacion(fila):
    return fila['tipo'] == 'evento' and isinstance(fila.get('compactacion'), dict) \
        and fila['compactacion']['estado'] != 'en_curso'


def test_compactar_entre_turnos_con_instrucciones_y_seguir(tmp_path, monkeypatch):
    cliente = Cliente(tmp_path, 'Haz tres pasos.')
    try:
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        fin_de_turno = [f for f in cliente.filas if f['tipo'] == 'contexto'][-1]
        # Al acabar el turno el panel recibe el uso estimado y el umbral (90 % de la ventana).
        assert fin_de_turno['umbral'] == 131072 * 9 // 10 and fin_de_turno['estimado'] >= fin_de_turno['tokens']
        cliente.enviar(tipo='compactar', instrucciones='Conserva las rutas de los archivos.')
        hecha = cliente.hasta(compactacion)['compactacion']
        assert hecha['estado'] == 'hecha' and hecha['motivo'] == 'manual'
        assert hecha['instrucciones'] == 'Conserva las rutas de los archivos.'
        assert hecha['resumen'] == 'Resumen: el usuario quiere tres pasos.' and hecha['antes'] > 0
        contexto = cliente.tipo('contexto')
        assert contexto['estimado'] == contexto['tokens'] and contexto['umbral'] > contexto['tokens']
        resumen = cliente.peticiones()[-1]
        assert resumen[-1]['content'] == prompt_de('Conserva las rutas de los archivos.')
        assert 'Conserva las rutas' in resumen[-1]['content'] and resumen[-1]['content'].startswith(PROMPT)
        # No abre turno: no hay turno_terminado y el siguiente mensaje parte del resumen.
        assert not [f for f in cliente.filas[-3:] if f['tipo'] == 'turno_terminado']
        cliente.enviar(tipo='mensaje', texto='Sigue.')
        assert cliente.tipo('turno_terminado')['estado'] == 'completed'
        siguiente = cliente.peticiones()[-1]
        assert siguiente[-2] == {'role': 'user', 'content': PREFIJO + '\nResumen: el usuario quiere tres pasos.'}
        assert siguiente[-1] == {'role': 'user', 'content': 'Sigue.'}
        assert not any(m['role'] == 'assistant' for m in siguiente)
        antes = len(cliente.peticiones())
        stderr = cliente.cerrar()
        assert cliente.proc.returncode == 0, stderr
        assert len(cliente.peticiones()) == antes
    finally:
        cliente.matar()
    ident = next(f for f in cliente.filas if f['tipo'] == 'sesion')['id']
    monkeypatch.setenv('ALFALFA_HOME', str(tmp_path / 'home'))
    datos = history(ident)
    marcas = [e for e in datos['eventos'] if 'compactacion' in e]
    # El historial guarda cómo acabó, no el «Compactando…».
    assert [m['compactacion']['estado'] for m in marcas] == ['hecha']
    assert marcas[0]['compactacion']['resumen'].startswith('Resumen:')
    assert datos['contexto']['umbral'] == 131072 * 9 // 10


def test_esperar_abre_sin_turno_y_compacta_una_sesion_terminada(tmp_path):
    primero = Cliente(tmp_path, 'Haz tres pasos.')
    try:
        primero.tipo('turno_terminado')
        assert primero.cerrar() is not None and primero.proc.returncode == 0
    finally:
        primero.matar()
    ident = next(f for f in primero.filas if f['tipo'] == 'sesion')['id']
    pedidas = len(primero.peticiones())
    cliente = Cliente(tmp_path, '--reanudar', ident, '--esperar')
    try:
        assert cliente.tipo('sesion')['id'] == ident
        cliente.enviar(tipo='compactar')
        assert cliente.hasta(compactacion)['compactacion']['estado'] == 'hecha'
        assert len(cliente.peticiones()) == pedidas + 1  # sólo el resumen, ningún turno
        cliente.enviar(tipo='compactar')
        fallida = cliente.hasta(compactacion)
        assert fallida['compactacion']['estado'] == 'fallida' and 'No hay historia nueva' in fallida['texto']
        stderr = cliente.cerrar()
        assert cliente.proc.returncode == 0, stderr
        assert not [f for f in cliente.filas if f['tipo'] in ('error', 'turno_terminado')]
        assert cliente.filas[-1]['tipo'] == 'fin' and cliente.filas[-1]['estado'] == 'completed'
    finally:
        cliente.matar()


def test_compactar_durante_un_turno_espera_a_la_siguiente_respuesta():
    async def ejercicio():
        salida = io.StringIO()
        t = Transport(salida)
        t.in_turn = True
        t.receive(json.dumps({'tipo': 'compactar', 'instrucciones': '  Céntrate en los fallos.  '}))
        assert t.compact_pending == 'Céntrate en los fallos.' and t.compact_now is False
        assert 'siguiente respuesta' in salida.getvalue()
        assert t.take_compact() == 'Céntrate en los fallos.' and t.take_compact() is False
        t.in_turn = False
        t.receive(json.dumps({'tipo': 'compactar'}))
        t.receive(json.dumps({'tipo': 'compactar'}))
        assert 'Ya se está compactando' in salida.getvalue()
        t.receive(json.dumps({'tipo': 'mensaje', 'texto': 'después'}))
        assert await asyncio.wait_for(t.next_message(), 1) == (COMPACTAR, None)
        assert await asyncio.wait_for(t.next_message(), 1) == 'después'
        t.receive(json.dumps({'tipo': 'compactar', 'instrucciones': 'x' * 2001}))
        assert 'inválido' in salida.getvalue().splitlines()[-1]
    asyncio.run(ejercicio())


def test_el_desglose_separa_memoria_mcp_y_complementos():
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    payload = {'messages': [{'role': 'system', 'content': 'x' * 8000}, {'role': 'user', 'content': 'y' * 2000}],
               'tools': [{'function': {'name': 'read_file', 'description': 'z' * 1000}},
                         {'function': {'name': 'abrir_habilidad', 'description': 'h' * 400}}]}
    partes = NativeRuntime._desglose(payload, 6000, {'memoria': 500, 'instrucciones_mcp': 200, 'vacia': 0})
    assert {'memoria', 'instrucciones_mcp', 'complementos'} <= set(partes) and 'vacia' not in partes
    assert abs(sum(partes.values()) - 6000) <= 3
    assert partes['sistema'] > partes['memoria'] > partes['instrucciones_mcp'] > 0
    # Una pieza nunca pesa más que el mensaje de sistema.
    assert NativeRuntime._desglose(payload, None, {'memoria': 10 ** 9})['sistema'] == 0


def test_los_archivos_de_memoria_llegan_al_panel(tmp_path, monkeypatch):
    from unittest.mock import patch
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    raiz = tmp_path / 'proyecto'
    (raiz / '.git').mkdir(parents=True)
    (raiz / 'AGENTS.md').write_text('Usa pytest. ' * 100)
    monkeypatch.chdir(raiz)
    salida = io.StringIO()
    m = TerminalManager('Hola', home=tmp_path / 'home', transport=Transport(salida))

    async def inferencia(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        return dict(message={'role': 'assistant', 'content': 'Hola.'}, done=True, done_reason='stop',
                    prompt_eval_count=9000, eval_count=5)
    try:
        with patch.object(NativeRuntime, 'request', inferencia):
            assert asyncio.run(m.run('Hola')) == 0
    finally:
        m.close()
    contexto = [json.loads(l) for l in salida.getvalue().splitlines() if json.loads(l)['tipo'] == 'contexto'][-1]
    archivo, = contexto['memoria']
    assert Path(archivo['ruta']).resolve() == (raiz / 'AGENTS.md').resolve() and archivo['tokens'] > 250
    assert contexto['desglose']['memoria'] > 0


def test_execvp_sin_archivo_no_es_bloqueo_del_sandbox():
    falta = "sandbox-exec: execvp() of './\"Abrir laboratorio 3D.command\"' failed: No such file or directory"
    assert not is_likely_sandbox_denied('seatbelt', 71, aggregated=falta)
    assert not is_likely_sandbox_denied('seatbelt', 71, aggregated="sandbox-exec: execvp() of 'x' failed: Not a directory")
    # Un execvp que el sandbox sí bloquea, o su propio sandbox anidado, siguen contando.
    assert is_likely_sandbox_denied('seatbelt', 71, aggregated="sandbox-exec: execvp() of 'x' failed: Operation not permitted")
    assert is_likely_sandbox_denied('seatbelt', 1, aggregated='sandbox-exec: sandbox_apply: Operation not permitted')
    assert is_likely_sandbox_denied('seatbelt', 1, aggregated=falta + '\nerror: Permission denied')


@pytest.mark.parametrize('escrito, esperado', [
    ('./"Abrir laboratorio 3D.command"', './Abrir laboratorio 3D.command'),
    ("'Abrir laboratorio 3D.command'", 'Abrir laboratorio 3D.command'),
    ('Abrir\\ laboratorio\\ 3D.command', 'Abrir laboratorio 3D.command'),
])
def test_argv_con_comillas_de_shell(tmp_path, escrito, esperado):
    (tmp_path / 'Abrir laboratorio 3D.command').write_text('#!/bin/sh\n')
    assert argv_sin_comillas_de_shell(['open', escrito], tmp_path) == ['open', esperado]
    # Lo que ya es literal, o no nombra un archivo, no se toca.
    assert argv_sin_comillas_de_shell(['open', esperado], tmp_path) is None
    assert argv_sin_comillas_de_shell(['python3', '-c', 'print("x")'], tmp_path) is None
    assert argv_sin_comillas_de_shell(['echo', '"no existe"'], tmp_path) is None


def test_run_command_con_comillas_no_pide_permiso_y_dice_el_argv_bueno(tmp_path, monkeypatch):
    from unittest.mock import patch
    from alfalfa.proveedores.nativo.runtime import NativeRuntime
    from alfalfa.terminal import TerminalManager
    raiz = tmp_path / 'proyecto'
    raiz.mkdir()
    (raiz / 'Abrir laboratorio 3D.command').write_text('#!/bin/sh\necho hola\n')
    monkeypatch.chdir(raiz)
    salida = io.StringIO()
    m = TerminalManager('Ábrelo', home=tmp_path / 'home', transport=Transport(salida))
    respuestas = []

    async def inferencia(rt, client, endpoint, payload, **kwargs):
        if endpoint == '/api/show':
            return {'capabilities': ['tools'], 'model_info': {'x.context_length': 131072}}
        herramienta = [x for x in payload['messages'] if x['role'] == 'tool']
        if herramienta:
            respuestas.append(herramienta[-1]['content'])
            return dict(message={'role': 'assistant', 'content': 'Listo.'}, done=True, done_reason='stop',
                        prompt_eval_count=10, eval_count=5)
        return dict(message={'role': 'assistant', 'content': '', 'tool_calls': [{'function': {
            'name': 'run_command', 'arguments': {'argv': ['./"Abrir laboratorio 3D.command"'],
                                                 'purpose': 'Abrir el laboratorio'}}}]},
                    done=True, done_reason='stop', prompt_eval_count=10, eval_count=5)
    try:
        with patch.object(NativeRuntime, 'request', inferencia):
            asyncio.run(m.run('Ábrelo'))
    finally:
        m.close()
    assert not [l for l in salida.getvalue().splitlines() if json.loads(l)['tipo'] == 'permiso']
    assert respuestas and 'argv es literal' in respuestas[0]
    assert './Abrir laboratorio 3D.command' in respuestas[0] and 'No se ejecutó' in respuestas[0]
