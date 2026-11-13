"""Heurística de denegación probable del sandbox para la política on-request.

Derivado de codex-rs/sandboxing/src/denial.rs (OpenAI Codex, Apache-2.0; ver
alfalfa/seatbelt/NOTICE, LICENSE y PROCEDENCIA.md). Se conserva el
orden de evaluación de is_likely_sandbox_denied:

1. Sin sandbox o código 0: no es una denegación.
2. Una palabra indicadora en stderr, stdout o la salida agregada: sí lo es,
   aunque el código sea 2, 126 o 127.
3. Códigos 2, 126 y 127 sin palabra indicadora: rechazo rápido.
4. Lo demás: no.

Diferencias intencionales con Codex:
- Se omite el caso SIGSYS de seccomp, que sólo existe en Linux.
- Un código ausente (la orden no llegó a terminar por sí misma) no es una
  denegación: Codex siempre tiene un entero.
- NativeRuntime mezcla stderr en stdout; la terminal pasa esa salida como
  agregada y la función sigue aceptando las tres por separado.
- macOS: dentro de Seatbelt, `open` no llega a Launch Services y falla con
  mensajes que no hablan del sandbox («Unable to find application named»,
  «No application knows how to open URL», LSOpenURLsWithRole). Se tratan como
  denegación probable para ofrecer el reintento sin sandbox; si la app de verdad
  no existe, el reintento lo dirá igual.
- macOS: si `sandbox-exec` no encuentra el programa («sandbox-exec: execvp() of
  '…' failed: No such file or directory», código 71), el texto dice «sandbox»
  sólo porque es quien lanza la orden. No es un bloqueo: sin sandbox fallaría
  igual, así que no se ofrece el reintento.
- La palabra «sandbox» sola no cuenta: aparecía en salidas normales (ruff o
  un grep que citan código sobre el sandbox) y ofrecía repetir sin sandbox una
  orden que sólo había fallado por sí misma. Cuentan los avisos propios de
  Seatbelt: una línea «Sandbox: proceso(pid) deny(1) …», «deny(1)» y
  «sandbox_apply» (`_SEATBELT`).
"""
from __future__ import annotations

import re

SANDBOX_DENIED_KEYWORDS = (
    'operation not permitted',
    'permission denied',
    'read-only file system',
    'seccomp',
    'landlock',
    'failed to write file',
    # Launch Services bloqueado por Seatbelt (open, open -a).
    'unable to find application named',
    'no application knows how to open',
    'lsopenurlswithrole',
    # open de una .app recién compilada: Launch Services no ve el ejecutable desde el sandbox.
    'klsnoexecutableerr',
    'cannot be opened for an unexpected reason',
)
QUICK_REJECT_EXIT_CODES = (2, 126, 127)
# Lo que escribe Seatbelt cuando deniega: «Sandbox: swift-build(123) deny(1) file-write-create /x».
_SEATBELT = re.compile(r'(?:^|\n)\s*sandbox: |deny\(1\)|sandbox_apply', re.IGNORECASE)
# sandbox-exec no pudo lanzar el programa por algo ajeno al sandbox.
_LANZAMIENTO_FALLIDO = re.compile(
    r"sandbox-exec: execvp\(\) of '.*?' failed: (?:no such file or directory|not a directory|exec format error)",
    re.IGNORECASE)


def sin_fallos_de_lanzamiento(texto):
    """El texto sin las líneas de sandbox-exec que sólo dicen que el programa no existe."""
    return _LANZAMIENTO_FALLIDO.sub('', str(texto or ''))


def is_likely_executor_managed_sandbox_denied(exit_code, stdout='', stderr='', aggregated=''):
    if exit_code == 0:
        return False
    for section in (stderr, stdout, aggregated):
        text = sin_fallos_de_lanzamiento(section)
        lower = text.lower()
        if any(needle in lower for needle in SANDBOX_DENIED_KEYWORDS) or _SEATBELT.search(text):
            return True
    return False


def is_likely_sandbox_denied(sandbox_type, exit_code, stdout='', stderr='', aggregated=''):
    if sandbox_type in (None, 'none') or exit_code == 0 or type(exit_code) is not int:
        return False
    if is_likely_executor_managed_sandbox_denied(exit_code, stdout, stderr, aggregated):
        return True
    if exit_code in QUICK_REJECT_EXIT_CODES:
        return False
    return False
