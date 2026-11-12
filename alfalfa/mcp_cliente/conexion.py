"""Una conexión MCP (SDK oficial `mcp`) por servidor, con su propio supervisor.

El SDK usa grupos de tareas de anyio: los contextos de transporte y de
ClientSession se abren y se cierran en la misma tarea. Por eso cada conexión
vive en una tarea supervisora que inicializa, descubre las herramientas, queda
a la espera y, al pedir el cierre o al caerse el transporte, sale de los
contextos; el SDK termina entonces el grupo de procesos del servidor stdio.

Las llamadas se envían desde la tarea del motor. El SDK correlaciona cada
respuesta con su id de JSON-RPC, así que dos llamadas no cruzan respuestas.
Una llamada compite con el cierre de la conexión: si ésta cae, la llamada
termina como incierta. Nada aquí reintenta un tools/call.

Entorno stdio: BASE_ENTORNO (la base del SDK, get_default_environment, más
TMPDIR y LANG) y sólo las variables declaradas para ese servidor. El SDK añade
su base a la que recibe; ninguna de las dos lleva la clave de Ollama.
Lanzamiento: siempre dentro de SeatbeltSandbox (seatbelt/), con red sólo si la
configuración del servidor dice red: true; sin sandbox no se inicia.
HTTP: un httpx.AsyncClient propio sin variables de proxy (trust_env=False), sin
seguir redirecciones (las cabeceras configuradas no viajan a otro destino) y
con sólo las cabeceras de ese servidor.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from datetime import timedelta

import httpx

from ..seatbelt import SandboxUnavailable, SeatbeltSandbox
from .validacion import validador

BASE_ENTORNO = ('HOME', 'LOGNAME', 'PATH', 'SHELL', 'TERM', 'USER', 'TMPDIR', 'LANG')
MAX_HERRAMIENTAS = 2000
MAX_PAGINAS = 50
CIERRE_SEGUNDOS = 5.0
STDERR_COLA = 600


class ConexionCerrada(Exception):
    """El transporte cayó o se cerró; la llamada en curso queda incierta."""


class _LecturaVigilada:
    """Envuelve el flujo de lectura del SDK y avisa cuando termina (EOF o cierre)."""

    def __init__(self, flujo, cerrada):
        self._flujo, self._cerrada = flujo, cerrada

    async def __aenter__(self):
        await self._flujo.__aenter__()
        return self

    async def __aexit__(self, *exc):
        self._cerrada.set()
        return await self._flujo.__aexit__(*exc)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self._flujo.__anext__()
        except StopAsyncIteration:
            self._cerrada.set()
            raise

    async def receive(self):
        return await self._flujo.receive()

    async def aclose(self):
        self._cerrada.set()
        await self._flujo.aclose()

    def __getattr__(self, nombre):
        return getattr(self._flujo, nombre)


def entorno_stdio(servidor, ambiente=None):
    ambiente = os.environ if ambiente is None else ambiente
    base = {k: ambiente[k] for k in BASE_ENTORNO if k in ambiente and not ambiente[k].startswith('()')}
    return {**base, **servidor.entorno}


def cliente_http(servidor):
    return httpx.AsyncClient(headers=dict(servidor.cabeceras), trust_env=False, follow_redirects=False,
                             timeout=httpx.Timeout(servidor.tiempo_herramienta, connect=10))


class Conexion:
    def __init__(self, servidor, cwd, limpiar, *, protegidas=()):
        self.servidor = servidor
        self.cwd = cwd
        self.protegidas = protegidas
        self.limpiar = limpiar
        self.sesion = None
        self.herramientas = []
        self.cerrada = asyncio.Event()
        self._parar = asyncio.Event()
        self._lista = None
        self._tarea = None
        # Cola acotada del stderr del servidor stdio, leída de un tubo: un servidor
        # verboso no llena disco ni memoria en una sesión larga.
        self._cola = bytearray()
        self._lector = None

    @property
    def viva(self):
        return self.sesion is not None and not self.cerrada.is_set()

    async def iniciar(self):
        """Inicializa y descubre; ValueError con diagnóstico limpio si no lo logra a tiempo."""
        self._lista = asyncio.get_running_loop().create_future()
        self._tarea = asyncio.create_task(self._supervisar(), name='mcp-' + self.servidor.id)
        try:
            await asyncio.wait_for(asyncio.shield(self._lista), self.servidor.tiempo_inicio)
        except asyncio.TimeoutError:
            await self.cerrar()
            raise ValueError('No respondió a initialize/tools/list en ' + str(int(self.servidor.tiempo_inicio))
                             + ' s.' + await self._cola_stderr()) from None
        except asyncio.CancelledError:
            await self.cerrar()
            raise
        except Exception as exc:
            cola = await self._cola_stderr()
            await self.cerrar()
            raise ValueError(self._motivo(exc) + cola) from None

    def _motivo(self, exc):
        if isinstance(exc, SandboxUnavailable):
            return 'Sandbox Seatbelt no disponible; no se inicia sin aislamiento: ' + str(exc)[:300]
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        return self.limpiar((type(exc).__name__ + ': ' + str(exc))[:300]).rstrip('.') + '.'

    async def _cola_stderr(self):
        if self._lector is not None:
            # El proceso ya salió o va a salir: lo último que escribió llega enseguida.
            await asyncio.wait({self._lector}, timeout=1)
        texto = bytes(self._cola[-STDERR_COLA:]).decode('utf-8', errors='replace').strip()
        return (' Últimas líneas de stderr: ' + self.limpiar(texto)) if texto else ''

    async def _leer_stderr(self, tubo):
        lector = asyncio.StreamReader()
        try:
            transporte, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(lector), tubo)
        except BaseException:
            # Cancelada antes de conectar: el descriptor no queda abierto.
            tubo.close()
            raise
        try:
            while bloque := await lector.read(4096):
                self._cola.extend(bloque)
                del self._cola[:-STDERR_COLA * 4]
        finally:
            transporte.close()

    async def _transporte(self, pila):
        if self.servidor.transporte == 'stdio':
            from mcp.client.stdio import StdioServerParameters, stdio_client
            cwd = self.servidor.cwd or self.cwd
            sandbox = SeatbeltSandbox(cwd, network=self.servidor.red, protegidas=self.protegidas)
            orden = await sandbox.prepare([self.servidor.orden, *self.servidor.argumentos])
            # stderr del servidor a un tubo propio: nunca se copia en bruto a
            # registros ni eventos; sólo su cola, ya limpia, en un diagnóstico.
            leer, escribir = os.pipe()
            self._lector = asyncio.create_task(self._leer_stderr(os.fdopen(leer, 'rb', buffering=0)))
            errlog = os.fdopen(escribir, 'wb', buffering=0)
            parametros = StdioServerParameters(command=orden[0], args=orden[1:],
                                               env=entorno_stdio(self.servidor), cwd=cwd)
            try:
                lectura, escritura = await pila.enter_async_context(stdio_client(parametros, errlog=errlog))
            finally:
                # Sólo el hijo conserva el extremo de escritura: su salida cierra el tubo.
                errlog.close()
        else:
            from mcp.client.streamable_http import streamable_http_client
            cliente = await pila.enter_async_context(cliente_http(self.servidor))
            lectura, escritura, _ = await pila.enter_async_context(
                streamable_http_client(self.servidor.url, http_client=cliente))
        return lectura, escritura

    async def _supervisar(self):
        from mcp import ClientSession
        from mcp.types import Implementation
        try:
            async with AsyncExitStack() as pila:
                lectura, escritura = await self._transporte(pila)
                sesion = await pila.enter_async_context(ClientSession(
                    _LecturaVigilada(lectura, self.cerrada), escritura,
                    client_info=Implementation(name='alfalfacode', version='1')))
                await sesion.initialize()
                herramientas, cursor = [], None
                for _ in range(MAX_PAGINAS):
                    pagina = await sesion.list_tools(cursor=cursor) if cursor else await sesion.list_tools()
                    herramientas.extend(pagina.tools)
                    cursor = pagina.nextCursor
                    if not cursor or len(herramientas) >= MAX_HERRAMIENTAS:
                        break
                self.herramientas = herramientas[:MAX_HERRAMIENTAS]
                self.sesion = sesion
                if not self._lista.done():
                    self._lista.set_result(True)
                parar = asyncio.create_task(self._parar.wait())
                caida = asyncio.create_task(self.cerrada.wait())
                try:
                    await asyncio.wait({parar, caida}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    parar.cancel()
                    caida.cancel()
        except BaseException as exc:
            if self._lista is not None and not self._lista.done():
                self._lista.set_exception(exc if isinstance(exc, Exception) else ConexionCerrada('cancelada'))
                # Quien esperaba pudo irse ya (plazo vencido): no queda sin recoger.
                self._lista.exception()
            if not isinstance(exc, (Exception, asyncio.CancelledError)):
                raise
        finally:
            self.sesion = None
            self.cerrada.set()

    async def llamar(self, nombre, argumentos):
        """tools/call una sola vez; ConexionCerrada si el transporte cae antes de la respuesta."""
        sesion = self.sesion
        if sesion is None or self.cerrada.is_set():
            raise ConexionCerrada('El servidor ' + self.servidor.id + ' no está conectado.')
        envio = asyncio.create_task(self._enviar(sesion, nombre, argumentos))
        caida = asyncio.create_task(self.cerrada.wait())
        try:
            hecho, _ = await asyncio.wait({envio, caida}, timeout=self.servidor.tiempo_herramienta + 5,
                                          return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            envio.cancel()
            caida.cancel()
            await asyncio.gather(envio, caida, return_exceptions=True)
            raise
        caida.cancel()
        if envio not in hecho:
            envio.cancel()
            await asyncio.gather(envio, caida, return_exceptions=True)
            if self.cerrada.is_set():
                raise ConexionCerrada('El servidor ' + self.servidor.id + ' se desconectó durante la llamada.')
            raise asyncio.TimeoutError()
        await asyncio.gather(caida, return_exceptions=True)
        return envio.result()

    async def _enviar(self, sesion, nombre, argumentos):
        """tools/call sin ClientSession.call_tool: éste valida outputSchema con jsonschema.validate,
        que descarga los $ref externos; aquí se valida con el registro sin red de validacion.py."""
        import mcp.types as types
        resultado = await sesion.send_request(
            types.ClientRequest(types.CallToolRequest(
                params=types.CallToolRequestParams(name=nombre, arguments=argumentos))),
            types.CallToolResult,
            request_read_timeout_seconds=timedelta(seconds=self.servidor.tiempo_herramienta))
        esquema = next((h.outputSchema for h in self.herramientas or () if h.name == nombre), None)
        if esquema is not None and not resultado.isError:
            if resultado.structuredContent is None:
                raise RuntimeError('La herramienta declara outputSchema y no devolvió contenido estructurado.')
            try:
                invalido = next(validador(esquema).iter_errors(resultado.structuredContent), None) is not None
            except Exception:
                # Esquema mal formado o con un $ref externo, que no se descarga.
                invalido = True
            if invalido:
                raise RuntimeError('El contenido estructurado no cumple el outputSchema de la herramienta.')
        return resultado

    async def cerrar(self):
        self._parar.set()
        try:
            await self._cerrar_supervisor()
        finally:
            if self._lector is not None and not self._lector.done():
                self._lector.cancel()
                await asyncio.gather(self._lector, return_exceptions=True)

    async def _cerrar_supervisor(self):
        tarea = self._tarea
        if tarea is None or tarea.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(tarea), CIERRE_SEGUNDOS)
        except asyncio.CancelledError:
            tarea.cancel()
            raise
        except Exception:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)
