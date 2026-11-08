"""Cierre de grupos de procesos: agentes, comandos y herramientas externas.

Cada agente y cada orden arrancan en su propia sesión (start_new_session), así
que su pid es también el id de su grupo. Matar sólo al líder dejaba vivos a sus
nietos (servidores de desarrollo, demonios de compilación, servidores http de
las pruebas), y éstos retenían stdout/stderr: el lector esperaba un EOF que no
llegaba y el puesto del agente quedaba ocupado para siempre.

En Python 3.11, `Process.wait()` llamado antes de que salga el líder no
vuelve hasta que se cierran también las tuberías; por eso aquí se mira
`returncode` (que se fija al salir el líder) y, pasada la gracia, se cierran
las tuberías a mano.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time

# Tras salir el líder, lo que queda en las tuberías se lee en este plazo; si un
# nieto las retiene más, se mata el grupo y se deja de leer.
GRACIA_TUBERIAS = 5.0
ESPERA_TERM = 3.0


def senal_grupo(pgid, sig):
    """Envía una señal al grupo; False si ya no existe o no es nuestro."""
    if not isinstance(pgid, int) or pgid <= 1:
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def grupo_vivo(pgid):
    return senal_grupo(pgid, 0)


def grupos_descendientes(pid):
    """Grupos de los descendientes de `pid` que se fueron a su propio grupo.

    SwiftPM (llbuild) lanza `swift-driver` y `swift-frontend` con setpgid: matar el
    grupo de la orden dejaba la compilación viva, ocupando la CPU, y el siguiente
    intento competía con ella hasta quedarse también sin tiempo. Se leen antes de
    matar, porque al morir el padre los hijos pasan a launchd y ya no se reconocen.
    Sólo se devuelven grupos cuyo líder es un descendiente: nunca uno ajeno."""
    if not isinstance(pid, int) or pid <= 1:
        return []
    try:
        salida = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid='], capture_output=True, text=True,
                                timeout=5, env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'}).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    hijos, grupo = {}, {}
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) != 3:
            continue
        try:
            hijo, padre, pgid = (int(x) for x in partes)
        except ValueError:
            continue
        hijos.setdefault(padre, []).append(hijo)
        grupo[hijo] = pgid
    descendientes, pendientes = set(), list(hijos.get(pid, ()))
    while pendientes:
        actual = pendientes.pop()
        if actual in descendientes:
            continue
        descendientes.add(actual)
        pendientes.extend(hijos.get(actual, ()))
    return sorted({grupo[d] for d in descendientes if grupo.get(d) in descendientes and grupo[d] != pid})


def senal_arbol(pid, grupos, sig):
    """La señal al grupo de la orden y a los grupos de sus descendientes."""
    vivo = senal_grupo(pid, sig)
    for pgid in grupos:
        vivo = senal_grupo(pgid, sig) or vivo
    return vivo


async def esperar_salida(proc, plazo=None):
    """Espera a que salga el líder sin depender de que se cierren sus tuberías."""
    fin = None if plazo is None else time.monotonic() + plazo
    espera = asyncio.ensure_future(proc.wait())
    try:
        while proc.returncode is None and not espera.done():
            resto = 0.05 if fin is None else min(0.05, fin - time.monotonic())
            if resto <= 0:
                break
            await asyncio.wait({espera}, timeout=resto)
        if espera.done() and not espera.cancelled() and espera.exception() is None:
            return espera.result()
        return proc.returncode
    finally:
        if not espera.done():
            espera.cancel()


async def esperar_y_gracia(proc, gracia=None):
    """Vuelve cuando el líder salió y pasó la gracia para vaciar sus tuberías."""
    await esperar_salida(proc)
    await asyncio.sleep(GRACIA_TUBERIAS if gracia is None else gracia)


def cerrar_tuberias(proc):
    """Cierra stdin/stdout/stderr del lado del arnés: los lectores reciben EOF."""
    transporte = getattr(proc, '_transport', None)
    if transporte is not None:
        try:
            transporte.close()
        except Exception:
            pass


async def terminar_grupo(proc, espera=ESPERA_TERM, gracia=1.0):
    """SIGTERM al grupo, hasta `espera` s, SIGKILL; siempre, aunque el líder ya saliera."""
    pgid = getattr(proc, 'pid', None)
    otros = grupos_descendientes(pgid)
    senal_arbol(pgid, otros, signal.SIGTERM)
    inicio = time.monotonic()
    await esperar_salida(proc, espera)
    while time.monotonic() - inicio < espera and senal_arbol(pgid, otros, 0):
        await asyncio.sleep(0.05)
    senal_arbol(pgid, otros, signal.SIGKILL)
    if proc.returncode is None:
        await esperar_salida(proc, espera)
    # Un nieto que se fue a otra sesión puede seguir con las tuberías abiertas:
    # se da la gracia para que llegue lo pendiente y luego se cierran igual
    # (lo ya recibido sigue en el búfer de cada lector, que ve EOF).
    fin = time.monotonic() + gracia
    while time.monotonic() < fin and tuberias_abiertas(proc):
        await asyncio.sleep(0.05)
    cerrar_tuberias(proc)


def tuberias_abiertas(proc):
    """¿Queda abierta alguna tubería de lectura (stdout/stderr) del proceso?"""
    pipes = getattr(getattr(proc, '_transport', None), '_pipes', None)
    if not isinstance(pipes, dict):
        return False
    return any(fd != 0 and p is not None and not getattr(p, 'disconnected', True) for fd, p in pipes.items())


async def terminar_lector(proc, lector, gracia=None, vacio=''):
    """El resultado de un lector de tubería sin colgarse por un nieto que la retiene."""
    gracia = GRACIA_TUBERIAS if gracia is None else gracia
    if not lector.done():
        await esperar_salida(proc)
        try:
            await asyncio.wait_for(asyncio.shield(lector), gracia)
        except asyncio.TimeoutError:
            await terminar_grupo(proc)
            if not lector.done():
                try:
                    await asyncio.wait_for(asyncio.shield(lector), 1)
                except asyncio.TimeoutError:
                    lector.cancel()
    await asyncio.gather(lector, return_exceptions=True)
    if lector.cancelled() or lector.exception() is not None:
        return vacio
    return lector.result()


async def leer_con_gracia(proc, lectura, gracia=None):
    """Espera una lectura de tubería; b'' si el líder salió y nada llega en `gracia` s."""
    gracia = GRACIA_TUBERIAS if gracia is None else gracia
    lectura = asyncio.ensure_future(lectura)
    try:
        while not lectura.done():
            if proc.returncode is not None:
                hecho, _ = await asyncio.wait({lectura}, timeout=gracia)
                if not hecho:
                    return b''
                break
            await asyncio.wait({lectura}, timeout=0.25)
        return lectura.result()
    finally:
        if not lectura.done():
            lectura.cancel()


async def leer_linea(proc, gracia=None):
    """readline de stdout que no espera para siempre a un nieto que la retiene."""
    return await leer_con_gracia(proc, proc.stdout.readline(), gracia)


def registro_de_grupo(pid, desde):
    """Lo que se guarda para reconocer el grupo tras un cierre brusco."""
    return {'pgid': pid, 'pid': pid, 'desde': desde, 'hasta': time.time()}


def _inicio(texto):
    try:
        return time.mktime(time.strptime(' '.join(texto.split()), '%a %b %d %H:%M:%S %Y'))
    except (ValueError, OverflowError):
        return None


def miembros_de_grupos():
    """{pgid: [(pid, inicio)]} de todos los procesos, leído una vez con ps."""
    try:
        salida = subprocess.run(['ps', '-axo', 'pid=,pgid=,lstart='], capture_output=True, text=True,
                                timeout=10, env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'}).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    grupos = {}
    for linea in salida.splitlines():
        partes = linea.split(None, 2)
        if len(partes) < 3:
            continue
        try:
            pid, pgid = int(partes[0]), int(partes[1])
        except ValueError:
            continue
        grupos.setdefault(pgid, []).append((pid, _inicio(partes[2])))
    return grupos


def es_nuestro(registro, miembros):
    """¿El grupo vivo es el que arrancó el arnés, o su pid se reutilizó?

    Si el líder sigue vivo, su arranque debe caer en el momento guardado. Si ya
    no está, el id de grupo no puede reutilizarse mientras el grupo exista, así
    que basta con que nadie en él sea anterior al arranque guardado.
    """
    if not miembros or not isinstance(registro, dict):
        return False
    pgid = registro.get('pgid')
    try:
        desde = float(registro.get('desde'))
        hasta = float(registro.get('hasta', desde))
    except (TypeError, ValueError):
        return False
    lider = next((inicio for pid, inicio in miembros if pid == pgid), 'falta')
    if lider != 'falta':
        return lider is not None and desde - 2 <= lider <= hasta + 2
    return all(inicio is not None and inicio >= desde - 2 for _, inicio in miembros)


def matar_grupos(pgids, espera=ESPERA_TERM):
    """Versión síncrona para el arranque y el apagado: TERM, espera, KILL."""
    vivos = [p for p in set(pgids) if senal_grupo(p, signal.SIGTERM)]
    fin = time.monotonic() + espera
    while vivos and time.monotonic() < fin:
        time.sleep(0.05)
        vivos = [p for p in vivos if grupo_vivo(p)]
    for pgid in vivos:
        senal_grupo(pgid, signal.SIGKILL)
    return vivos


def recoger_huerfanos(registros, espera=ESPERA_TERM):
    """Mata los grupos guardados que sigan vivos y sean de verdad nuestros."""
    registros = [r for r in registros if isinstance(r, dict) and isinstance(r.get('pgid'), int)]
    if not registros:
        return []
    grupos = miembros_de_grupos()
    nuestros = [r['pgid'] for r in registros if es_nuestro(r, grupos.get(r['pgid']))]
    matar_grupos(nuestros, espera)
    return nuestros


def pid_vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # existe, aunque sea de otro usuario
    except (ProcessLookupError, OverflowError, TypeError, ValueError):
        return False


async def comunicar(proc, plazo, entrada=None):
    """communicate() con plazo; si vence o se cancela, mata el grupo entero y lo espera.

    `proc.kill()` sólo alcanzaba al líder: los hijos de git (filtros, lfs,
    credenciales), de npm o de codex seguían vivos con las tuberías abiertas.
    El proceso debe haberse lanzado con start_new_session=True.
    """
    try:
        comunicacion = proc.communicate() if entrada is None else proc.communicate(entrada)
        return await asyncio.wait_for(comunicacion, plazo)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await terminar_grupo(proc)
        raise
