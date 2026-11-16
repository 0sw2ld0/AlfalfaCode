"""Si el sandbox del sistema arranca aquí: Seatbelt en macOS, bwrap en Linux.

Las pruebas que lanzan órdenes o servidores aislados de verdad se saltan, con el motivo, donde no hay
sandbox: fuera de macOS y Linux, sin /usr/bin/bwrap o donde bwrap no arranca (en Ubuntu 24.04, sin el
perfil de AppArmor que explica docs/USO.md).
"""
import asyncio
import os
import sys
import tempfile
from functools import lru_cache


@lru_cache(maxsize=1)
def motivo_sin_sandbox():
    """None si el sandbox del sistema arranca; si no, por qué no."""
    from alfalfa.seatbelt import SANDBOX_EXEC, SandboxUnavailable
    from alfalfa.seatbelt.bwrap import BWRAP, BwrapSandbox
    if sys.platform == 'darwin':
        return None if os.access(SANDBOX_EXEC, os.X_OK) else 'requiere ' + SANDBOX_EXEC
    if not sys.platform.startswith('linux'):
        return 'requiere macOS (Seatbelt) o Linux (bwrap)'
    if not os.access(BWRAP, os.X_OK):
        return 'requiere ' + BWRAP + ' (sudo apt install bubblewrap)'
    try:
        asyncio.run(BwrapSandbox(tempfile.mkdtemp(prefix='bwrap-sonda-')).prepare(['true']))
    except SandboxUnavailable as exc:
        return 'bwrap no arranca aquí: ' + str(exc)[:300]
    return None


def procesos_con(marca):
    """Los pids, vistos desde fuera, de los procesos que llevan `marca` entre sus argumentos.

    Con bwrap cada orden tiene su propio espacio de procesos: el pid que anota un proceso del sandbox
    (os.getpid()) es el de dentro y fuera puede ser otro proceso. bwrap, que lleva la orden entera entre
    sus argumentos, no cuenta."""
    import subprocess
    salida = subprocess.run(['ps', '-axww', '-o', 'pid=,args='], capture_output=True, text=True, timeout=10,
                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}).stdout
    pids = []
    for linea in salida.splitlines():
        pid, _, args = linea.strip().partition(' ')
        if marca in args and os.path.basename(args.split(' ', 1)[0]) != 'bwrap' and pid.isdigit():
            pids.append(int(pid))
    return pids
