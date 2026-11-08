"""Puntos de AlfalfaCode donde se disparan los hooks y forma de su entrada.

La entrada sigue los esquemas de Codex (codex-rs/hooks/schema/generated/
*.command.input.schema.json, Apache-2.0), que son los de Claude Code más
`turn_id`: session_id, transcript_path, cwd, hook_event_name, model,
permission_mode y los campos propios de cada evento. AlfalfaCode añade
`alfalfa_tool_name` y `alfalfa_tool_input` con la llamada nativa.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from . import nombres
from .motor import MAX_CONTINUACIONES

# Historial de ejecuciones que se guarda en la sesión (evidencia), como mucho.
MAX_REGISTRO = 200


class HooksDeSesion:
    def __init__(self, motor, terminal, task, *, solo_lectura=False, reanudada=False):
        self.motor = motor
        self.reanudada = reanudada
        self.terminal = terminal
        self.task = task
        self.solo_lectura = solo_lectura
        self.turno = None
        self.iniciada = False
        self.continuaciones = 0
        self.registro = []
        self.al_anotar = None

    def activo(self):
        return self.motor.activo()

    def nuevo_turno(self):
        # El id del turno de la sesión (arnes/sesiones); sin él, uno propio.
        self.turno = self.task.get('turno_actual') or uuid4().hex
        self.continuaciones = 0
        return self.turno

    def comunes(self, evento):
        return dict(session_id=self.task['id'],
                    transcript_path=str(self.terminal.directory / (self.task['id'] + '.jsonl')),
                    cwd=self.motor.raiz, hook_event_name=evento, model=self.task.get('model') or '',
                    permission_mode='plan' if self.solo_lectura else 'default',
                    turn_id=self.turno or self.nuevo_turno())

    def _anotar(self, evento, resultado, extra=None):
        for fila in resultado.ejecuciones:
            fila = dict(fila, turn_id=self.turno, **(extra or {}))
            self.registro.append(fila)
        del self.registro[:-MAX_REGISTRO]
        if self.al_anotar is not None:
            self.al_anotar(self.registro)
        for aviso in resultado.avisos:
            self.terminal.event(self.task, 'system', 'Hooks · ' + aviso[:1500])
        return resultado

    async def inicio(self):
        """SessionStart, una vez por proceso: «startup» o «resume»."""
        if self.iniciada:
            return None
        self.iniciada = True
        fuente = 'resume' if self.reanudada else 'startup'
        entrada = dict(self.comunes('SessionStart'), source=fuente)
        return self._anotar('SessionStart', await self.motor.disparar('SessionStart', entrada, [fuente]))

    async def mensaje(self, texto):
        entrada = dict(self.comunes('UserPromptSubmit'), prompt=texto)
        return self._anotar('UserPromptSubmit', await self.motor.disparar('UserPromptSubmit', entrada))

    def _herramienta(self, nativo, args):
        return dict(tool_name=nombres.nombre_hook(nativo), tool_input=nombres.a_claude(nativo, args, self.motor.raiz),
                    alfalfa_tool_name=nativo, alfalfa_tool_input=args if isinstance(args, dict) else {})

    async def antes(self, nativo, args, ident):
        entrada = dict(self.comunes('PreToolUse'), tool_use_id=ident, **self._herramienta(nativo, args))
        if nativo in ('run_command', 'run_checks') and isinstance(args, dict) and args.get('cwd') not in (None, '', '.'):
            # Como Codex (tool_cwd): la carpeta real de la orden.
            entrada['cwd'] = str((Path(self.motor.raiz) / str(args['cwd'])).resolve())
        resultado = await self.motor.disparar('PreToolUse', entrada, nombres.entradas_de_filtro(nativo))
        return self._anotar('PreToolUse', resultado, dict(tool_use_id=ident, herramienta=nativo))

    async def despues(self, nativo, args, respuesta, ident):
        entrada = dict(self.comunes('PostToolUse'), tool_use_id=ident, tool_response=respuesta,
                       **self._herramienta(nativo, args))
        resultado = await self.motor.disparar('PostToolUse', entrada, nombres.entradas_de_filtro(nativo))
        return self._anotar('PostToolUse', resultado, dict(tool_use_id=ident, herramienta=nativo))

    async def parada(self, ultimo):
        """Stop. Devuelve (resultado, puede_seguir): tras MAX_CONTINUACIONES ya no sigue."""
        entrada = dict(self.comunes('Stop'), stop_hook_active=self.continuaciones > 0,
                       last_assistant_message=ultimo)
        resultado = self._anotar('Stop', await self.motor.disparar('Stop', entrada))
        if resultado.bloquear and not resultado.detener:
            if self.continuaciones >= MAX_CONTINUACIONES:
                self.terminal.event(self.task, 'system', 'Hooks · Stop pidió seguir ' + str(self.continuaciones + 1)
                                    + ' veces en este turno; se termina para no entrar en bucle.')
                return resultado, False
            self.continuaciones += 1
            return resultado, True
        return resultado, False
