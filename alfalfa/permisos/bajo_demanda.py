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

# Consultas con red que sólo leen: corren en el sandbox con red sin tarjeta (como un `git status`
# local). `gh` sólo habla con GitHub; `git fetch`/`ls-remote` sólo con un remoto ya configurado,
# nunca con una URL que el modelo escriba (podría llevar datos en ella).
LECTURAS_DE_RED = {
    'gh': {('pr', 'view'), ('pr', 'list'), ('pr', 'status'), ('pr', 'checks'), ('pr', 'diff'),
           ('issue', 'view'), ('issue', 'list'), ('run', 'view'), ('run', 'list'), ('repo', 'view'),
           ('release', 'view'), ('release', 'list')},
    'git': {('fetch',), ('ls-remote',)},
}
_OPCIONES_QUE_ESCRIBEN = ('--web', '-w', '--log-failed', '--exit-status', '--upload-pack', '--exec')


def lectura_de_red(argv, runtime=None):
    """¿Es `argv` una consulta con red que sólo lee y se puede lanzar sin preguntar?

    No tras leer contenido externo: ahí la red vuelve a pedir permiso como antes."""
    if runtime is not None:
        from ..contenido_externo import contaminada
        if contaminada(runtime):
            return False
    if not argv:
        return False
    programa = str(argv[0]).rsplit('/', 1)[-1]
    reglas = LECTURAS_DE_RED.get(programa)
    if not reglas:
        return False
    resto = [str(a) for a in argv[1:]]
    if programa == 'git' and resto[:1] and resto[0].startswith('-'):
        return False  # git -C otra/carpeta, -c clave=valor…: no
    if not any(tuple(resto[:len(r)]) == r for r in reglas):
        return False
    if any(a.split('=', 1)[0] in _OPCIONES_QUE_ESCRIBEN for a in resto):
        return False
    if programa == 'git' and any('://' in a or '@' in a or (':' in a and not a.startswith('-')) for a in resto):
        return False  # sólo remotos por su nombre, nunca una URL
    return True


USE_DEFAULT = 'use_default'
# Lo que devuelve la aprobación con «No volver a preguntar por órdenes que empiecen por …».
SIEMPRE = 'siempre'
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
        bypass = bool(self.reglas) and not escalated and ruled == 'permitida' and self.reglas.fuera_del_sandbox(argv)
        if bypass:
            from ..contenido_externo import contaminada
            if contaminada(runtime):
                # Tras leer contenido externo, salir del sandbox vuelve a preguntar (una tarjeta).
                escalated, bypass = True, False
        if bypass:
            # Como `allow` de Codex con bypass_sandbox: el usuario dijo «No volver a preguntar».
            runtime.manager.event(runtime.task, 'system', 'Permitida siempre por ' + str(rule_text) + ': '
                                  + shlex.join(argv)[:300] + ' (sin sandbox)')
            return await launch(list(argv), 'none', {'intento': attempt, 'permisos': [], 'red': True,
                                                     'regla_sin_sandbox': rule_text})
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
            if network and not lectura_de_red(argv, runtime):
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
                            rule=rule_text if ruled == 'preguntar' else None, equipo=equipo,
                            reglas=self.reglas)
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
                            rule=rule_text if equipo else None, reglas=self.reglas)
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
                   failure=None, previous=None, danger=None, rule=None, equipo=False, reglas=None):
        from . import sesion
        unsandboxed = SALIR in reasons or REINTENTO in reasons
        # Como ApprovedForSession de Codex: lo peligroso y lo que una regla manda preguntar, nunca.
        para_sesion = PELIGROSO not in reasons and REGLA_USUARIO not in reasons
        clave = sesion.clave_de_orden(argv, args.get('cwd'), sin_sandbox=unsandboxed, red=RED in reasons)
        # Tras leer contenido externo, la red y la salida del sandbox llevan también ese motivo
        # (una sola tarjeta) y su propia clave: lo aprobado para la sesión antes no vale.
        from ..contenido_externo import MOTIVO as EXTERNO
        from ..contenido_externo import clave_de_orden, contaminada
        origenes = contaminada(runtime) if (unsandboxed or RED in reasons) else []
        if origenes:
            reasons = list(reasons) + [EXTERNO]
            clave = clave_de_orden(clave)
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
        prefijo = None
        if (unsandboxed and para_sesion and not origenes and reglas is not None
                and not reglas.evaluar(list(argv)).coincidencias):
            # «No volver a preguntar por órdenes que empiecen por …», como el
            # ApprovedExecpolicyAmendment de Codex: nunca tras contenido externo, en órdenes
            # peligrosas, con una regla que ya decide ni con prefijos que lo permitirían todo.
            from ..reglas import prefijo_para_siempre
            prefijo = prefijo_para_siempre(args.get('prefix_rule'), argv)
            if prefijo:
                request['prefijo'] = prefijo
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
        if origenes:
            request['origen_externo'] = ', '.join(origenes[-6:])
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
        if prefijo and respuesta == SIEMPRE:
            OnRequestPolicy._guardar_siempre(runtime, reglas, prefijo, argv)
        return respuesta

    @staticmethod
    def _guardar_siempre(runtime, reglas, prefijo, argv):
        """Escribe la regla en `$ALFALFA_HOME/reglas.toml` y la aplica ya a esta sesión y sus
        subagentes, que comparten el mismo objeto de reglas. Si no se puede guardar, la orden
        aprobada se ejecuta igual y se avisa."""
        import time

        from ..reglas import ReglasInvalidas, guardar_para_siempre
        home = getattr(runtime.manager, 'home', None)
        texto = shlex.join(prefijo)
        try:
            if home is None:
                raise ReglasInvalidas('no hay carpeta de AlfalfaCode')
            nuevas = guardar_para_siempre(home, prefijo, 'Aprobada con «No volver a preguntar» el '
                                          + time.strftime('%Y-%m-%d') + ' (' + shlex.join(argv)[:120] + ').')
        except (ReglasInvalidas, OSError) as exc:
            runtime.manager.event(runtime.task, 'system', 'No se pudo guardar «No volver a preguntar» por «'
                                  + texto + '»: ' + str(exc)[:200] + '. Esta vez se ejecuta igual.')
            return
        reglas.reglas.append(nuevas.reglas[-1])
        runtime.manager.event(runtime.task, 'system', 'Ya no se preguntará por las órdenes que empiecen por «'
                              + texto + '»: corren fuera del sandbox. Se quita en reglas.toml.')
