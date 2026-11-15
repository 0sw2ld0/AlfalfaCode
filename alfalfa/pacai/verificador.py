# Derivado del SDK de Python de PACAI (Apache-2.0); ver NOTICE y LICENSE en esta carpeta.
"""Reglas de sesión de PACAI 0.2: lo que el esquema no puede ver.

``Session`` sigue una sesión mensaje a mensaje, tal como la ve un extremo, y
anota cada infracción. Es la misma lógica que usa ``conformance/verificar.py``.

    s = Session()
    for m in mensajes:
        s.feed(m)          # devuelve los problemas de ese mensaje
    s.finish()             # comprobaciones de final de sesión
    s.problems             # todos los problemas, con la línea
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .esquema import validar as validate
from .jcs import digest
from .tipos import PROFILE_OF, RUN_SCOPED

__all__ = ['Session', 'RunInfo', 'ApprovalInfo']

# Respuestas cuyo reply_to debe apuntar a un tipo concreto del otro lado.
_ANSWERS = {'session_started': 'hello', 'approval_result': 'approval_request', 'snapshot': 'hello'}
# Tipos que se pueden enviar antes de que la sesión empiece.
_BEFORE_START = {'hello', 'session_started', 'error', 'close', 'heartbeat'}


def _instant(texto: Any) -> Any:
    """Convierte un instante ISO 8601 en datetime; si no puede, devuelve el texto."""
    try:
        return datetime.fromisoformat(str(texto).replace('Z', '+00:00'))
    except ValueError:
        return str(texto)


def _before(a: Any, b: Any) -> bool:
    x, y = _instant(a), _instant(b)
    if type(x) is not type(y):
        x, y = str(a), str(b)
    return x < y


@dataclass
class RunInfo:
    run_id: str
    parent_run_id: str | None = None
    tool_call_id: str | None = None
    active: bool = True
    outcome: str | None = None
    children: set[str] = field(default_factory=set)
    open_tools: set[str] = field(default_factory=set)


@dataclass
class ApprovalInfo:
    request: Mapping[str, Any]
    state: str = 'pending'          # pending, approved, denied, expired
    scope: str | None = None


class Session:
    """Sigue una sesión y comprueba sus reglas (§2, §4, §6, §7, §8)."""

    def __init__(self, validate_schema: bool = True):
        self.validate_schema = validate_schema
        self.problems: list[str] = []
        self.seen: dict[str, Mapping[str, Any]] = {}
        self.seq = {'agent': 0, 'client': 0}
        self.profiles: set[str] = set()
        self.extensions: set[str] = set()
        self.started = False
        self.closed: set[str] = set()
        self.runs: dict[str, RunInfo] = {}
        self.tools: dict[str, Mapping[str, Any]] = {}
        self.tool_run: dict[str, str] = {}
        self.ended_tools: set[str] = set()
        self.messages: dict[str, str] = {}
        self.ended_messages: set[str] = set()
        self.approvals: dict[str, ApprovalInfo] = {}
        self.approvals_by_tool: dict[str, list[str]] = {}
        self.inputs: set[str] = set()
        self.client_tools: set[str] = set()
        self._resume_hello: str | None = None
        self._needs_snapshot = False
        self._count = 0

    # -- consultas ---------------------------------------------------------
    @property
    def active_runs(self) -> list[str]:
        return [r for r, i in self.runs.items() if i.active]

    @property
    def active_root_run(self) -> str | None:
        return next((r for r, i in self.runs.items() if i.active and i.parent_run_id is None), None)

    def authorized_by_session(self, name: str, arguments: Any) -> str | None:
        """Id de una aprobación con alcance ``session`` que cubre esta llamada exacta."""
        d = digest(arguments)
        for aid, a in self.approvals.items():
            if a.state == 'approved' and a.scope == 'session' and a.request.get('name') == name \
                    and a.request.get('digest') == d:
                return aid
        return None

    # -- entrada principal -------------------------------------------------
    def feed(self, m: Mapping[str, Any]) -> list[str]:
        """Procesa un mensaje y devuelve los problemas que encuentra en él."""
        self._count += 1
        donde = f'línea {self._count} ({m.get("type")})'
        nuevos: list[str] = []

        def fallo(texto: str) -> None:
            nuevos.append(f'{donde}: {texto}')

        mid = m.get('id')
        if mid in self.seen:
            if self.seen[mid] == m:
                return []  # retransmisión: se descarta (§2)
            fallo('id repetido con otro contenido')
            self.problems += nuevos
            return nuevos
        if self.validate_schema:
            nuevos += [f'{donde}: {e}' for e in validate(m)]
        t, emisor, ref = m.get('type'), m.get('sender'), m.get('reply_to')
        p = m.get('payload') if isinstance(m.get('payload'), dict) else {}

        if emisor in self.closed:
            fallo('mensaje tras close de ese mismo emisor')
        if self._needs_snapshot and emisor == 'agent' and t != 'session_started':
            if t != 'snapshot':
                fallo('salto de seq sin snapshot')
            self._needs_snapshot = False
        self._check_seq(m, t, emisor, p, fallo)
        self._check_reply(t, emisor, ref, p, fallo)
        self.seen[mid] = m

        if not self.started and t not in _BEFORE_START:
            fallo(f'{t} antes de session_started')
        if t in PROFILE_OF and PROFILE_OF[t] not in self.profiles:
            fallo(f'perfil {PROFILE_OF[t]} no negociado')
        if t and '.' in t and t.split('.')[0] not in self.extensions:
            fallo(f'extensión {t.split(".")[0]} no negociada')
        if emisor == 'agent' and t in RUN_SCOPED and 'run_id' in p:
            info = self.runs.get(p['run_id'])
            if info is None or not info.active:
                fallo(f'run_id {p["run_id"]} no es una ejecución activa')

        manejador = getattr(self, '_on_' + str(t), None)
        if manejador is not None and '.' not in str(t):
            manejador(m, p, emisor, ref, fallo)
        self.problems += nuevos
        return nuevos

    def finish(self) -> list[str]:
        """Comprobaciones de final de sesión. Devuelve los problemas nuevos."""
        nuevos = []
        if self.active_runs:
            nuevos.append(f'ejecuciones sin run_finished: {sorted(self.active_runs)}')
        if self._needs_snapshot:
            nuevos.append('salto de seq sin snapshot')
        self.problems += nuevos
        return nuevos

    @classmethod
    def check(cls, messages, validate_schema: bool = True) -> list[str]:
        """Verifica una sesión completa y devuelve todos sus problemas."""
        s = cls(validate_schema)
        for m in messages:
            s.feed(m)
        s.finish()
        return s.problems

    # -- sobre -------------------------------------------------------------
    def _check_seq(self, m, t, emisor, p, fallo):
        if emisor not in self.seq or not isinstance(m.get('seq'), int):
            return
        s, esperado = m['seq'], self.seq[emisor] + 1
        if s != esperado:
            # Al reanudar sin reenvío, el agente salta seq y lo explica con snapshot (§6).
            if t == 'session_started' and p.get('resumed') and emisor == 'agent' and s > esperado \
                    and self._resume_hello:
                self._needs_snapshot = True
            else:
                fallo(f'seq {s} tras {self.seq[emisor]} del mismo emisor')
        self.seq[emisor] = s

    def _check_reply(self, t, emisor, ref, p, fallo):
        if ref is None:
            return
        previo = self.seen.get(ref)
        propio_permitido = t == 'approval_result' and p.get('decision') == 'expired'
        if previo is None:
            fallo('reply_to a un mensaje inexistente')
        elif previo.get('sender') == emisor and not propio_permitido:
            fallo('responde a un mensaje propio')
        elif t in _ANSWERS and previo.get('type') != _ANSWERS[t]:
            fallo(f'responde a un {previo.get("type")}')

    # -- negociación (§4.1, §6) --------------------------------------------
    def _on_hello(self, m, p, emisor, ref, fallo):
        if emisor == 'client':
            self.client_tools |= {h.get('name') for h in p.get('client_tools', []) if isinstance(h, dict)}
        if not self.started:
            return
        if emisor == 'agent':
            fallo('el agente repite hello; al reanudar solo contesta con session_started')
        elif 'resume_after_seq' not in p:
            fallo('hello repetido sin resume_after_seq')
        else:
            self._resume_hello = m['id']

    def _on_session_started(self, m, p, emisor, ref, fallo):
        perfiles, ext = set(p.get('profiles', [])), set(p.get('extensions', []))
        if not self.started:
            if p.get('resumed'):
                fallo('resumed en una sesión que no había empezado')
            self.profiles, self.extensions, self.started = perfiles, ext, True
            return
        if not p.get('resumed'):
            fallo('session_started repetido sin resumed')
        elif ref != self._resume_hello:
            fallo('la reanudación no contesta al hello de reanudación')
        if perfiles != self.profiles or ext != self.extensions:
            fallo('la reanudación cambia perfiles o extensiones')

    # -- ejecuciones y subejecuciones (§4.2, §4.9) ------------------------
    def _on_input(self, m, p, emisor, ref, fallo):
        self.inputs.add(m['id'])

    def _on_run_started(self, m, p, emisor, ref, fallo):
        madre_id, tc = p.get('parent_run_id'), p.get('tool_call_id')
        if madre_id is None:
            if p.get('input_id') not in self.inputs:
                fallo('input_id no corresponde a un input del cliente')
            raiz = self.active_root_run
            if raiz:
                fallo(f'abre una ejecución raíz con {raiz} todavía activa')
        else:
            madre = self.runs.get(madre_id)
            if madre is None or not madre.active:
                fallo('parent_run_id no es una ejecución activa')
            else:
                madre.children.add(m['id'])
            if tc is not None and (self.tool_run.get(tc) != madre_id or tc in self.ended_tools):
                fallo('tool_call_id no es una herramienta abierta de la ejecución madre')
        self.runs[m['id']] = RunInfo(m['id'], madre_id, tc)

    def _on_run_finished(self, m, p, emisor, ref, fallo):
        info = self.runs.get(p.get('run_id'))
        if info is None or not info.active:
            fallo('cierra una ejecución que no está abierta')
            return
        hijas = sorted(c for c in info.children if self.runs[c].active)
        if hijas:
            fallo(f'termina antes que sus subejecuciones {hijas}')
        if info.open_tools:
            fallo(f'termina con herramientas sin tool_end {sorted(info.open_tools)}')
        info.active, info.outcome = False, p.get('outcome')

    # -- mensajes (§4.3) ---------------------------------------------------
    def _on_message_start(self, m, p, emisor, ref, fallo):
        self.messages[m['id']] = p.get('run_id')

    def _on_message_delta(self, m, p, emisor, ref, fallo):
        if p.get('message_id') not in self.messages:
            fallo(f'{m.get("type")} sin message_start')
        elif p.get('message_id') in self.ended_messages:
            fallo(f'{m.get("type")} tras message_end')

    def _on_message_end(self, m, p, emisor, ref, fallo):
        self._on_message_delta(m, p, emisor, ref, fallo)
        self.ended_messages.add(p.get('message_id'))

    # -- herramientas (§4.4) -----------------------------------------------
    def _open_tool(self, tc, p):
        self.tools[tc], self.tool_run[tc] = p, p.get('run_id')
        if p.get('run_id') in self.runs:
            self.runs[p['run_id']].open_tools.add(tc)

    def _on_tool_start(self, m, p, emisor, ref, fallo):
        self._open_tool(m['id'], p)
        if p.get('side') == 'client' and p.get('name') not in self.client_tools:
            fallo(f'herramienta del cliente {p.get("name")} no declarada en client_tools')
        aid = p.get('approval_id')
        if aid is None:
            return
        a = self.approvals.get(aid)
        if a is None:
            fallo('approval_id no es una aprobación conocida')
        elif a.state != 'approved' or a.scope != 'session':
            fallo('approval_id no autoriza: no es una aprobación session vigente')
        elif a.request.get('name') != p.get('name') or a.request.get('digest') != digest(p.get('arguments', {})):
            fallo('approval_id no autoriza: otra herramienta u otros argumentos')

    def _on_tool_end(self, m, p, emisor, ref, fallo):
        tc = p.get('tool_call_id')
        inicio = self.tools.get(tc)
        if inicio is None:
            fallo('tool_end sin tool_start')
            return
        if tc in self.ended_tools:
            fallo('tool_end repetido')
        elif emisor != inicio.get('side'):
            fallo(f'lo cierra {emisor} y la herramienta es del lado {inicio.get("side")}')
        elif emisor == 'client' and ref != tc:
            fallo('el cliente debe responder con reply_to al tool_start')
        if p.get('status') == 'success' and not self._approved(tc):
            fallo('herramienta ejecutada sin aprobación vigente')
        if any(i.active and i.tool_call_id == tc for i in self.runs.values()):
            fallo('la herramienta termina antes que su subejecución')
        self.ended_tools.add(tc)
        if self.tool_run.get(tc) in self.runs:
            self.runs[self.tool_run[tc]].open_tools.discard(tc)

    def _approved(self, tc) -> bool:
        """Sin approval_request no hay nada que comprobar; con ella, la última debe estar aprobada."""
        pedidas = self.approvals_by_tool.get(tc)
        return not pedidas or self.approvals[pedidas[-1]].state == 'approved'

    # -- aprobaciones (§4.5) -----------------------------------------------
    def _on_approval_request(self, m, p, emisor, ref, fallo):
        if p.get('digest') != digest(p.get('arguments', {})):
            fallo('el digest no corresponde a los argumentos')
        if not _before(m.get('time', ''), p.get('expires', '')):
            fallo('la petición nace caducada')
        tc = p.get('tool_call_id')
        inicio = self.tools.get(tc)
        if inicio is None or tc in self.ended_tools:
            fallo('approval_request de una herramienta que no está abierta')
        elif inicio.get('name') != p.get('name') or digest(inicio.get('arguments', {})) != p.get('digest'):
            fallo('approval_request no coincide con su tool_start')
        self.approvals[m['id']] = ApprovalInfo(p)
        self.approvals_by_tool.setdefault(tc, []).append(m['id'])

    def _on_approval_result(self, m, p, emisor, ref, fallo):
        a = self.approvals.get(ref)
        if a is None:
            return  # reply_to ya se marcó; el agente contestaría unknown_approval
        if a.state != 'pending':
            # Un sí del cliente que llega tras el expired del agente se ignora (§4.5).
            if not (a.state == 'expired' and emisor == 'client'):
                fallo('aprobación ya resuelta')
            return
        caducada = not _before(m.get('time', ''), a.request.get('expires', ''))
        if emisor == 'agent':
            if not caducada:
                fallo('declara caducada una petición antes de expires')
            a.state = 'expired'
            return
        alcance = p.get('scope')
        if alcance and alcance not in a.request.get('scopes', []):
            fallo(f'alcance {alcance} no ofrecido')
            a.state = 'invalid'
        elif p.get('decision') == 'approved' and caducada:
            a.state = 'expired'  # tarde: no cuenta como aprobación
        else:
            a.state, a.scope = p.get('decision'), alcance

    # -- estado compartido (§7.2) ------------------------------------------
    def _on_state_delta(self, m, p, emisor, ref, fallo):
        for op in p.get('patch', []):
            ruta = str(op.get('path', '')) if isinstance(op, dict) else ''
            if ruta != '/' + str(emisor) and not ruta.startswith(f'/{emisor}/'):
                fallo(f'{emisor} modifica una raíz ajena ({ruta})')
            desde = op.get('from') if isinstance(op, dict) else None
            if op.get('op') == 'move' and desde and not str(desde).startswith(f'/{emisor}'):
                fallo(f'{emisor} modifica una raíz ajena ({desde})')

    def _on_state_snapshot(self, m, p, emisor, ref, fallo):
        for raiz in p:
            if raiz != emisor:
                fallo(f'{emisor} modifica una raíz ajena (/{raiz})')

    # -- reanudación (§6) --------------------------------------------------
    def _on_snapshot(self, m, p, emisor, ref, fallo):
        if self._resume_hello is None or ref != self._resume_hello:
            fallo('snapshot fuera de una reanudación')
        self._needs_snapshot = False
        estados = {r.get('run_id'): r for r in p.get('runs', []) if isinstance(r, dict)}
        for rid in self.active_runs:
            if rid not in estados:
                fallo(f'el snapshot omite la ejecución activa {rid}')
        for rid, r in estados.items():
            info = self.runs.get(rid)
            if r.get('status') == 'active':
                if info is not None and not info.active:
                    fallo(f'relanza la ejecución terminada {rid}')
                elif info is None:
                    self.runs[rid] = RunInfo(rid, r.get('parent_run_id'))
            elif info is not None and info.active:
                info.active, info.outcome = False, r.get('outcome')
                info.open_tools.clear()
        for previo in p.get('last_messages', []) + p.get('pending_approvals', []):
            if isinstance(previo, dict):
                self._absorb(previo, fallo)
        for pedida in p.get('pending_approvals', []):
            if isinstance(pedida, dict) and pedida.get('type') != 'approval_request':
                fallo('pending_approvals solo lleva approval_request')

    def _absorb(self, previo, fallo):
        """Incorpora un mensaje que el cliente se perdió y llega dentro de snapshot."""
        mid, t = previo.get('id'), previo.get('type')
        if mid in self.seen:
            return
        self.seen[mid] = previo
        p = previo.get('payload') if isinstance(previo.get('payload'), dict) else {}
        if t == 'tool_start':
            self._open_tool(mid, p)
        elif t == 'tool_end':
            tc = p.get('tool_call_id')
            self.ended_tools.add(tc)
            if self.tool_run.get(tc) in self.runs:
                self.runs[self.tool_run[tc]].open_tools.discard(tc)
        elif t == 'message_start':
            self.messages[mid] = p.get('run_id')
        elif t == 'message_end':
            self.ended_messages.add(p.get('message_id'))
        elif t == 'approval_request':
            if p.get('digest') != digest(p.get('arguments', {})):
                fallo('el digest no corresponde a los argumentos')
            tc = p.get('tool_call_id')
            if tc not in self.tools and p.get('run_id') in self.runs:
                self._open_tool(tc, {'run_id': p.get('run_id'), 'name': p.get('name'),
                                     'arguments': p.get('arguments', {}), 'side': 'agent'})
            self.approvals[mid] = ApprovalInfo(p)
            self.approvals_by_tool.setdefault(tc, []).append(mid)

    # -- cierre (§4.8) -----------------------------------------------------
    def _on_close(self, m, p, emisor, ref, fallo):
        self.closed.add(emisor)
