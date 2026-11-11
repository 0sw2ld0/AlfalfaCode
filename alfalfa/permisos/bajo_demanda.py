"""Política on-request de AlfalfaCode: sandbox por defecto y permiso sólo para excepciones.

Sigue el modelo AskForApproval::OnRequest de OpenAI Codex (Apache-2.0; ver
alfalfa/seatbelt/NOTICE y PROCEDENCIA.md): una orden normal corre en
el sandbox Seatbelt sin preguntar, y se pide permiso, una sola vez por
intento, cuando la orden

- es peligrosa (peligrosos.py): se aprueba para ejecutarla en el sandbox;
- declara `needs_network`: se aprueba la variante con red, que conserva las
  restricciones de escritura;
- pide `sandbox_permissions: require_escalated` con justificación, o el
  sandbox no arranca: se aprueba ejecutarla sin sandbox;
- falló con una denegación probable del sandbox (denegacion.py): se conserva
  ese resultado y se pide un reintento explícito, sin sandbox, de la misma
  orden. El reintento no vuelve a reintentarse.

Las reglas del usuario (arnes/reglas, al estilo de execpolicy) mandan antes:
`prohibida` no ejecuta la orden, `preguntar` pide permiso aunque la orden fuera
normal, y `permitida` quita la pregunta de orden peligrosa, pero nunca el
sandbox, la red ni la salida del sandbox, que siguen su camino.

Sólo TerminalManager activa esta política; los agentes del integrador conservan su
aprobación de siempre.
"""
from __future__ import annotations

import asyncio
import copy
import shlex
import unicodedata
from uuid import uuid4

from ..seatbelt import SandboxUnavailable
from .denegacion import is_likely_sandbox_denied
from .peligrosos import FORCED_RM, SUDO, dangerous_command_match

PELIGROSO = 'peligroso'
RED = 'red'
SALIR = 'salir_sandbox'
REINTENTO = 'reintento'
REGLA_USUARIO = 'regla'
MOTIVOS = (PELIGROSO, RED, SALIR, REINTENTO, REGLA_USUARIO, 'modo')

USE_DEFAULT = 'use_default'
REQUIRE_ESCALATED = 'require_escalated'
JUSTIFICATION_MIN = 10
JUSTIFICATION_MAX = 500
PREVIOUS_OUTPUT = 2000
DENIAL_LINE = 300

REGLA = {FORCED_RM: 'rm con opción de fuerza', SUDO: 'sudo'}


def _valid_justification(text):
    return (isinstance(text, str) and JUSTIFICATION_MIN <= len(text.strip()) <= JUSTIFICATION_MAX
            and not any(unicodedata.category(ch).startswith('C') for ch in text.strip()))


def request_options(args):
    """(salir del sandbox, red, justificación) declarados; ValueError si el contrato no se cumple."""
    mode = args.get('sandbox_permissions', USE_DEFAULT)
    if mode not in (USE_DEFAULT, REQUIRE_ESCALATED):
        raise ValueError('sandbox_permissions debe ser «use_default» o «require_escalated»; la orden no se ejecutó.')
    network = args.get('needs_network', False)
    if not isinstance(network, bool):
        raise ValueError('needs_network debe ser un booleano; la orden no se ejecutó.')
    justification = args.get('justification')
    if justification is not None and not _valid_justification(justification):
        raise ValueError('justification debe ser texto de ' + str(JUSTIFICATION_MIN) + ' a '
                         + str(JUSTIFICATION_MAX) + ' caracteres sin caracteres de control; la orden no se ejecutó.')
    if mode == REQUIRE_ESCALATED and justification is None:
        raise ValueError('Solicitud de salida del sandbox rechazada: require_escalated exige una justification '
                         'que explique por qué la orden no puede correr en el sandbox; la orden no se ejecutó.')
    return mode == REQUIRE_ESCALATED, network, justification.strip() if justification else None


def with_network(sandbox):
    """La misma política de escritura que `sandbox`, con la red abierta."""
    opened = copy.copy(sandbox)
    opened.network = True
    return opened


