"""Modos de permisos de una sesión, como los de Claude Code.

- auto (por defecto): las órdenes normales corren en el sandbox sin preguntar; las
  peligrosas, las de red y las que salen del sandbox piden permiso.
- manual: pide permiso antes de cada cambio, es decir, de cada orden que no sólo
  lee y de cada edición de archivos.
- ediciones («Aceptar ediciones»): edita sin preguntar y pide permiso para cada
  orden que no sólo lee.
- plan: sólo lee (sin herramientas de escritura ni órdenes) y propone un plan.
- omitir («Omitir permisos»): aprueba todo sin preguntar, también fuera del
  sandbox. Las reglas `prohibida` del usuario siguen mandando.

El modo vive en la tarea (`modo`), así que se guarda con la sesión y se puede
cambiar entre turnos.
"""
from __future__ import annotations

import re

AUTO, MANUAL, EDICIONES, PLAN, OMITIR = 'auto', 'manual', 'ediciones', 'plan', 'omitir'
MODOS = (AUTO, MANUAL, EDICIONES, PLAN, OMITIR)
NOMBRES = {AUTO: 'Auto', MANUAL: 'Manual', EDICIONES: 'Aceptar ediciones', PLAN: 'Plan', OMITIR: 'Omitir permisos'}
# Motivos de permiso nuevos: una orden que el modo manda preguntar y una edición.
MOTIVO_MODO = 'modo'
MOTIVO_EDICION = 'edicion'
EDITORAS = ('write_file', 'edit_file', 'apply_patch')
_RUTA_DE_PARCHE = re.compile(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', re.MULTILINE)

PLAN_MANUAL = '''
Modo plan: esta sesión sólo lee. Investiga lo necesario con las herramientas de
lectura y responde con un plan concreto: qué archivos cambiarías, cómo y cómo lo
comprobarías. No intentes editar ni ejecutar órdenes; el usuario cambiará de modo
cuando quiera que lo hagas.'''


def modo_de(task):
    modo = task.get('modo') if isinstance(task, dict) else None
    return modo if modo in MODOS else AUTO


def pide_ordenes(modo):
    return modo in (MANUAL, EDICIONES)


def pide_ediciones(modo):
    return modo == MANUAL


def solo_lee(modo):
    return modo == PLAN


def rutas_de(nombre, args):
    """Archivos que toca una edición, para la tarjeta de permiso."""
    if nombre == 'apply_patch':
        return list(dict.fromkeys(m.strip() for m in _RUTA_DE_PARCHE.findall(str(args.get('input') or ''))))[:20]
    ruta = args.get('path')
    return [ruta] if isinstance(ruta, str) else []


class AprobacionConModo:
    """Envuelve la aprobación de la sesión: en «Omitir permisos» concede sin preguntar.

    Lo deja anotado en la conversación para que siempre se sepa qué se aprobó así.
    """

    _PROPIOS = ('interior', 'manager')

    def __init__(self, interior, manager):
        object.__setattr__(self, 'interior', interior)
        object.__setattr__(self, 'manager', manager)

    # Todo lo demás (denied, auto, stdout…) es de la aprobación de dentro.
    def __getattr__(self, nombre):
        return getattr(self.interior, nombre)

    def __setattr__(self, nombre, valor):
        if nombre in self._PROPIOS:
            object.__setattr__(self, nombre, valor)
        else:
            setattr(self.interior, nombre, valor)

    async def __call__(self, args):
        if modo_de(self.manager.task) == OMITIR and args.get('motivo') != 'contenido_externo':
            import shlex
            orden = shlex.join(str(a) for a in args.get('argv') or [])
            self.manager.event(self.manager.task, 'system', 'Aprobado por el modo Omitir permisos: ' + orden[:300])
            return None
        return await self.interior(args)
