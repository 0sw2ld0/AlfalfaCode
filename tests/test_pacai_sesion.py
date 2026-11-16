"""`alfalfa --pacai` de verdad: un proceso aparte con el motor de producción, la inferencia sustituida y un cliente
PACAI por stdin y stdout. Cada transcripción pasa el esquema y las reglas de sesión de PACAI 0.3."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from alfalfa.terminal import main
from tests.pacai_comun import SESION, problemas

# Inferencia sustituida: pide una orden que necesita permiso (modo `tool`) o lanza un subagente (`sub`) y luego
# contesta con el último mensaje.
MODELO = '''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime
async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    if self.task.get('subagente_de'):
        return dict(message=dict(role='assistant', content='Informe del subagente', tool_calls=[]), done=True,
                    done_reason='stop', prompt_eval_count=1, eval_count=1)
    calls = []
    if sys.argv[1] == 'tool' and not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='run_command', arguments=dict(
            argv=[sys.executable, '-c', 'print(1)'], purpose='Probar', needs_network=True)))]
    if sys.argv[1] in ('sub', 'fondo') and not any(m['role'] == 'tool' for m in self.state['messages']):
        calls = [dict(function=dict(name='spawn_agent', arguments=dict(
            message='Revisa A', description='Revisor A', agent_type='explorador',
            **({'background': True} if sys.argv[1] == 'fondo' else {}))))]
    last = payload['messages'][-1]
    return dict(message=dict(role='assistant', content='' if calls else 'Hecho: ' + last['content'][:60],
                             tool_calls=calls), done=True, done_reason='stop', prompt_eval_count=1, eval_count=1)
with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(['--pacai']))
'''


class Proceso:
    def __init__(self, tmp_path, modo='tool'):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                   ALFALFA_HOME=str(tmp_path / 'home'))
        self.proc = subprocess.Popen([sys.executable, '-c', MODELO, modo], cwd=tmp_path, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.log, self.seq, self.cola = [], 0, queue.Queue()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for linea in self.proc.stdout:
            self.cola.put(linea)
        self.cola.put(None)

    def enviar(self, tipo, payload=None, **sobre):
        self.seq += 1
        m = dict(pacai='0.3', id='c_%08d' % self.seq, type=tipo, session=SESION, sender='client', seq=self.seq,
                 time='2026-11-15T10:00:00Z', payload=payload or {}, **sobre)
        self.log.append(m)
        self.proc.stdin.write(json.dumps(m) + '\n')
        self.proc.stdin.flush()
        return m

    def hasta(self, cumple, segundos=30):
        """Lee mensajes del agente hasta que uno cumpla la condición; devuelve ese."""
        while True:
            try:
                linea = self.cola.get(timeout=segundos)
            except queue.Empty:
                raise AssertionError('el agente no contestó a tiempo: ' + str([m['type'] for m in self.log])) from None
            assert linea is not None, 'el agente cerró stdout antes de tiempo: ' + self.proc.stderr.read()
            m = json.loads(linea)
            self.log.append(m)
            if cumple(m):
                return m

    def tipo(self, tipo):
        return self.hasta(lambda m: m['type'] == tipo)

    def saludar(self, **cambios):
        self.enviar('hello', dict(versions=['0.3'], profiles=['work'], extensions=['alfalfa'], **cambios))
        return self.tipo('session_started')

    def entrada(self, texto, **payload):
        return self.enviar('input', dict(blocks=[{'type': 'text', 'text': texto}], **payload))

    def terminar(self):
        """EOF: el cliente se va. Devuelve lo que quedó en stdout y stderr."""
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
        while (linea := self.cola.get(timeout=10)) is not None:
            self.log.append(json.loads(linea))
        return self.proc.stderr.read()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def del_agente(self, tipo=None):
        return [m for m in self.log if m['sender'] == 'agent' and (tipo is None or m['type'] == tipo)]


@pytest.fixture
def proceso(tmp_path):
    creados = []

    def crear(modo='tool'):
        creados.append(Proceso(tmp_path, modo))
        return creados[-1]
    yield crear
    for p in creados:
        p.kill()


def test_una_sesion_completa_con_aprobacion_pasa_el_verificador(proceso):
    p = proceso()
    inicio = p.saludar()
    assert inicio['payload']['profiles'] == ['work'] and inicio['reply_to'] == p.log[0]['id']
    entrada = p.entrada('Prueba una orden')
    run = p.tipo('run_started')
    assert run['payload'] == {'input_id': entrada['id']}
    peticion = p.tipo('approval_request')
    assert peticion['payload']['run_id'] == run['id'] and peticion['payload']['reason'] == 'network'
    assert peticion['payload']['arguments']['argv'][1:] == ['-c', 'print(1)']
    p.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    fin = p.tipo('run_finished')
    assert fin['payload'] == {'run_id': run['id'], 'outcome': 'success'}
    assert p.terminar() is not None and p.proc.returncode == 0
    tipos = [m['type'] for m in p.del_agente()]
    assert tipos[0] == 'hello' and tipos[1] == 'session_started' and tipos[-1] == 'close'
    for esperado in ('tool_start', 'tool_end', 'message_start', 'message_delta', 'message_end', 'usage'):
        assert esperado in tipos
    fin_herramienta = p.del_agente('tool_end')[0]
    assert fin_herramienta['payload']['status'] == 'success'
    texto = ''.join(b['text'] for m in p.del_agente('message_delta') for b in m['payload']['blocks'])
    assert 'Hecho:' in texto
    assert problemas(p.log) == []


def test_un_subagente_es_una_subejecucion_que_termina_antes_que_su_madre(proceso):
    p = proceso('sub')
    p.saludar()
    p.entrada('Lanza un revisor')
    raiz = p.tipo('run_started')
    p.tipo('run_finished')                                  # la del subagente
    p.tipo('run_finished')                                  # la raíz
    p.terminar()
    sub = [m for m in p.del_agente('run_started') if 'parent_run_id' in m['payload']]
    assert len(sub) == 1 and sub[0]['payload']['parent_run_id'] == raiz['id']
    assert sub[0]['payload']['agent'] == {'name': 'explorador', 'description': 'Revisor A'}
    lanzar = next(m for m in p.del_agente('tool_start') if m['payload']['name'] == 'spawn_agent')
    assert sub[0]['payload']['tool_call_id'] == lanzar['id']          # lo lanzó esa herramienta (§4.9)
    cierres = [m['payload']['run_id'] for m in p.del_agente('run_finished')]
    assert cierres == [sub[0]['id'], raiz['id']]
    # Lo que dice el subagente lleva su propio run_id.
    suyos = [m for m in p.del_agente('message_start') if m['payload']['run_id'] == sub[0]['id']]
    assert len(suyos) == 1
    assert problemas(p.log) == []


def test_un_subagente_en_segundo_plano_cierra_su_herramienta_en_el_acto_y_su_raiz_lo_espera(proceso):
    p = proceso('fondo')
    p.saludar()
    p.entrada('Lanza un revisor en paralelo')
    raiz = p.tipo('run_started')
    p.hasta(lambda m: m['type'] == 'run_finished' and m['payload']['run_id'] == raiz['id'])
    p.terminar()
    lanzar = next(m for m in p.del_agente('tool_start') if m['payload']['name'] == 'spawn_agent')
    sub = next(m for m in p.del_agente('run_started') if 'parent_run_id' in m['payload'])
    assert sub['payload']['tool_call_id'] == lanzar['id'] and sub['payload']['parent_run_id'] == raiz['id']
    orden = [(m['type'], m['payload'].get('tool_call_id') or m['payload'].get('run_id')) for m in p.del_agente()
             if m['type'] in ('tool_end', 'run_finished')]
    # `spawn_agent` vuelve en el acto con el id del subagente: su `tool_end` llega antes que el fin del subagente,
    # y la raíz no termina hasta que acaba él.
    assert orden.index(('tool_end', lanzar['id'])) < orden.index(('run_finished', sub['id'])) \
        < orden.index(('run_finished', raiz['id']))
    assert next(m for m in p.del_agente('tool_end') if m['payload']['tool_call_id'] == lanzar['id'])[
        'payload']['status'] == 'success'
    assert problemas(p.log) == []


def test_negar_el_permiso_no_ejecuta_la_orden(proceso, tmp_path):
    p = proceso()
    p.saludar()
    p.entrada('Prueba una orden')
    peticion = p.tipo('approval_request')
    p.enviar('approval_result', {'decision': 'denied'}, reply_to=peticion['id'])
    p.tipo('run_finished')
    p.terminar()
    fin = p.del_agente('tool_end')[0]
    assert fin['payload']['status'] == 'error' and fin['payload']['tool_call_id'] == peticion['payload']['tool_call_id']
    assert problemas(p.log) == []


def test_cerrar_la_entrada_con_un_permiso_pendiente_lo_niega_y_nada_se_ejecuta(proceso):
    p = proceso()
    p.saludar()
    p.entrada('Prueba una orden')
    p.tipo('approval_request')
    p.terminar()                                           # EOF equivale a close del cliente (§5.1)
    assert p.proc.returncode in (0, 130)
    assert not [m for m in p.del_agente('tool_end') if m['payload']['status'] == 'success']
    assert p.del_agente()[-1]['type'] == 'close'
    assert [m['payload']['outcome'] for m in p.del_agente('run_finished')] == ['interrupted']
    assert problemas(p.log) == []


def test_un_close_del_cliente_termina_la_sesion_con_su_close(proceso):
    p = proceso('plain')
    p.saludar()
    p.enviar('close', {'reason': 'adiós'})
    p.tipo('close')
    p.terminar()
    assert p.proc.returncode in (0, 130) and problemas(p.log) == []


def test_un_input_mientras_hay_una_ejecucion_en_curso_se_encola_y_abre_la_siguiente(proceso):
    p = proceso()
    p.saludar()
    uno = p.entrada('Primero')
    peticion = p.tipo('approval_request')
    dos = p.entrada('Segundo')                             # llega con la primera ejecución esperando un permiso
    p.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    primera = p.tipo('run_finished')
    segunda_inicio = p.tipo('run_started')
    assert segunda_inicio['payload'] == {'input_id': dos['id']}      # la segunda sólo empieza al acabar la primera
    p.tipo('run_finished')
    p.terminar()
    inicios = p.del_agente('run_started')
    assert [m['payload']['input_id'] for m in inicios] == [uno['id'], dos['id']]
    assert primera['payload']['run_id'] == inicios[0]['id']
    # Nunca hubo dos ejecuciones raíz a la vez (lo comprueba el verificador).
    assert problemas(p.log) == []


def test_steer_corrige_la_ejecucion_en_curso_sin_abrir_otra(proceso):
    p = proceso()
    p.saludar()
    p.entrada('Prueba una orden')
    run = p.tipo('run_started')
    peticion = p.tipo('approval_request')
    p.enviar('input', {'blocks': [{'type': 'text', 'text': 'mejor usa pytest'}], 'mode': 'steer', 'run_id': run['id']})
    p.enviar('approval_result', {'decision': 'approved', 'scope': 'once'}, reply_to=peticion['id'])
    p.tipo('run_finished')
    p.terminar()
    assert len(p.del_agente('run_started')) == 1
    texto = ''.join(b['text'] for m in p.del_agente('message_delta') for b in m['payload']['blocks'])
    assert 'mejor usa pytest' in texto                      # el modelo la leyó antes de su siguiente respuesta
    assert problemas(p.log) == []


def test_interrupt_corta_la_ejecucion_cierra_la_herramienta_y_la_sesion_sigue(proceso):
    p = proceso()
    p.saludar()
    p.entrada('Prueba una orden')
    run = p.tipo('run_started')
    p.tipo('approval_request')
    p.enviar('interrupt', {'run_id': run['id']})
    fin = p.tipo('run_finished')
    assert fin['payload'] == {'run_id': run['id'], 'outcome': 'interrupted'}
    assert p.del_agente('tool_end')[-1]['payload']['status'] in ('cancelled', 'error')
    # La sesión sigue abierta: otro input abre otra ejecución.
    p.entrada('Otra cosa')
    segunda = p.tipo('run_started')
    assert segunda['id'] != run['id']
    p.tipo('run_finished')
    p.enviar('interrupt', {'run_id': run['id']})            # la primera ya no está en curso
    error = p.tipo('error')
    assert error['payload']['code'] == 'unknown_run'
    p.terminar()
    assert problemas(p.log) == []


def test_un_cliente_que_manda_basura_recibe_errores_y_la_sesion_sigue(proceso):
    p = proceso('plain')
    p.saludar()
    p.proc.stdin.write('no es json\n{"pacai":"0.3"}\n')
    p.proc.stdin.flush()
    assert p.tipo('error')['payload']['code'] == 'invalid_message'
    assert p.tipo('error')['payload']['code'] == 'invalid_message'
    p.entrada('Hola')
    p.tipo('run_finished')
    p.terminar()
    assert p.proc.returncode == 0
    assert problemas(p.log) == []


def test_sin_version_comun_el_proceso_responde_y_termina(proceso):
    p = proceso('plain')
    p.enviar('hello', {'versions': ['9.9']})
    assert p.tipo('error')['payload']['code'] == 'version_incompatible'
    p.terminar()
    assert p.proc.returncode in (0, 1, 130)


def test_la_sesion_se_guarda_con_el_primer_input_como_encargo(proceso, tmp_path):
    p = proceso('plain')
    p.saludar()
    p.entrada('Corrige el test de login')
    p.tipo('run_finished')
    p.terminar()
    task = json.loads(next((tmp_path / 'home').rglob('task.json')).read_text())
    assert task['objective'] == 'Corrige el test de login' and task['status'] == 'completed'


@pytest.mark.parametrize('argumentos', [['--pacai', 'una tarea'], ['--pacai', '--stdio'],
                                        ['--pacai', '--cola-encargos'], ['--pacai', '--bifurcar', 'x']])
def test_pacai_no_se_combina_con_un_encargo_ni_con_stdio(argumentos, capsys):
    with pytest.raises(SystemExit) as salida:
        main(argumentos)
    assert salida.value.code == 2
    assert '--pacai' in capsys.readouterr().err
