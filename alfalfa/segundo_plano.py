"""Órdenes en segundo plano, como los «background terminals» de Codex.

Un juego, un servidor o una app que se lanza con `swift run App` no termina: la
orden *es* el programa. Codex (codex-rs/core/src/unified_exec) devuelve el control
al modelo pasados unos segundos (`yield_time_ms`) y deja el proceso vivo con un id;
el modelo vuelve a mirarlo con `write_stdin`, y la interfaz avisa «1 background
terminal running · /ps to view · /stop to close».

Aquí `run_command` con `background: true` hace lo mismo: espera unos segundos por si
falla al arrancar y, si sigue vivo, devuelve su id (`p1`) con lo que lleva escrito.
`command_status` enseña la salida nueva, si terminó y con qué código, y puede
detenerlo.

La salida (stdout y stderr juntos, en una sola tubería y en el orden en que se
escribieron) la lee el arnés y la vuelca a `procesos/pN.log` en la carpeta de la
sesión, con tope: el registro guarda como mucho TOPE_REGISTRO bytes y, al pasarlo,
se queda con la segunda mitad (lo último) y una marca «[… N bytes anteriores
recortados]» al principio. Antes el proceso escribía directo en el archivo y un
`npm run dev` o un `tail -f` de horas podía llenar el disco. Leer siempre la
tubería también evita que el proceso se bloquee: nunca se queda escribiendo en
una tubería llena. Si el arnés muere, la tubería se queda sin lector y el proceso
recibe SIGPIPE (o EPIPE) al escribir, en vez de quedarse colgado; los que callan
se recogen al volver a abrir la sesión (`grupos.json` y procesos.recoger_huerfanos).

Limpieza: el registro de un proceso vivo no se borra nunca. El de uno terminado
se guarda mientras command_status puede pedirlo, es decir, mientras la sesión
sigue abierta y está entre los MAX_TERMINADOS terminados más recientes. Al cerrar
la sesión se borran todos (al reanudarla los ids empiezan otra vez en p1 y no hay
forma de pedirlos), y al abrirla se borran los que dejó un cierre brusco.

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
import json
import os
import re
import shlex
import signal
import time
from pathlib import Path

from .procesos import (
    cerrar_tuberias,
    esperar_salida,
    grupo_vivo,
    grupos_descendientes,
    recoger_huerfanos,
    registro_de_grupo,
    senal_arbol,
)
from .seatbelt import AISLADAS

ESPERA_INICIAL = 10.0   # segundos antes de devolver el control (yield_time_ms de Codex)
MAX_ESPERA = 30         # lo que command_status puede esperar a que haya algo nuevo
MAX_ESPERA_FINAL = 300  # lo que puede esperar a que termine (DEFAULT_MAX_BACKGROUND_TERMINAL_TIMEOUT_MS)
MAX_SALIDA = 16000      # caracteres de salida que ve el modelo por consulta
MAX_PROCESOS = 8
# Bytes de salida que se guardan en disco por proceso. Al pasarlo, el registro se queda con la
# segunda mitad: siempre bastante más que los MAX_SALIDA caracteres que ve el modelo por consulta.
TOPE_REGISTRO = 1024 * 1024
MAX_TERMINADOS = 16     # procesos terminados cuya salida se puede seguir pidiendo en la sesión
GRACIA_VACIADO = 1.0    # lo que se espera, tras salir el proceso, a que llegue lo último de la tubería
GRUPOS = 'grupos.json'  # los grupos vivos, para recogerlos si el arnés muere de golpe
_SOBRANTE = re.compile(r'p\d+\.log|\.p\d+\.log\.tmp|\.grupos\.json\.tmp')


def marca_recorte(bytes_recortados):
    return '[… ' + str(bytes_recortados) + ' bytes anteriores recortados]\n'


def _escribir_todo(descriptor, datos):
    vista = memoryview(datos)
    while vista:
        vista = vista[os.write(descriptor, vista):]


class Registro:
    """La salida de un proceso en disco, con tope: guarda lo último que escribió.

    Las posiciones son lógicas (bytes recibidos desde el principio), así que lo que
    ya leyó el modelo sigue valiendo aunque el archivo se recorte. El archivo es la
    marca de recorte, si la hay, seguida de los bytes [inicio, escritos)."""

    def __init__(self, ruta, tope=None):
        self.ruta = Path(ruta)
        self.tope = max(4096, int(tope or TOPE_REGISTRO))
        self.escritos = 0   # bytes recibidos en total
        self.inicio = 0     # primer byte que sigue en el archivo
        self.cabecera = 0   # bytes de la marca al principio del archivo
        self.tamano = 0     # bytes de salida en el archivo, sin la marca
        self._fd = self._abrir(os.O_TRUNC)

    def _abrir(self, extra=0):
        return os.open(self.ruta, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | extra, 0o600)

    def escribir(self, datos):
        if not datos or self._fd is None:
            return
        datos = bytes(datos)
        if self.cabecera + self.tamano + len(datos) <= self.tope:
            _escribir_todo(self._fd, datos)
            self.escritos += len(datos)
            self.tamano += len(datos)
        else:
            self._rotar(datos)

    def _rotar(self, datos):
        """Se queda con la mitad del tope (lo último) y la marca con lo recortado hasta ahora.

        Se reescribe en un archivo aparte y se cambia de golpe: quien lea nunca ve medio
        archivo. Cada rotación copia medio tope tras recibir al menos medio tope nuevo."""
        conservar = self.tope // 2
        if len(datos) >= conservar:
            cola = datos[-conservar:]
        else:
            falta = conservar - len(datos)
            cola = (self._leer(max(0, self.tamano - falta), falta) or b'') + datos
        corte = 0
        while corte < min(3, len(cola)) and 0x80 <= cola[corte] <= 0xBF:
            corte += 1  # sin empezar a media letra (bytes de continuación de UTF-8)
        cola = cola[corte:]
        escritos = self.escritos + len(datos)
        inicio = escritos - len(cola)
        cabecera = marca_recorte(inicio).encode()
        temporal = self.ruta.with_name('.' + self.ruta.name + '.tmp')
        fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            _escribir_todo(fd, cabecera + cola)
        finally:
            os.close(fd)
        os.replace(temporal, self.ruta)
        self.escritos, self.inicio, self.cabecera, self.tamano = escritos, inicio, len(cabecera), len(cola)
        os.close(self._fd)  # el del archivo de antes, ya sustituido
        self._fd = None
        self._fd = self._abrir()

    def _leer(self, posicion, cuantos=None):
        """Bytes de salida desde `posicion` dentro del archivo (sin contar la marca); None si falta."""
        try:
            with open(self.ruta, 'rb') as archivo:
                archivo.seek(self.cabecera + posicion)
                return archivo.read() if cuantos is None else archivo.read(cuantos)
        except OSError:
            return None

    def leer(self, desde=0):
        """(bytes desde la posición lógica `desde`, posición final, bytes perdidos antes de ellos)."""
        desde = max(0, min(int(desde), self.escritos))
        perdidos = max(0, self.inicio - desde)
        posicion = max(desde, self.inicio) - self.inicio
        datos = self._leer(posicion, self.tamano - posicion)
        if datos is None:
            return b'', desde, 0
        return datos, self.inicio + posicion + len(datos), perdidos

    def cola(self, cuantos):
        """Los últimos `cuantos` bytes, sin marca."""
        return self._leer(max(0, self.tamano - cuantos)) or b''

    def cerrar(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def borrar(self):
        self.cerrar()
        for ruta in (self.ruta, self.ruta.with_name('.' + self.ruta.name + '.tmp')):
            try:
                ruta.unlink()
            except FileNotFoundError:
                pass


class Proceso:
    def __init__(self, ident, proc, argv, cwd, sandbox, registro, purpose, grupo=None):
        self.id, self.proc, self.argv, self.cwd = ident, proc, list(argv), cwd
        self.sandbox, self.registro, self.purpose = sandbox, registro, purpose
        self.desde = time.time()
        self.grupo = grupo if isinstance(grupo, dict) else registro_de_grupo(proc.pid, self.desde)
        self.leido = registro.escritos  # posición lógica hasta la que ya vio el modelo
        self.codigo = None
        self.fin = None
        self.detenido = False
        self.bomba = None

    @property
    def vivo(self):
        return self.proc.returncode is None

    @property
    def bombeando(self):
        """¿Sigue abierta la tubería? Un nieto puede tenerla después de salir el proceso."""
        return self.bomba is not None and not self.bomba.done()

    async def vaciar(self, gracia=GRACIA_VACIADO):
        """Tras salir el proceso, espera un poco a que lo último de la tubería llegue al registro."""
        if self.bombeando:
            await asyncio.wait({self.bomba}, timeout=gracia)

    def salida(self, desde=0, maximo=MAX_SALIDA):
        """Lo escrito desde el byte `desde` (como mucho los últimos `maximo` caracteres).

        Si parte ya no está en el registro, el texto empieza con la marca de recorte."""
        datos, fin, perdidos = self.registro.leer(desde)
        texto = datos.decode(errors='replace')
        recortado = len(texto) > maximo
        if recortado:
            texto = texto[-maximo:] if maximo > 0 else ''
        if perdidos:
            recortado = True
            texto = marca_recorte(max(perdidos, fin - desde - len(texto.encode()))) + texto
        return texto, fin, recortado

    def fila(self):
        """Lo que la app enseña en «Procesos»."""
        ultimas = self.registro.cola(600).decode(errors='replace').splitlines()[-3:]
        return dict(id=self.id, argv=self.argv, cwd=self.cwd, proposito=self.purpose,
                    desde=self.desde, vivo=self.vivo, codigo=self.proc.returncode,
                    sin_sandbox=self.sandbox not in AISLADAS, detenido=self.detenido, ultimas=ultimas)


class ProcesosEnSegundoPlano:
    def __init__(self, carpeta, aviso=None, evento=None):
        """`aviso()` se llama cuando cambia la lista; `evento(texto)` deja una línea en la conversación.

        Se crea con el cerrojo de la sesión tomado: lo que haya en `carpeta` es de un arnés
        anterior de esta misma sesión que ya no existe."""
        self.carpeta = Path(carpeta)
        self.procesos = {}
        self.aviso = aviso or (lambda: None)
        self.evento = evento or (lambda texto: None)
        self._contador = 0
        self._vigilantes = set()
        self._olvidados = set()
        self._recoger_restos()

    def vivos(self):
        return [p for p in self.procesos.values() if p.vivo]

    def lista(self):
        return [p.fila() for p in self.procesos.values()]

    def hay_sitio(self):
        return len(self.vivos()) < MAX_PROCESOS

    def _recoger_restos(self):
        """Lo que dejó un arnés que murió sin cerrar la sesión (kill -9, apagón): sus procesos,
        si siguen vivos y son de verdad suyos, y sus registros, que ya nadie puede pedir."""
        if not self.carpeta.is_dir():
            return
        try:
            grupos = json.loads((self.carpeta / GRUPOS).read_text())
        except (OSError, ValueError):
            grupos = []
        if isinstance(grupos, list) and grupos:
            recoger_huerfanos(grupos)
        self._borrar_sobrantes()

    def _borrar_sobrantes(self, conservar=()):
        try:
            archivos = list(self.carpeta.iterdir())
        except OSError:
            archivos = []
        for archivo in archivos:
            if _SOBRANTE.fullmatch(archivo.name) and archivo.name not in conservar:
                try:
                    archivo.unlink()
                except OSError:
                    pass
        self._guardar_grupos()

    def _guardar_grupos(self):
        """Los grupos que pueden seguir vivos, para recogerlos si el arnés muere de golpe."""
        filas = [p.grupo for p in self.procesos.values() if p.vivo or p.bombeando]
        ruta = self.carpeta / GRUPOS
        try:
            if not filas:
                ruta.unlink(missing_ok=True)
                return
            temporal = self.carpeta / ('.' + GRUPOS + '.tmp')
            fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            try:
                _escribir_todo(fd, json.dumps(filas).encode())
            finally:
                os.close(fd)
            os.replace(temporal, ruta)
        except OSError:
            pass

    def nuevo_registro(self):
        """(id, Registro) para un proceso que se va a lanzar; el registro se abre antes de lanzarlo."""
        if len(self.vivos()) >= MAX_PROCESOS:
            raise ValueError('Ya hay ' + str(MAX_PROCESOS) + ' procesos en segundo plano. Detén alguno con '
                             'command_status (detener: true) antes de lanzar otro. No se ejecutó.')
        self._contador += 1
        ident = 'p' + str(self._contador)
        self.carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
        return ident, Registro(self.carpeta / (ident + '.log'))

    def registrar(self, ident, proc, argv, cwd, sandbox, registro, purpose, grupo=None):
        """El proceso, lanzado con stdout en una tubería (stderr en la misma), pasa a segundo plano:
        una tarea la lee hasta el final y la vuelca al registro con tope."""
        proceso = Proceso(ident, proc, argv, cwd, sandbox, registro, purpose, grupo)
        self.procesos[ident] = proceso
        proceso.bomba = self._tarea(self._bombear(proceso))
        self._tarea(self._vigilar(proceso))
        self._olvidar_viejos()
        self._guardar_grupos()
        self.aviso()
        return proceso

    def adoptar(self, proc, previo, argv, cwd, sandbox, purpose, grupo=None):
        """Una orden ya lanzada con su salida en una tubería pasa a segundo plano.

        Lo que ya escribió va al registro y una tarea sigue copiando la tubería en él.
        Mira antes `hay_sitio()`: con MAX_PROCESOS vivos lanza ValueError.
        Un proceso que acaba de terminar también se adopta: command_status da su código."""
        ident, registro = self.nuevo_registro()
        try:
            registro.escribir(previo)
        except OSError:
            pass
        return self.registrar(ident, proc, argv, cwd, sandbox, registro, purpose, grupo)

    def _tarea(self, coro):
        tarea = asyncio.ensure_future(coro)
        self._vigilantes.add(tarea)
        tarea.add_done_callback(self._vigilantes.discard)
        return tarea

    async def _bombear(self, proceso):
        """Lee la tubería hasta que la cierran todos los que escriben en ella.

        Sigue leyendo aunque el registro falle (disco lleno): si dejara de leer, el proceso
        se bloquearía al llenarse la tubería."""
        try:
            while chunk := await proceso.proc.stdout.read(65536):
                try:
                    proceso.registro.escribir(chunk)
                except OSError:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            proceso.registro.cerrar()
            self._guardar_grupos()

    async def _vigilar(self, proceso):
        # No con proc.wait(): desde Python 3.12 no vuelve hasta que se cierra la tubería, y un
        # nieto puede retenerla mucho después de salir el proceso.
        codigo = await esperar_salida(proceso.proc)
        proceso.codigo, proceso.fin = codigo, time.time()
        if not proceso.detenido:
            self.evento('El proceso ' + proceso.id + ' (' + shlex.join(proceso.argv)[:200] + ') terminó con código '
                        + str(codigo) + '.')
        self._olvidar_viejos()
        self._guardar_grupos()
        self.aviso()

    def _olvidar_viejos(self):
        """Sólo se pueden seguir pidiendo los MAX_TERMINADOS terminados más recientes; los demás
        se olvidan y su registro se borra. Uno vivo, o con la tubería abierta, nunca."""
        terminados = sorted((p for p in self.procesos.values() if not p.vivo and not p.bombeando),
                            key=lambda p: p.fin or p.desde)
        for proceso in terminados[:max(0, len(terminados) - MAX_TERMINADOS)]:
            proceso.registro.borrar()
            del self.procesos[proceso.id]
            self._olvidados.add(proceso.id)

    def estado(self, ident, *, detener=False):
        proceso = self.procesos.get(ident)
        if proceso is None:
            if ident in self._olvidados:
                raise ValueError('El proceso «' + str(ident) + '» terminó hace rato y su salida ya se borró: se '
                                 'guardan los ' + str(MAX_TERMINADOS) + ' terminados más recientes.')
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
        if proceso is None:
            return
        if not proceso.vivo:
            await proceso.vaciar()  # lo último que quedaba en la tubería
            return
        if segundos <= 0:
            return
        if hasta_el_final:
            limite = time.monotonic() + min(segundos, MAX_ESPERA_FINAL)
            while proceso.vivo and time.monotonic() < limite:
                await asyncio.sleep(0.25)
        else:
            inicio = proceso.registro.escritos
            limite = time.monotonic() + min(segundos, MAX_ESPERA)
            while proceso.vivo and time.monotonic() < limite:
                await asyncio.sleep(0.25)
                if proceso.registro.escritos > inicio:
                    await asyncio.sleep(0.25)  # que termine la línea
                    return
        if not proceso.vivo:
            await proceso.vaciar()

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
        # Procesos que salieron pero cuya tubería sigue abierta: un nieto (`servidor &`) que
        # se quedó en su grupo. Se recogen como los huérfanos, sólo si el grupo es el nuestro.
        restos = [p for p in self.procesos.values() if not p.vivo and p.bombeando]
        if restos:
            await asyncio.to_thread(recoger_huerfanos, [p.grupo for p in restos])
            for proceso in restos:
                cerrar_tuberias(proceso.proc)
                await proceso.vaciar()
        self._guardar_grupos()

    def cerrar(self):
        """Al cerrar la sesión: borra los registros, que ya nadie puede pedir (al reanudarla los ids
        vuelven a empezar en p1). El de un proceso vivo se queda: no debería haberlo tras
        detener_todos, y si lo hay lo recoge el siguiente arranque como a un huérfano."""
        for proceso in list(self.procesos.values()):
            if proceso.vivo:
                continue
            if proceso.bombeando:
                cerrar_tuberias(proceso.proc)
                proceso.bomba.cancel()
            proceso.registro.borrar()
            del self.procesos[proceso.id]
        self._borrar_sobrantes(conservar={p.registro.ruta.name for p in self.procesos.values()})
        try:
            self.carpeta.rmdir()  # sólo si quedó vacía
        except OSError:
            pass


async def _matar(proc):
    """Como TerminalManager.terminate: el grupo y los de sus descendientes, primero con SIGTERM."""
    otros = grupos_descendientes(proc.pid)
    for señal, espera in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if not senal_arbol(proc.pid, otros, señal):
            return
        # Sin proc.wait(), que espera también a que se cierre la tubería.
        await esperar_salida(proc, espera)
        if proc.returncode is None:
            continue
        if not any(grupo_vivo(g) for g in otros):
            return
        await asyncio.sleep(espera)
        if not any(grupo_vivo(g) for g in otros):
            return
