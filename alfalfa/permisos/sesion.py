"""Aprobaciones «para esta sesión», como `ReviewDecision::ApprovedForSession` de Codex.

Codex (codex-rs/core/src/tools/sandboxing.rs, `with_cached_approval`) guarda la
decisión por una clave serializada con la orden exacta, la carpeta y los permisos
de sandbox que concede; si todas las claves de una petición ya están aprobadas
para la sesión, no vuelve a preguntar. Aquí igual:

- Una orden: argv exacto, carpeta y si sale del sandbox o pide red. Aprobar el
  reintento sin sandbox de una orden guarda la clave «sin sandbox»: la próxima vez
  esa misma orden sale del sandbox directamente, sin el intento que iba a fallar.
- Una edición (modo Manual): una clave por archivo; aprobar una edición para la
  sesión deja editar esos archivos sin preguntar otra vez.
- No se guarda nunca lo peligroso (rm -rf, sudo…) ni lo que una regla del
  usuario manda preguntar siempre.

La lista vive en la tarea (`aprobadas_en_sesion`), así que dura lo que la sesión,
también si se cierra la app y se retoma; una bifurcación empieza sin ella. Guarda
la huella sha256 de cada clave, no la clave: una orden aprobada con la contraseña
dentro (`PGPASSWORD=… psql`) no queda escrita en task.json. Las sesiones de
antes guardaban la clave entera: se siguen reconociendo y pasan a huella en
cuanto se aprueba otra cosa.
"""
from __future__ import annotations

import hashlib
import json

CAMPO = 'aprobadas_en_sesion'
MAXIMO = 200
# Lo que devuelve la aprobación cuando el usuario eligió «en esta sesión».
SESION = 'sesion'


def clave_de_orden(argv, cwd=None, sin_sandbox=False, red=False):
    """Sin sandbox ya hay red: la clave no la distingue."""
    carpeta = cwd if isinstance(cwd, str) and cwd not in ('', '.') else '.'
    return json.dumps(['orden', [str(a) for a in argv], carpeta, bool(sin_sandbox), bool(red) and not sin_sandbox],
                      ensure_ascii=False, separators=(',', ':'))


def claves_de_edicion(rutas):
    return [json.dumps(['edicion', str(r)], ensure_ascii=False, separators=(',', ':')) for r in rutas]


PREFIJO = 'sha256:'


def huella(clave):
    return PREFIJO + hashlib.sha256(clave.encode('utf-8', 'surrogatepass')).hexdigest()


def aprobadas(task, claves, *, registrar=True):
    """Todas las claves están aprobadas para la sesión (ninguna clave: no)."""
    guardadas = task.get(CAMPO) if isinstance(task, dict) else None
    aprobada = (bool(claves) and isinstance(guardadas, list)
                and all(huella(c) in guardadas or c in guardadas for c in claves))
    if aprobada and registrar:
        from ..auditoria_equipo import permiso_sesion
        permiso_sesion('permiso_reutilizado', task, claves)
    return aprobada


def recordar(task, claves):
    guardadas = task.get(CAMPO)
    if not isinstance(guardadas, list):
        guardadas = task[CAMPO] = []
    # Las de una sesión de antes, con la clave entera, pasan a huella.
    guardadas[:] = list(dict.fromkeys(g if not isinstance(g, str) or g.startswith(PREFIJO) else huella(g)
                                      for g in guardadas))
    for c in claves:
        if huella(c) not in guardadas:
            guardadas.append(huella(c))
    del guardadas[:-MAXIMO]
    from ..auditoria_equipo import permiso_sesion
    permiso_sesion('permiso_guardado', task, claves)
