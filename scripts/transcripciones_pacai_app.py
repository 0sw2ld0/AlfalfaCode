"""Rehace las transcripciones de `alfalfa-mac/Tests/AlfalfaKitTests/Fixtures/pacai/`.

    python scripts/transcripciones_pacai_app.py [escenario …]   # sin escenario, todos y `traducciones`

Cada escenario abre el motor de verdad dos veces, una con `--stdio` v2 y otra con `--pacai`, con la inferencia
sustituida por un modelo falso que siempre hace lo mismo, y le manda las mismas órdenes de la app: las de v2 como
`AlfalfaCommand.line` y las de PACAI como `PacaiClient`. Guarda lo que pasó en los dos lados. Las pruebas de Swift
(`PacaiTranscriptTests`) vuelven a mandar esas órdenes con el código de la app, comprueban que salen las mismas
líneas y que los dos protocolos dan la misma secuencia de `AlfalfaMessage`: la interfaz ve lo mismo.

Las dos pasadas corren en la misma carpeta (se vacía entre una y otra) para que las rutas coincidan. HOME y
ALFALFA_HOME son temporales: no se lee ni se escribe nada de quien lo ejecuta. `traducciones` no lanza el motor:
pasa por el traductor de `--pacai` un evento v2 de cada cosa que la app lee (`PacaiTranslationTests`).
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
DESTINO = RAIZ / 'alfalfa-mac' / 'Tests' / 'AlfalfaKitTests' / 'Fixtures' / 'pacai'
SESION = 's_' + '0123456789abcdef' * 2

# El modelo falso decide por palabras del último mensaje del usuario, y sólo si aún no hay resultados de
# herramientas desde ese mensaje: así cada turno pide sus herramientas una vez y luego contesta.
MODELO = r'''
import json, sys
from unittest.mock import patch
from alfalfa.terminal import main
from alfalfa.proveedores.nativo.runtime import NativeRuntime

def llamada(nombre, **args):
    return dict(function=dict(name=nombre, arguments=args))

def herramientas(texto):
    if 'orden-red' in texto:
        # Una dirección por mensaje: lo aprobado para la sesión no tapa la pregunta del turno siguiente.
        # `git ls-remote` ofrece «no volver a preguntar» (`git ls-remote …`).
        return [llamada('run_command', argv=['git', 'ls-remote', 'https://ejemplo.com/' + '-'.join(texto.split())],
                        purpose='Ver las ramas remotas', needs_network=True)]
    if 'orden-lenta' in texto:
        return [llamada('run_command', argv=['sleep', '3'], purpose='Esperar')]
    if 'subagente' in texto:
        return [llamada('spawn_agent', message='Revisa el README', description='Revisor', agent_type='explorador')]
    if 'navega' in texto or 'lee la página' in texto:
        return [llamada('browser_read', mode='text')]
    if 'planifica' in texto:
        return [llamada('update_plan', explanation='Voy por partes', plan=[
                    dict(step='Escribir la nota', status='in_progress'), dict(step='Repasarla', status='pending')]),
                llamada('write_file', path='nota.txt', content='hola\n', expected_sha256='NEW')]
    return []

async def inference(self, client, endpoint, payload, **kwargs):
    if endpoint == '/api/show':
        return {'capabilities': ['tools']}
    mensajes = payload['messages']
    usuario = [i for i, m in enumerate(mensajes) if m['role'] == 'user']
    ultimo = usuario[-1] if usuario else 0
    texto = str(mensajes[ultimo].get('content') or '') if usuario else ''
    if self.task.get('subagente_de'):
        return dict(message=dict(role='assistant', content='Informe del subagente', tool_calls=[]), done=True,
                    done_reason='stop', prompt_eval_count=2, eval_count=1)
    calls = [] if any(m['role'] == 'tool' for m in mensajes[ultimo:]) else herramientas(texto)
    respuesta = 'Hecho: ' + texto.strip().splitlines()[-1][:60] if not calls else ''
    return dict(message=dict(role='assistant', content=respuesta, tool_calls=calls), done=True,
                done_reason='stop', prompt_eval_count=2, eval_count=1)

with patch.object(NativeRuntime, 'request', inference):
    sys.exit(main(sys.argv[1:]))
'''


# -- las órdenes de la app, como las escribe cada protocolo -------------------------------------------------------
def linea_v2(orden: dict, ref: str | None) -> dict:
    """`AlfalfaCommand.line` (AlfalfaCommand.swift)."""
    tipo = orden['tipo']
    if tipo == 'permission':
        o = {'tipo': 'permiso', 'id': ref, 'aprobado': orden['approved']}
        if orden['approved'] and orden.get('site'):
            o['sitio'] = orden['site']
        elif orden['approved'] and orden.get('always'):
            o['siempre'] = True
        elif orden['approved'] and orden.get('session'):
            o['sesion'] = True
        return o
    if tipo == 'message':
        o = {'tipo': 'mensaje', 'texto': orden['text']}
        imagenes = [r for r in orden.get('images', []) if r.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]
        archivos = [r for r in orden.get('images', []) if r not in imagenes]
        if imagenes:
            o['imagenes'] = imagenes
        if archivos:
            o['archivos'] = archivos
        if orden.get('id'):
            o['id'] = orden['id']
        return o
    if tipo == 'cancel':
        return {'tipo': 'cancelar'}
    if tipo == 'sendNow':
        return {'tipo': 'enviar_ahora', 'texto': orden['text'], 'id': orden['id']}
    if tipo == 'redirect':
        return {'tipo': 'redirigir'}
    if tipo == 'unqueue':
        return {'tipo': 'quitar_de_cola', 'id': orden['id']}
    if tipo == 'compact':
        return {'tipo': 'compactar'}
    if tipo == 'contextMode':
        return {'tipo': 'contexto', 'modo': orden['mode']}
    if tipo == 'settings':
        o = {'tipo': 'ajustes'}
        for clave, campo in (('model', 'modelo'), ('effort', 'esfuerzo'), ('mode', 'modo')):
            if orden.get(clave):
                o[campo] = orden[clave]
        return o
    if tipo == 'budget':
        return {'tipo': 'presupuesto', 'sesion': orden.get('session'), 'mes': orden.get('month')}
    if tipo == 'keepAwake':
        return {'tipo': 'mantener_despierto', 'activo': orden['on']}
    if tipo == 'browserReply':
        return dict(orden['body'], tipo='navegador_respuesta', id=ref)
    if tipo == 'taskQueue':
        o = {'tipo': 'encargos', 'accion': orden['action']}
        if orden['action'] == 'preparar':
            o.update(encargos=orden['tasks'], modo=orden['mode'], paralelos=orden['parallelism'])
        return o
    raise ValueError(tipo)


def _navegador_en_ingles(cuerpo):
    """La respuesta del navegador con los nombres de PACAI, como `PacaiBrowser.reply`."""
    from alfalfa.pacai.nombres import NAVEGADOR_CAMPOS, NAVEGADOR_VALORES, _traducir
    return _traducir(cuerpo, NAVEGADOR_CAMPOS, NAVEGADOR_VALORES)


class ClientePacai:
    """Lo que hace `PacaiClient` (PacaiClient+Commands.swift) con las órdenes de los escenarios."""

    def __init__(self):
        self.seq = 0
        self.raiz = None
        self.esperando = False
        self.retenidas = []
        self.herramientas = set()

    def mensaje(self, tipo, payload, ident=None, reply_to=None):
        self.seq += 1
        m = dict(pacai='0.3', id=ident or 'c_%016x' % self.seq, type=tipo, session=SESION, sender='client',
                 seq=self.seq, time='2026-11-16T10:00:00.000Z', payload=payload)
        if reply_to:
            m['reply_to'] = reply_to
        return m

    @staticmethod
    def bloques(texto, adjuntos):
        bloques = [{'type': 'text', 'text': texto}] if texto else []
        for ruta in adjuntos:
            imagen = ruta.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
            bloques.append({'type': 'file', 'name': Path(ruta).name,
                            'media_type': 'image/png' if imagen else 'text/plain', 'url': Path(ruta).as_uri()})
        return bloques

    def abrir(self, apertura):
        salida = [self.mensaje('hello', {'versions': ['0.3'], 'profiles': ['work'], 'extensions': ['alfalfa'],
                                         'client_tools': [{'name': 'browser',
                                                           'description': 'El navegador propio de la app de Mac',
                                                           'input_schema': {'type': 'object'}}]})]
        if apertura.get('task') is not None or apertura.get('attachments'):
            self.esperando = True
            salida.append(self.mensaje('input', {'blocks': self.bloques(apertura.get('task') or '',
                                                                        apertura.get('attachments', [])),
                                                 'alfalfa': {'echo': False}}))
        elif apertura.get('continue'):
            self.esperando = True
            salida.append(self.mensaje('input', {'blocks': [], 'alfalfa': {'continue': True}}))
        return salida

    def recibir(self, m):
        salida = []
        if m['type'] == 'run_started' and 'parent_run_id' not in m['payload']:
            self.raiz, self.esperando = m['id'], False
            retenidas, self.retenidas = self.retenidas, []
            for orden, ref in retenidas:
                salida += self.orden(orden, ref)
        elif m['type'] == 'run_finished' and m['payload']['run_id'] == self.raiz:
            self.raiz = None
        elif m['type'] == 'tool_start' and m['payload'].get('side') == 'client':
            self.herramientas.add(m['id'])
        elif m['type'] == 'tool_end':
            self.herramientas.discard(m['payload']['tool_call_id'])
        elif m['type'] == 'error' and self.esperando:
            self.esperando = False
            retenidas, self.retenidas = self.retenidas, []
            for orden, ref in retenidas:
                salida += self.orden(orden, ref)
        return salida

    def orden(self, orden, ref):
        tipo = orden['tipo']
        if tipo == 'message':
            if self.esperando:
                self.retenidas.append((orden, ref))
                return []
            payload = {'blocks': self.bloques(orden['text'], orden.get('images', []))}
            if self.raiz:
                payload.update(mode='steer', run_id=self.raiz, alfalfa={'redirect': False})
            else:
                self.esperando = True
            return [self.mensaje('input', payload, ident=orden.get('id'))]
        if tipo == 'permission':
            p = {'decision': 'approved' if orden['approved'] else 'denied'}
            if orden['approved']:
                site, session, always = orden.get('site'), orden.get('session'), orden.get('always')
                p['scope'] = 'session' if site == 'sesion' or (site is None and session and not always) else 'once'
                if always:
                    p['alfalfa'] = {'scope': 'always'}
                if site == 'proyecto':
                    p['alfalfa'] = {'scope': 'project'}
            return [self.mensaje('approval_result', p, reply_to=ref)]
        if tipo == 'browserReply':
            if ref not in self.herramientas:
                return []
            self.herramientas.discard(ref)
            cuerpo = orden['body']
            p = {'tool_call_id': ref, 'status': 'success' if cuerpo.get('ok') else 'error',
                 'output': [{'type': 'structured', 'schema': 'alfalfa.browser_reply',
                             'data': _navegador_en_ingles(cuerpo)}]}
            if cuerpo.get('error'):
                p['summary'] = cuerpo['error'][:300]
            return [self.mensaje('tool_end', p, reply_to=ref)]
        if tipo == 'cancel':
            return [self.mensaje('close', {})]
        if tipo == 'taskQueue':
            accion = {'preparar': 'prepare', 'iniciar': 'start', 'pausar': 'pause', 'detener': 'stop',
                      'cancelar': 'cancel'}[orden['action']]
            if accion == 'start':
                return [self.mensaje('input', {'blocks': [{'type': 'structured', 'schema': 'alfalfa.jobs_command',
                                                           'data': {'action': 'start'}}]})]
            payload = {'action': accion}
            if accion == 'prepare':
                payload.update(jobs=orden['tasks'], mode={'serie': 'serial', 'paralelo': 'parallel'}[orden['mode']],
                               parallel=orden['parallelism'])
            return [self.mensaje('alfalfa.jobs_command', payload)]
        sencillas = {'sendNow': ('send_now', lambda o: {'id': o['id']}),
                     'redirect': ('redirect', lambda o: {}),
                     'unqueue': ('dequeue', lambda o: {'id': o['id']}),
                     'compact': ('compact', lambda o: {}),
                     'contextMode': ('set_context_mode',
                                     lambda o: {'mode': {'ahorro': 'saving', 'completo': 'full'}[o['mode']]}),
                     'settings': ('set_settings', lambda o: {k: ({'ediciones': 'accept_edits', 'omitir':
                                                                  'bypass_permissions'}.get(v, v) if k == 'mode'
                                                                 else v) for k, v in o.items()
                                                             if k in ('model', 'effort', 'mode') and v}),
                     'budget': ('set_budget', lambda o: {'session': o.get('session'), 'month': o.get('month')}),
                     'keepAwake': ('set_keep_awake', lambda o: {'active': o['on']})}
        nombre, payload = sencillas[tipo]
        return [self.mensaje('alfalfa.' + nombre, payload(orden))]


# -- una pasada del motor -----------------------------------------------------------------------------------------
class Motor:
    def __init__(self, carpeta: Path, casa: Path, argumentos: list[str]):
        env = {k: v for k, v in os.environ.items() if not k.startswith('ALFALFA_')}
        env.update(PYTHONPATH=str(RAIZ), ALFALFA_HOME=str(casa / 'alfalfa'), HOME=str(casa),
                   ALFALFA_INSTANTANEAS='0')
        self.proc = subprocess.Popen([sys.executable, '-c', MODELO, *argumentos], cwd=carpeta, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
        self.cola: queue.Queue = queue.Queue()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for linea in self.proc.stdout:
            self.cola.put(linea)
        self.cola.put(None)

    def escribir(self, mensajes):
        for m in mensajes:
            self.proc.stdin.write(json.dumps(m, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()


class Pasada:
    """Un escenario contra un motor: registra lo recibido y lo enviado, en orden."""

    def __init__(self, protocolo: str, carpeta: Path, casa: Path, apertura: dict, extra: list[str]):
        self.protocolo, self.pasos, self.vistos = protocolo, [], []
        self.cliente = ClientePacai() if protocolo == 'pacai' else None
        argumentos = ['--' + protocolo, '--navegador', *extra]
        if protocolo == 'pacai' and '--esperar' in argumentos:
            argumentos.remove('--esperar')
        if protocolo == 'stdio' and apertura.get('task') is not None:
            for ruta in apertura.get('attachments', []):
                argumentos += ['--imagen' if ruta.endswith('.png') else '--archivo', ruta]
            argumentos += ['--', apertura['task']]
        self.motor = Motor(carpeta, casa, argumentos)
        if self.cliente:
            enviado = self.cliente.abrir(apertura)
            self.pasos.append({'apertura': apertura, 'enviado': enviado})
            self.motor.escribir(enviado)
        self.terminado = False

    def _uno(self, segundos):
        linea = self.motor.cola.get(timeout=segundos)
        if linea is None:
            self.terminado = True
            return None
        m = json.loads(linea)
        paso = {'recibido': m}
        if self.cliente:
            enviado = self.cliente.recibir(m)
            if enviado:
                paso['enviado'] = enviado
                self.motor.escribir(enviado)
        self.pasos.append(paso)
        self.vistos.append(m)
        return m

    def esperar(self, condicion, segundos=40):
        limite = time.monotonic() + segundos
        while True:
            if self.terminado:
                raise AssertionError(self.protocolo + ': el motor terminó esperando ' + condicion)
            m = self._uno(max(0.1, limite - time.monotonic()))
            if m is not None and cumple(self.protocolo, condicion, m, self.vistos):
                return m

    def calma(self, segundos=0.6):
        """Lee lo que llegue durante `segundos` sin esperar nada concreto."""
        limite = time.monotonic() + segundos
        while not self.terminado and time.monotonic() < limite:
            try:
                self._uno(max(0.05, limite - time.monotonic()))
            except queue.Empty:
                return

    def orden(self, orden):
        orden = _rutas(orden, self.escenario_carpeta) if hasattr(self, 'escenario_carpeta') else orden
        ref = referencia(self.protocolo, orden, self.vistos)
        if self.cliente:
            enviado = self.cliente.orden(orden, ref)
        else:
            enviado = [linea_v2(orden, ref)]
        self.pasos.append({'orden': orden, 'enviado': enviado})
        self.motor.escribir(enviado)

    def cerrar(self):
        if not self.terminado:
            self.motor.proc.stdin.close()
            while not self.terminado:
                self._uno(40)
        self.motor.proc.wait(timeout=40)
        return {'pasos': self.pasos, 'salida': self.motor.proc.returncode}


def referencia(protocolo, orden, vistos):
    """El id al que contesta una orden: el último permiso o la última petición del navegador."""
    if orden['tipo'] == 'permission':
        tipo = 'permiso' if protocolo == 'stdio' else 'approval_request'
    elif orden['tipo'] == 'browserReply':
        tipo = 'navegador' if protocolo == 'stdio' else 'tool_start'
    else:
        return None
    for m in reversed(vistos):
        if protocolo == 'stdio' and m.get('tipo') == tipo:
            return m['id']
        if protocolo == 'pacai' and m.get('type') == tipo and (tipo != 'tool_start'
                                                                or m['payload'].get('side') == 'client'):
            return m['id']
    raise AssertionError('nada a lo que contestar: ' + orden['tipo'])


def cumple(protocolo, condicion, m, vistos):
    v2 = protocolo == 'stdio'
    nombre, _, valor = condicion.partition(':')
    if nombre == 'permiso':
        return m.get('tipo') == 'permiso' if v2 else m.get('type') == 'approval_request'
    if nombre == 'turno':
        if v2:
            return m.get('tipo') == 'turno_terminado'
        raices = {x['id'] for x in vistos if x.get('type') == 'run_started' and 'input_id' in x['payload']}
        return m.get('type') == 'run_finished' and m['payload']['run_id'] in raices
    if nombre == 'navegador':
        if v2:
            return m.get('tipo') == 'navegador' and m.get('accion') != 'cancelar_relevo'
        return m.get('type') == 'tool_start' and m['payload'].get('side') == 'client'
    if nombre == 'inicio':
        if v2:
            datos = m.get('herramienta') or {}
            return m.get('tipo') == 'evento' and datos.get('fase') == 'inicio' and datos.get('nombre') == valor
        return m.get('type') == 'tool_start' and m['payload'].get('name') == valor
    if nombre == 'eco':
        return (m.get('tipo') == 'evento' and m.get('rol') == 'user') if v2 else m.get('type') == 'alfalfa.user_message'
    if nombre == 'quitado':
        return m.get('tipo') == 'quitado_de_cola' if v2 else m.get('type') == 'alfalfa.dequeued'
    if nombre == 'compactada':
        datos = (m.get('compactacion') if v2 else (m.get('payload') or {}).get('compaction')) or {}
        return datos.get('estado' if v2 else 'status') in (('hecha',) if v2 else ('done',))
    if nombre == 'presupuesto':
        return m.get('tipo') == 'presupuesto' if v2 else m.get('type') == 'alfalfa.budget'
    if nombre == 'sesion':
        return m.get('tipo') == 'sesion' if v2 else m.get('type') == 'alfalfa.session'
    if nombre == 'cola':
        if v2:
            return m.get('tipo') == 'encargos' and (m.get('cola') or {}).get('estado') == valor
        estado = {'lista': 'ready', 'completada': 'completed'}.get(valor, valor)
        return m.get('type') == 'alfalfa.jobs' and ((m.get('payload') or {}).get('queue') or {}).get('status') == estado
    if nombre == 'fin':
        return m.get('tipo') == 'fin' if v2 else m.get('type') == 'close'
    raise ValueError(condicion)


# -- los escenarios -----------------------------------------------------------------------------------------------
def permisos(p):
    """Una vez, para la sesión, «no volver a preguntar» y negar."""
    p.esperar('permiso')
    p.orden({'tipo': 'permission', 'approved': True})
    p.esperar('turno')
    respuestas = (('otra vez orden-red', {'approved': True, 'session': True}),
                  ('y una más orden-red', {'approved': False}),
                  ('la última orden-red', {'approved': True, 'always': True}))
    for n, (texto, respuesta) in enumerate(respuestas):
        p.orden({'tipo': 'message', 'text': texto, 'id': 'cola-permiso-000' + str(n)})
        p.esperar('permiso')
        p.orden(dict(respuesta, tipo='permission'))
        p.esperar('turno')


def interrupcion(p):
    """«Detener» con un permiso pendiente: la sesión se cierra y queda para reanudar."""
    p.esperar('permiso')
    p.orden({'tipo': 'cancel'})


def cola(p):
    """Un mensaje que entra en el turno en marcha, uno que se quita y otro que se envía ya."""
    p.esperar('inicio:run_command')
    p.orden({'tipo': 'message', 'text': 'y luego di adiós', 'id': 'cola-uno-0001'})
    p.esperar('eco')
    p.esperar('turno')
    p.orden({'tipo': 'message', 'text': 'otra orden-lenta', 'id': 'cola-dos-0002'})
    p.esperar('inicio:run_command')
    p.orden({'tipo': 'message', 'text': 'éste se queda', 'id': 'cola-tres-0003'})
    p.orden({'tipo': 'message', 'text': 'éste se quita', 'id': 'cola-cuatro-0004'})
    p.orden({'tipo': 'unqueue', 'id': 'cola-cuatro-0004'})
    p.esperar('quitado')
    p.orden({'tipo': 'sendNow', 'text': 'éste se queda', 'id': 'cola-tres-0003'})
    p.esperar('turno')
    p.esperar('turno')


def redirigir(p):
    """«Redirigir»: el modelo lee ya el mensaje en cola."""
    p.esperar('inicio:run_command')
    p.orden({'tipo': 'message', 'text': 'mira esto ya', 'id': 'cola-cinco-0005'})
    p.orden({'tipo': 'redirect'})
    p.esperar('turno')


def subagentes(p):
    p.esperar('turno')
    p.calma(1.0)


def navegador(p):
    """El navegador de la app: un sitio nuevo pide permiso (para todo el proyecto), el motor reanuda la visita y,
    en el turno siguiente, la página se lee."""
    p.esperar('navegador')
    p.orden({'tipo': 'browserReply', 'body': {'ok': False, 'permiso': {'tipo': 'sitio', 'url': 'https://ejemplo.com',
                                                                       'token': 'tok-1', 'iniciada': False},
                                              'resultado': {}}})
    p.esperar('permiso')
    p.orden({'tipo': 'permission', 'approved': True, 'site': 'proyecto'})
    p.esperar('navegador')                                     # reanudar la visita
    p.orden({'tipo': 'browserReply', 'body': {'ok': True, 'resultado': {}}})
    p.esperar('turno')
    p.orden({'tipo': 'message', 'text': 'lee la página otra vez', 'id': 'cola-siete-0007'})
    p.esperar('navegador')
    p.orden({'tipo': 'browserReply', 'body': {'ok': True, 'resultado': {'url': 'https://ejemplo.com',
                                                                        'titulo': 'Ejemplo',
                                                                        'texto': 'Hola desde la página'}}})
    p.esperar('turno')


def plan_y_cambios(p):
    p.esperar('turno')


def compactacion(p):
    p.esperar('turno')
    p.orden({'tipo': 'compact'})
    p.esperar('compactada')
    p.calma()


def ajustes(p):
    """Modelo, esfuerzo y modo para el turno siguiente, presupuesto y contexto completo."""
    p.esperar('turno')
    p.orden({'tipo': 'budget', 'session': 5, 'month': 50})
    p.esperar('presupuesto')
    p.orden({'tipo': 'settings', 'model': 'glm-5.3-flash:cloud', 'effort': 'high', 'mode': 'ediciones'})
    p.orden({'tipo': 'contextMode', 'mode': 'completo'})
    p.orden({'tipo': 'keepAwake', 'on': False})
    p.calma()
    p.orden({'tipo': 'message', 'text': 'sigue', 'id': 'cola-seis-0006'})
    p.esperar('turno')


def adjuntos(p):
    """El encargo con una imagen y un archivo, y luego un mensaje con otra imagen. El modelo falso no ve imágenes:
    el motor lo dice igual con los dos protocolos."""
    p.esperar('turno')
    p.orden({'tipo': 'message', 'text': 'y esta otra', 'id': 'cola-ocho-0008', 'images': ['/proyecto/otra.png']})
    p.calma(3)


def reanudar(p):
    """«Reanudar» una sesión interrumpida sin escribir nada: sigue con el encargo."""
    p.esperar('turno')


def cola_de_encargos(p):
    """Una cola de encargos: se prepara, se inicia y su encargo pide un permiso a la sesión de la cola."""
    p.orden({'tipo': 'taskQueue', 'action': 'preparar', 'tasks': ['Haz la orden-red de la cola'], 'mode': 'serie',
             'parallelism': 1})
    p.esperar('cola:lista')
    p.orden({'tipo': 'taskQueue', 'action': 'iniciar'})
    p.esperar('permiso')
    p.orden({'tipo': 'permission', 'approved': True})
    p.esperar('cola:completada')
    p.calma()


def con_git(carpeta, casa, protocolo):
    subprocess.run(['git', 'init', '-q'], cwd=carpeta, check=True)
    subprocess.run(['git', '-c', 'user.name=Prueba', '-c', 'user.email=prueba@example.com', 'commit', '-q',
                    '--allow-empty', '-m', 'inicio'], cwd=carpeta, check=True)
    return []


def con_adjuntos(carpeta, casa, protocolo):
    for nombre in ('captura.png', 'otra.png'):
        (carpeta / nombre).write_bytes(b'png')
    (carpeta / 'notas.txt').write_text('notas\n')
    return []


def interrumpida(carpeta, casa, protocolo):
    """Una sesión que se cortó con un permiso pendiente: la que se reanuda."""
    p = Pasada('stdio', carpeta, casa, {'task': 'Prueba orden-red'}, [])
    try:
        sesion = p.esperar('sesion')['id']
        p.esperar('permiso')
        p.orden({'tipo': 'cancel'})
        p.cerrar()
    finally:
        if p.motor.proc.poll() is None:
            p.motor.proc.kill()
    return ['--reanudar', sesion]


ESCENARIOS = {
    'permisos': (permisos, {'task': 'Prueba orden-red'}, None),
    'interrupcion': (interrupcion, {'task': 'Prueba orden-red'}, None),
    'cola': (cola, {'task': 'Haz una orden-lenta'}, None),
    'redirigir': (redirigir, {'task': 'Haz una orden-lenta'}, None),
    'subagentes': (subagentes, {'task': 'Lanza un subagente'}, None),
    'navegador': (navegador, {'task': 'navega por ejemplo.com'}, None),
    'plan_y_cambios': (plan_y_cambios, {'task': 'planifica y escribe la nota'}, con_git),
    'compactacion': (compactacion, {'task': 'Hola'}, None),
    'ajustes': (ajustes, {'task': 'Hola'}, None),
    'adjuntos': (adjuntos, {'task': 'Mira estos adjuntos', 'attachments': ['/proyecto/captura.png',
                                                                          '/proyecto/notas.txt']}, con_adjuntos),
    'reanudar': (reanudar, {'continue': True}, interrumpida),
    'cola_de_encargos': (cola_de_encargos, {}, lambda carpeta, casa, protocolo: ['--cola-encargos']),
}


def _rutas(valor, carpeta):
    """Las rutas `/proyecto/…` del escenario, en la carpeta de verdad."""
    return json.loads(json.dumps(valor).replace('/proyecto', str(carpeta)))


def grabar(nombre: str) -> dict:
    escenario, apertura, preparar = ESCENARIOS[nombre]
    base = Path(tempfile.mkdtemp(prefix='pacai-app-')).resolve()
    carpeta, casa = base / 'proyecto', base / 'casa'
    resultado = {'escenario': nombre}
    try:
        for protocolo in ('stdio', 'pacai'):
            for ruta in (carpeta, casa):
                shutil.rmtree(ruta, ignore_errors=True)
                ruta.mkdir(parents=True)
            (carpeta / 'README.md').write_text('Proyecto de prueba\n')
            extra = preparar(carpeta, casa, protocolo) if preparar else []
            p = Pasada(protocolo, carpeta, casa, _rutas(apertura, carpeta), extra)
            p.escenario_carpeta = carpeta
            try:
                escenario(p)
                resultado[protocolo] = p.cerrar()
            except Exception:
                # Lo último que pasó, para ver dónde se quedó el escenario.
                for paso in p.pasos[-int(os.environ.get("PASOS", "25")):]:
                    print(protocolo, json.dumps(paso, ensure_ascii=False)[:300], file=sys.stderr)
                raise
            finally:
                if p.motor.proc.poll() is None:
                    p.motor.proc.kill()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    texto = json.dumps(resultado, ensure_ascii=False, indent=1)
    return json.loads(texto.replace(str(carpeta), '/proyecto').replace(str(casa), '/casa'))


# -- traducciones sueltas ----------------------------------------------------------------------------------------
# Eventos v2 de todo lo que la app lee en una sesión, con los campos y los valores enumerados de verdad, también los
# que los escenarios no alcanzan (revisión de código, aprendizaje, cuota, tarifa, MCP, control de apps…). Cada uno
# pasa por el traductor de `--pacai` con una ejecución abierta; la prueba de Swift comprueba que el cliente de la
# app los devuelve iguales que el decodificador v2.
HERRAMIENTA_INICIO = {'nombre': 'run_command', 'fase': 'inicio', 'descripcion': 'Pasar las pruebas',
                      'en_curso': 'Pasando las pruebas', 'orden': 'pytest -q', 'cwd': 'tests'}
CIERRE = {'resumen': 'Listo el login.', 'pregunta': '¿Lo subo?', 'opciones': ['Sí, súbelo', 'Todavía no'],
          'sugerencia': 'Sube la rama', 'sesiones': [{'titulo': 'Revisar el CSS', 'encargo': 'Revisa el CSS',
                                                      'motivo': 'Se ve mal en móvil', 'modelo': 'glm-5.3-flash:cloud'}],
          'revision_codigo': {'turno': 't9', 'estado': 'disponible', 'autor': 'deepseek-v4.1-flash:cloud',
                              'hallazgos': []}}
CORPUS = [
    ('sesion', dict(id='abc123def4567890', cwd='/proyecto', modelo='glm-5.3-flash:cloud', esfuerzo='high',
                    modo='ediciones', contexto='completo', tokens=1200, coste=0.25, continuada_de='0123456789abcdef')),
    ('sesion', dict(id='abc123def4567890', cwd='/proyecto', modelo='m', esfuerzo='low', modo='omitir',
                    contexto='ahorro', tokens=0)),
    ('evento', dict(rol='tool', texto="Inicio run_command · {'argv': ['pytest', '-q']}", hora=1.5, turno='t9',
                    herramienta=HERRAMIENTA_INICIO)),
    ('evento', dict(rol='tool', texto='run_command\n{"exit_code": 1}', hora=2.5, turno='t9',
                    herramienta={'nombre': 'run_command', 'fase': 'fin', 'codigo': 1, 'error': True,
                                 'clasificacion': {'disposition': 'failure', 'status': 'failed'},
                                 'diagnosticos': {'errores': 2, 'avisos': 1, 'incompleto': True}})),
    ('evento', dict(rol='tool', texto="Inicio apply_patch · {'input': '…'}", hora=3.0, turno='t9',
                    herramienta={'nombre': 'apply_patch', 'fase': 'inicio', 'descripcion': 'Editar 2 archivos',
                                 'objetivos': ['a.py', 'b.py']})),
    ('evento', dict(rol='tool', texto='apply_patch\n{"ok": true}', hora=3.5, turno='t9',
                    herramienta={'nombre': 'apply_patch', 'fase': 'fin'})),
    ('evento', dict(rol='agent', texto='Corregí el login.', hora=4.0, turno='t9')),
    ('evento', dict(rol='system', texto='Hooks · formateado', hora=4.5, turno='t9')),
    ('evento', dict(rol='system', texto='Compactando…', hora=5.0, compactacion={'estado': 'en_curso',
                                                                                 'motivo': 'manual'})),
    ('evento', dict(rol='system', texto='Conversación compactada', hora=5.5, turno='t9',
                    compactacion={'estado': 'hecha', 'motivo': 'auto', 'antes': 90000, 'despues': 4000,
                                  'resumen': 'Lo hecho hasta ahora', 'instrucciones': 'Céntrate en el login'})),
    ('evento', dict(rol='system', texto='No se pudo compactar', hora=6.0, compactacion={'estado': 'fallida',
                                                                                        'motivo': 'umbral'})),
    ('evento', dict(rol='system', texto='Pasado 200K cobra el doble', hora=6.5,
                    tarifa={'desde': 200000, 'entrada': 2, 'salida': 1.5, 'modo': 'ahorro', 'modelo': 'Haiku',
                            'estimado': 160000, 'faltan': 30000})),
    ('contexto', dict(tokens=28400, limite=262144, desglose={'sistema': 10, 'herramientas': 20, 'mcp': 5,
                                                             'mensajes': 30, 'memoria': 4, 'instrucciones_mcp': 2,
                                                             'complementos': 1},
                      estimado=30000, umbral=200000, memoria=[{'ruta': 'AGENTS.md', 'tokens': 4}],
                      tarifa={'desde': 200000, 'entrada': 2, 'salida': 1.5, 'modo': 'completo'},
                      herramientas_mcp=300, herramientas_mcp_cargadas=2, herramientas_mcp_base=40,
                      herramientas_mcp_metodo='bytes/4', herramientas_mcp_exacto=False,
                      herramientas_mcp_lista=[{'nombre': 'mcp__notas__guardar', 'tokens': 150}],
                      herramientas_mcp_diferidas=7, herramientas_mcp_diferidas_tokens=900)),
    ('contexto', dict(tokens=1, limite=0)),
    ('uso_sesion', dict(tokens=5000, coste=0.125)),
    ('plan', dict(pasos=[{'paso': 'Leer', 'estado': 'completed'}, {'paso': 'Probar', 'estado': 'in_progress'},
                         {'paso': '', 'estado': 'pending'}], explicacion='Voy por partes')),
    ('procesos', dict(procesos=[dict(id='p1', argv=['npm', 'run', 'dev'], proposito='Servidor', desde=10.0,
                                     vivo=True, codigo=None, sin_sandbox=True, detenido=False,
                                     ultimas=['listo en :3000'])])),
    ('subagentes', dict(subagentes=[dict(id='a1', nombre='Revisor', tipo='explorador', estado=estado, modelo='m',
                                         desde=1.0, hasta=2.0, actividad='leyendo', pasos=3, informe='ok',
                                         error='') for estado in ('iniciando', 'trabajando', 'terminado', 'fallido',
                                                                  'cerrado', 'interrumpido')])),
    ('presupuesto', dict(sesion={'gastado': 4.2, 'tope': 5}, mes={'gastado': 20.0, 'tope': 50, 'mes': '2026-11'},
                         aviso='sesion', agotado=None, cache={'leidos': 900, 'escritos': 100, 'sin_cache': 0.5,
                                                              'con_cache': 0.2})),
    ('presupuesto', dict(sesion={'gastado': 1.0, 'tope': None}, mes={'gastado': 50.0, 'tope': 50},
                         aviso='mes', agotado='mes')),
    ('cuota', dict(servicio='alfalfa', cuotas=[dict(proveedor='ollama', nombre='Modelos de Ollama', usado_pct=15,
                                                   renueva='2026-12-01', dia={'usado_pct': 90,
                                                                              'renueva': '2026-11-17T00:00:00Z'})])),
    ('despierto', dict(activo=True)),
    ('preparando', dict(texto='Preparando deshacer… (8306 archivos, 1,2 GB)')),
    ('preparando', dict()),
    ('quitado_de_cola', dict(id='cola-x', quitado=False, motivo='entregado')),
    ('quitado_de_cola', dict(id='cola-y', quitado=True)),
    ('revision_codigo', dict(revision=dict(turno='t9', estado='terminada', autor='deepseek-v4.1-flash:cloud',
                                           modelo='glm-5.3-flash:cloud', resumen='Dos cosas', motivo=None,
                                           hallazgos=[dict(ruta='a.py', linea=3, prioridad=1, titulo='Falta un None',
                                                           detalle='Puede fallar')], omitidos=['c.bin'], coste=0.01,
                                           coste_estimado=True, tokens_sesion=90, coste_sesion=0.3))),
    ('revision_codigo', dict(revision=dict(turno='t9', estado='revisando', autor='a'))),
    ('revision_codigo', dict(revision=dict(turno='t9', estado='sin_presupuesto', autor='a'))),
    ('encargos', dict(cola=dict(id='q1', estado='ejecutando', modo='paralelo', paralelos=2, permisos='ediciones',
                                tokens_sesion=50, coste_sesion=0.1, encargos=[
                                    dict(id='e1', texto='uno', titulo='uno', estado='completed', resumen='Hecho',
                                         sesion='s1', carpeta='/w/e1', rama='cola/e1',
                                         limpieza={'estado': 'rama_conservada', 'motivo': 'tiene commits'}),
                                    dict(id='e2', texto='dos', titulo='dos', estado='ejecutando',
                                         actividad='Leyendo'),
                                    dict(id='e3', texto='tres', titulo='tres', estado='cancelado'),
                                    dict(id='e4', texto='cuatro', titulo='cuatro', estado='interrumpido'),
                                    dict(id='e5', texto='cinco', titulo='cinco', estado='pendiente')]))),
    ('encargos', dict(cola=dict(id='q1', estado='completada', modo='serie', paralelos=1,
                                encargos=[dict(id='e1', texto='uno', titulo='uno', estado='failed',
                                               limpieza={'estado': 'quitado'})], resumen='Resumen'))),
    ('aprendizaje_propuesto', dict(id='ap1', clase='habilidad', almacen='proyecto', accion='crear',
                                   alcance='proyecto', nombre='pruebas-login', motivo='Lo repetiste tres veces',
                                   diff='+ paso 1', sesion='s1', turno='t9', creada=1.0)),
    ('aprendizaje_propuesto', dict(id='ap2', clase='memoria', almacen='usuario', motivo='Te gusta así',
                                   diff='+ usa tabs')),
    ('aprendizaje_resuelto', dict(id='ap1', aplicado=True, descartada=False, texto='Guardada')),
    ('aprendizaje_resuelto', dict(id='ap2', aplicado=False, descartada=True, texto='Descartada')),
    ('sesion_cambiada', dict(id='fedcba9876543210', continuada_de='abc123def4567890')),
    ('error', dict(texto='El modelo m no ve imágenes.', motivo='sin_vision',
                   rechazado={'texto': 'Mira', 'imagenes': ['/proyecto/a.png'], 'archivos': ['/proyecto/b.txt']},
                   con_vision=[{'modelo': 'gemma4:31b-cloud', 'nombre': 'Gemma 4'}])),
    ('error', dict(texto='No se pudo iniciar o ejecutar la sesión.')),
    ('permiso', dict(argv=['rm', '-rf', 'build'], proposito='Limpiar', motivo='peligroso',
                     motivos=['peligroso', 'red'], justificacion='Hace falta', regla='rm con opción de fuerza',
                     intento='i1', reintento_de='i0', red=True, sin_sandbox='Seatbelt lo bloqueó')),
    ('permiso', dict(argv=['mcp__notas__guardar'], proposito='Guardar la nota', motivo='mcp',
                     mcp={'servidor': 'notas', 'herramienta': 'guardar', 'nombre': 'mcp__notas__guardar',
                          'llamada': 'k1', 'argumentos': {'ruta': 'a.md', 'texto': 'hola', 'modo': 'ahorro'}})),
    ('permiso', dict(argv=['app_action', 'Safari'], proposito='Pulsar', motivo='app',
                     app={'nombre': 'Safari', 'bundle': 'com.apple.Safari', 'accion': 'hacer clic',
                          'nivel': 'lectura'})),
    ('permiso', dict(argv=['remember'], proposito='Guardar lo aprendido', motivo='aprendizaje', diff='+ usa tabs')),
    ('permiso', dict(argv=['navegador', 'visitar', 'https://ejemplo.com:443'], proposito='Visitar',
                     motivo='navegador', motivos=['navegador'],
                     sitio={'origen': 'https://ejemplo.com:443', 'proyecto': '/proyecto'})),
    ('permiso', dict(argv=['npm', 'install'], proposito='Instalar', motivo='salir_sandbox',
                     motivos=['salir_sandbox'], para_sesion=True, prefijo=['npm', 'install'],
                     prefijo_alcance='sin_sandbox')),
    ('permiso', dict(argv=['make'], proposito='Compilar', motivo='modo', para_sesion=True)),
    ('permiso', dict(argv=['x'], proposito='Contenido externo', motivo='contenido_externo',
                     motivos=['contenido_externo', 'diagnosticos_swift', 'edicion', 'reintento', 'regla'])),
    ('navegador', dict(accion='escribir', objetivo='#nombre', texto='Ana', enviar=True, borrar=False,
                       sitios={'version': 1, 'permitidos': [], 'bloqueados': [], 'estricto': False},
                       visita_sitio=None, visita_id=None)),
    ('navegador', dict(accion='desplazar', direccion='abajo', cantidad=300)),
    ('evento', dict(rol='system', texto='Importar · mcp docs (Codex), comando probar (OpenCode) sin autorizar',
                    hora=7.0, turno='t9', importadas={'pendientes': [
                        dict(clave='mcp:docs@/casa/.codex/config.toml', huella='0123456789abcdef', tipo='mcp',
                             nombre='docs', herramienta='Codex', estado='pendiente'),
                        dict(clave='agente:revisor@/proyecto/opencode.json', huella='00112233445566aa',
                             tipo='agente', nombre='revisor', herramienta='OpenCode', estado='pendiente'),
                        dict(clave='comando:probar@/proyecto/opencode.json', huella='fedcba9876543210',
                             tipo='comando', nombre='probar', herramienta='OpenCode', estado='cambio')]})),
    ('turno_terminado', dict(estado='blocked', resumen='Falta una clave', codigo_salida=1, tokens=900, turno='t9',
                             coste=0.4, tokens_turno=300, coste_turno=0.1, cierre=CIERRE,
                             cache={'leidos': 10, 'escritos': 2})),
    ('turno_terminado', dict(estado='cortado', resumen='', codigo_salida=1, tokens=950, turno='t10')),
    ('fin', dict(estado='failed', resumen='Fallo de ejecución', codigo_salida=1, tokens=1000, coste=0.5)),
]


def traducciones() -> dict:
    from alfalfa.pacai.sobre import Emisor
    from alfalfa.pacai.traductor import Traductor
    contador = iter(range(1, 10000))
    t = Traductor(Emisor('agent', SESION, ids=lambda: 'm_%016x' % next(contador)), perfiles=('work',),
                  extensiones=('alfalfa',), herramientas_cliente=('browser',))
    casos = []
    for n, (tipo, campos) in enumerate(CORPUS):
        previos = [] if t.raiz else t.empezar_turno('c_%016x' % (n + 1))      # una ejecución abierta
        v2 = dict(campos, version=2, tipo=tipo)
        if tipo == 'permiso':
            v2['id'] = '%032x' % (0xa000 + n)
            mensajes = t.permiso(dict(campos, id=v2['id']))
            # La app contesta (su eco es local, no del motor): así no queda nada que cancelar al cerrar el turno.
            mensajes += t.evento('permiso_resuelto', {'id': v2['id'], 'aprobado': True,
                                                      'motivo': 'Respuesta del cliente'})
        elif tipo == 'navegador':
            v2['id'] = '%032x' % (0xb000 + n)
            mensajes = t.evento(tipo, dict(campos, id=v2['id']))
        else:
            mensajes = t.evento(tipo, campos)
        casos.append({'v2': v2, 'pacai': previos + mensajes})
    return {'escenario': 'traducciones', 'casos': casos}


def main() -> int:
    nombres = sys.argv[1:] or [*ESCENARIOS, 'traducciones']
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nombre in nombres:
        if nombre == 'traducciones':
            (DESTINO / 'traducciones.json').write_text(json.dumps(traducciones(), ensure_ascii=False, indent=1)
                                                       + '\n', encoding='utf-8')
            continue
        datos = grabar(nombre)
        (DESTINO / (nombre + '.json')).write_text(json.dumps(datos, ensure_ascii=False, indent=1) + '\n',
                                                  encoding='utf-8')
        print(nombre, len(datos['stdio']['pasos']), len(datos['pacai']['pasos']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