class OnRequestPolicy:
    """Decide, por intento, si una orden necesita permiso y con qué aislamiento corre."""

    def __init__(self, reglas=None):
        self.reglas = reglas

    def _rules(self, argv):
        """Decisión, explicación y si la decide una regla del equipo."""
        if not self.reglas:
            return None, None, False
        from ..equipo import ORIGEN
        from ..reglas import PROHIBIDA
        evaluation = self.reglas.evaluar(list(argv))
        rule = evaluation.regla()
        if rule is None and evaluation.no_analizable:
            equipo = any(r.origen.startswith(ORIGEN) for r in self.reglas.reglas)
            return evaluation.decision, 'orden de shell no analizable con reglas que preguntan o prohíben', equipo
        if rule is None:
            return None, None, False
        equipo = rule.origen.startswith(ORIGEN)
        text = ('regla del equipo' if equipo else 'regla del usuario') + ' «' + rule.texto() + '»'
        if rule.justificacion:
            text += ': ' + rule.justificacion.rstrip('.')
        if evaluation.decision == PROHIBIDA:
            raise ValueError('Orden prohibida por una ' + text + '. No se ejecutó; no la reformules ni la lances '
                             'por otra herramienta para eludirla.')
        return evaluation.decision, text, equipo

    async def execute(self, tools, args, argv, launch, *, unsandboxed_timeout):
        """Lanza `argv` con `launch(command, sandbox, extra)` tras la decisión que corresponda."""
        escalated, network, justification = request_options(args)
        runtime = tools.runtime
        ruled, rule_text, equipo = self._rules(argv)
        danger = dangerous_command_match(argv)
        if ruled == 'permitida':
            # El usuario la permitió de forma explícita: no se pregunta como peligrosa.
            danger = None
        attempt = uuid4().hex[:16]
        reasons, failure = [], None
        command, label = list(argv), 'none'
        from . import sesion
        if (not escalated and not danger and ruled != 'preguntar'
                and sesion.aprobadas(runtime.task, [sesion.clave_de_orden(argv, args.get('cwd'), sin_sandbox=True)],
                                     registrar=False)):
            # El usuario ya dejó correr esta misma orden sin sandbox en esta sesión: el
            # intento en el sandbox iba a fallar igual (como el reintento cacheado de Codex).
            escalated = True
        if escalated:
            reasons.append(SALIR)
        else:
            sandbox = runtime.process_sandbox
            try:
                if sandbox is None:
                    raise SandboxUnavailable('No hay sandbox configurado.')
                # SwiftPM no puede abrir su sandbox dentro del nuestro (seatbelt/compiladores.py).
                from ..seatbelt.compiladores import dentro_del_sandbox
                command = await (with_network(sandbox) if network else sandbox).prepare(dentro_del_sandbox(argv))
                label = 'seatbelt'
            except SandboxUnavailable as exc:
                failure = str(exc)
                reasons.append(SALIR)
                runtime.manager.event(runtime.task, 'system', 'Sandbox Seatbelt no disponible: ' + failure)
            if network:
                reasons.append(RED)
        if danger:
            reasons.append(PELIGROSO)
        if ruled == 'preguntar':
            reasons.append(REGLA_USUARIO)
        from . import modos
        if not reasons and ruled != 'permitida' and modos.pide_ordenes(modos.modo_de(runtime.task)):
            # Manual y Aceptar ediciones: cada orden que no sólo lee espera al usuario.
            from pathlib import Path

            from .terminal import solo_lectura
            carpeta = tools.command_cwd(args)
            raiz = Path(tools.root)
            if not solo_lectura(list(argv), raiz, None if Path(carpeta) == raiz.resolve() else Path(carpeta)):
                reasons.append(modos.MOTIVO_MODO)
        if reasons:
            await self._ask(runtime, argv, args, reasons, attempt, unsandboxed_timeout,
                            justification=justification, failure=failure, danger=danger,
                            rule=rule_text if ruled == 'preguntar' else None, equipo=equipo)
        extra = {'intento': attempt, 'permisos': reasons, 'red': network or label == 'none'}
        result = await launch(command, label, extra)
        if label != 'seatbelt' or not self._denied(result):
            return result
        # Se conserva el intento fallido tal como quedó registrado.
        result['denegacion_probable'] = True
        self._annotate(runtime, result, denegacion_probable=True)
        retry = uuid4().hex[:16]
        retry_reasons = [REINTENTO] + ([PELIGROSO] if danger else [])
        if equipo and ruled == 'preguntar':
            retry_reasons.append(REGLA_USUARIO)
        try:
            await self._ask(runtime, argv, args, retry_reasons, retry, unsandboxed_timeout,
                            justification=justification, previous=attempt, danger=danger,
                            failure=self._denial_text(attempt, result), equipo=equipo,
                            rule=rule_text if equipo else None)
        except ValueError as exc:
            result['reintento'] = 'rechazado: ' + str(exc)
            self._annotate(runtime, result, reintento='rechazado')
            return result
        again = await launch(list(argv), 'none', {'intento': retry, 'reintento_de': attempt,
                                                   'permisos': retry_reasons, 'red': True})
        again['intento_previo'] = {'intento': attempt, 'sandbox': 'seatbelt', 'exit_code': result.get('exit_code'),
                                   'output': str(result.get('output') or '')[-PREVIOUS_OUTPUT:]}
        evidencia = getattr(runtime, 'evidence', None)
        if evidencia is not None and hasattr(evidencia, 'repetida_sin_sandbox'):
            # Ahora cuenta el reintento: el fallo de dentro era del sandbox, no de la orden.
            evidencia.repetida_sin_sandbox(result.get('operation_id'), again.get('operation_id'))
        return again

    @staticmethod
    def _annotate(runtime, result, **fields):
        """Añade al registro ya guardado del intento lo que se supo después.

        Lo sustituye en la lista en vez de cambiarlo por dentro: así el historial
        (operations.jsonl, al que sólo se añade) ve el cambio y lo escribe."""
        operations = runtime.state.get('operations') or []
        for index in range(len(operations) - 1, -1, -1):
            record = operations[index]
            if isinstance(record, dict) and record.get('id') == result.get('operation_id'):
                operations[index] = dict(record, **fields)
                runtime.save()
                return

    @staticmethod
    def _denial_text(attempt, result):
        """Qué vio el usuario fallar: código y la última línea de salida."""
        lines = [line.strip() for line in str(result.get('output') or '').splitlines() if line.strip()]
        text = 'El intento ' + attempt + ' terminó con código ' + str(result.get('exit_code'))
        return text + (': ' + lines[-1][:DENIAL_LINE] if lines else ' y una salida típica del sandbox.')

    @staticmethod
    def _denied(result):
        if result.get('timed_out') or result.get('exception') or result.get('output_limit_exceeded'):
            return False
        return is_likely_sandbox_denied('seatbelt', result.get('exit_code'), aggregated=result.get('output') or '')

    @staticmethod
    async def _ask(runtime, argv, args, reasons, attempt, unsandboxed_timeout, *, justification=None,
                   failure=None, previous=None, danger=None, rule=None, equipo=False):
        from . import sesion
        unsandboxed = SALIR in reasons or REINTENTO in reasons
        # Como ApprovedForSession de Codex: lo peligroso y lo que una regla manda preguntar, nunca.
        para_sesion = PELIGROSO not in reasons and REGLA_USUARIO not in reasons
        clave = sesion.clave_de_orden(argv, args.get('cwd'), sin_sandbox=unsandboxed, red=RED in reasons)
        if para_sesion and sesion.aprobadas(runtime.task, [clave]):
            runtime.manager.event(runtime.task, 'system', 'Aprobado en esta sesión: ' + shlex.join(argv)[:300]
                                  + (' (sin sandbox)' if unsandboxed else ''))
            return None
        request = {'argv': list(argv), 'purpose': str(args['purpose']), 'motivo': reasons[0],
                   'motivos': list(reasons), 'intento': attempt}
        if equipo:
            request['_equipo'] = True
        if para_sesion:
            request['para_sesion'] = True
        if justification:
            request['justificacion'] = justification
        if previous:
            request['reintento_de'] = previous
        if danger:
            request['regla'] = REGLA.get(danger, 'orden no analizable')
        if rule:
            request['regla'] = (request['regla'] + ' · ' + rule) if request.get('regla') else rule
        if RED in reasons:
            request['red'] = True
        if unsandboxed:
            # Mismo campo que ya conocían terminal y transporte: nunca se concede solo.
            request['sin_sandbox'] = failure or justification or 'Salida del sandbox solicitada.'
            if failure and SALIR in reasons:
                request['fallo_sandbox'] = failure
        try:
            if unsandboxed and unsandboxed_timeout is not None:
                respuesta = await asyncio.wait_for(runtime.approve(request), unsandboxed_timeout)
            else:
                respuesta = await runtime.approve(request)
        except asyncio.TimeoutError:
            policy = runtime.approval_policy
            if policy is not None and isinstance(getattr(policy, 'denied', None), int):
                policy.denied += 1
            raise ValueError('Sin respuesta a tiempo al permiso para ejecutar sin sandbox (el usuario no contestó); '
                             'la orden no se ejecutó. No es un rechazo de la orden: vuelve a pedirla si hace '
                             'falta.') from None
        if para_sesion and respuesta == sesion.SESION:
            sesion.recordar(runtime.task, [clave])
        return respuesta
