"""Órdenes en segundo plano, como los «background terminals» de Codex.

Un juego, un servidor o una app que se lanza con `swift run App` no termina: la
orden *es* el programa. Codex (codex-rs/core/src/unified_exec) devuelve el control
al modelo pasados unos segundos (`yield_time_ms`) y deja el proceso vivo con un id;
el modelo vuelve a mirarlo con `write_stdin`, y la interfaz avisa «1 background
terminal running · /ps to view · /stop to close».

Aquí `run_command` con `background: true` hace lo mismo: espera unos segundos por si
falla al arrancar y, si sigue vivo, devuelve su id (`p1`) con lo que lleva escrito.
`command_status` enseña la salida nueva, si terminó y con qué código, y puede
detenerlo. La salida va a un archivo de la sesión, no a una tubería, para que no
haya que leerla mientras nadie mira.

Una orden normal que agota su tiempo (una compilación en release que pasa de los
120 s) tampoco se corta: pasa aquí con lo que llevaba escrito (`adoptar`) y el
modelo espera su final con command_status y wait_for_exit, como Codex con una
orden que sigue viva tras su yield_time_ms. Cortarla dejaba el trabajo a medias
y el reintento volvía a empezar desde cero.

Los procesos siguen vivos entre turnos (el juego sigue abierto cuando el agente
termina) y se cierran con la sesión, como en Codex: al salir AlfalfaCode, con
«Detener» de la app o con `command_status` y `detener`.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import signal
import time
from pathlib import Path

from .procesos import grupo_vivo, grupos_descendientes, leer_con_gracia, senal_arbol

ESPERA_INICIAL = 10.0   # segundos antes de devolver el control (yield_time_ms de Codex)
MAX_ESPERA = 30         # lo que command_status puede esperar a que haya algo nuevo
MAX_ESPERA_FINAL = 300  # lo que puede esperar a que termine (DEFAULT_MAX_BACKGROUND_TERMINAL_TIMEOUT_MS)
MAX_SALIDA = 16000      # caracteres de salida que ve el modelo por consulta
MAX_PROCESOS = 8


class Proceso:
    def __init__(self, ident, proc, argv, cwd, sandbox, registro, purpose):
        self.id, self.proc, self.argv, self.cwd = ident, proc, list(argv), cwd
        self.sandbox, self.registro, self.purpose = sandbox, Path(registro), purpose
        self.desde = time.time()
        self.leido = 0          # bytes del registro que ya vio el modelo
        self.codigo = None
        self.detenido = False

    @property
    def vivo(self):
        return self.proc.returncode is None

    def salida(self, desde=0, maximo=MAX_SALIDA):
        """Lo escrito desde el byte `desde` (como mucho los últimos `maximo` caracteres)."""
        try:
            with open(self.registro, 'rb') as archivo:
                archivo.seek(desde)
                datos = archivo.read()
        except OSError:
            return '', desde, False
        texto = datos.decode(errors='replace')
        recortado = len(texto) > maximo
        return (texto[-maximo:] if recortado else texto), desde + len(datos), recortado

    def fila(self):
        """Lo que la app enseña en «Procesos»."""
        _, total, _ = self.salida(0, 0)
        ultimas = self.salida(max(0, total - 600))[0].splitlines()[-3:]
        return dict(id=self.id, argv=self.argv, cwd=self.cwd, proposito=self.purpose,
                    desde=self.desde, vivo=self.vivo, codigo=self.proc.returncode,
                    sin_sandbox=self.sandbox != 'seatbelt', detenido=self.detenido, ultimas=ultimas)


class ProcesosEnSegundoPlano:
    def __init__(self, carpeta, aviso=None, evento=None):
        """`aviso()` se llama cuando cambia la lista; `evento(texto)` deja una línea en la conversación."""
        self.carpeta = Path(carpeta)
        self.procesos = {}
        self.aviso = aviso or (lambda: None)
        self.evento = evento or (lambda texto: None)
        self._contador = 0
        self._vigilantes = set()

    def vivos(self):
        return [p for p in self.procesos.values() if p.vivo]

    def lista(self):
        return [p.fila() for p in self.procesos.values()]

    def hay_sitio(self):
        return len(self.vivos()) < MAX_PROCESOS

    def nuevo_registro(self):
        if len(self.vivos()) >= MAX_PROCESOS:
            raise ValueError('Ya hay ' + str(MAX_PROCESOS) + ' procesos en segundo plano. Detén alguno con '
                             'command_status (detener: true) antes de lanzar otro. No se ejecutó.')
        self._contador += 1
        ident = 'p' + str(self._contador)
        self.carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
        ruta = self.carpeta / (ident + '.log')
        descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        return ident, ruta, descriptor

    def registrar(self, ident, proc, argv, cwd, sandbox, ruta, purpose):
        proceso = Proceso(ident, proc, argv, cwd, sandbox, ruta, purpose)
        self.procesos[ident] = proceso
        vigilante = asyncio.ensure_future(self._vigilar(proceso))
        self._vigilantes.add(vigilante)
        vigilante.add_done_callback(self._vigilantes.discard)
        self.aviso()
        return proceso

    def adoptar(self, proc, previo, argv, cwd, sandbox, purpose):
        """Una orden ya lanzada con su salida en una tubería pasa a segundo plano.

        Lo que ya escribió va al registro y una tarea sigue copiando la tubería en él.
        Mira antes `hay_sitio()`: con MAX_PROCESOS vivos lanza ValueError.
        Un proceso que acaba de terminar también se adopta: command_status da su código."""
        ident, ruta, descriptor = self.nuevo_registro()
        try:
            os.write(descriptor, bytes(previo))
        except OSError:
            pass
        bomba = asyncio.ensure_future(self._bombear(proc, descriptor))
        self._vigilantes.add(bomba)
        bomba.add_done_callback(self._vigilantes.discard)
        proceso = self.registrar(ident, proc, argv, cwd, sandbox, ruta, purpose)
        proceso.leido = len(previo)
        return proceso

    @staticmethod
    async def _bombear(proc, descriptor):
        try:
            while chunk := await leer_con_gracia(proc, proc.stdout.read(65536)):
                os.write(descriptor, chunk)
        except (OSError, ValueError):
            pass
        finally:
            os.close(descriptor)

    async def _vigilar(self, proceso):
        codigo = await proceso.proc.wait()
        proceso.codigo = codigo
        if not proceso.detenido:
            self.evento('El proceso ' + proceso.id + ' (' + shlex.join(proceso.argv)[:200] + ') terminó con código '
                        + str(codigo) + '.')
        self.aviso()

    def estado(self, ident, *, detener=False):
        proceso = self.procesos.get(ident)
        if proceso is None:
            conocidos = ', '.join(self.procesos) or 'ninguno'
            raise ValueError('No hay un proceso «' + str(ident) + '» en esta sesión (hay: ' + conocidos + ').')
        texto, fin, recortado = proceso.salida(proceso.leido)
        proceso.leido = fin
        return {'proceso': proceso.id, 'argv': proceso.argv, 'vivo': proceso.vivo, 'exit_code': proceso.proc.returncode,
                'segundos': round(time.time() - proceso.desde), 'salida_nueva': texto, 'truncated': recortado,
                'detenido': proceso.detenido}

    async def esperar(self, ident, segundos, hasta_el_final=False):
        """Espera a que el proceso termine o escriba algo nuevo, como mucho `segundos`.

        Con `hasta_el_final` no vuelve con la primera línea nueva: espera a que termine
        (una compilación), como mucho MAX_ESPERA_FINAL segundos."""
        proceso = self.procesos.get(ident)
        if proceso is None or not proceso.vivo or segundos <= 0:
            return
        if hasta_el_final:
            limite = time.monotonic() + min(segundos, MAX_ESPERA_FINAL)
            while proceso.vivo and time.monotonic() < limite:
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.3)  # lo último que quedaba en la tubería
            return
        _, inicio, _ = proceso.salida(proceso.leido, 0)
        limite = time.monotonic() + min(segundos, MAX_ESPERA)
        while proceso.vivo and time.monotonic() < limite:
            await asyncio.sleep(0.25)
            if proceso.salida(inicio, 0)[1] > inicio:
                await asyncio.sleep(0.25)  # que termine la línea
                return

    async def detener(self, ident):
        proceso = self.procesos.get(ident)
        if proceso is None:
            raise ValueError('No hay un proceso «' + str(ident) + '» en esta sesión.')
        if proceso.vivo:
            proceso.detenido = True
            await _matar(proceso.proc)
            self.evento('Proceso ' + proceso.id + ' detenido: ' + shlex.join(proceso.argv)[:200])
        self.aviso()
        return proceso

    async def detener_todos(self):
        for proceso in list(self.vivos()):
            await self.detener(proceso.id)


async def _matar(proc):
    """Como TerminalManager.terminate: el grupo y los de sus descendientes, primero con SIGTERM."""
    otros = grupos_descendientes(proc.pid)
    for señal, espera in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if not senal_arbol(proc.pid, otros, señal):
            return
        try:
            await asyncio.wait_for(proc.wait(), espera)
        except asyncio.TimeoutError:
            continue
        if not any(grupo_vivo(g) for g in otros):
            return
        await asyncio.sleep(espera)
        if not any(grupo_vivo(g) for g in otros):
            return
