"""Cliente mínimo de Language Server Protocol sobre stdio.

Sólo lo que hace falta para ver los errores de un archivo recién editado:
`initialize`, abrir o cambiar el documento con su texto entero y recoger sus
diagnósticos. Si el servidor ofrece diagnósticos «pull» (`textDocument/diagnostic`)
se piden; si no, se espera al `publishDiagnostics` que manda él solo.

El servidor arranca una vez por sesión y se queda vivo entre ediciones. Las
peticiones que el servidor hace al cliente (`workspace/configuration`,
`client/registerCapability`…) se contestan con lo mínimo para que no se quede
esperando.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from .. import procesos

MAX_MENSAJE = 32 * 1024 * 1024
# Lo más que se espera, dentro del plazo de la edición, a que un servidor diga que terminó de cargar.
ESPERA_CARGA = 5.0
# Segundos tras arrancar en los que un servidor con `carga` puede estar aún cargando el proyecto.
CALENTAMIENTO = 30.0


METODO_DESCONOCIDO = -32601


class ServidorCaido(Exception):
    """El servidor no arrancó, se cerró o contestó con un error."""

    def __init__(self, mensaje='', codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


def uri_de(ruta):
    return Path(ruta).absolute().as_uri()


class ClienteLSP:
    def __init__(self, nombre, argv, raiz, *, env=None, opciones=None, idioma_de=None, envolver=None,
                 carga=False):
        self.nombre = nombre
        self.argv = list(argv)
        self.raiz = str(raiz)
        self.env = env
        self.opciones = opciones
        self.idioma_de = idioma_de or (lambda ruta: 'plaintext')
        # El sandbox: corrutina que recibe el argv y lo devuelve envuelto en sandbox-exec.
        self.envolver = envolver
        self.proc = None
        self.capacidades = {}
        # Diagnósticos «pull» (textDocument/diagnostic): None hasta saberlo. sourcekit-lsp no los
        # anuncia en initialize y, si el cliente los admite, deja de publicarlos él solo.
        self._pull = None
        # `workspace/diagnostic/refresh`: el servidor avisa de que sus diagnósticos anteriores no valían.
        self._refresco = asyncio.Event()
        self._refrescos = 0
        # `carga`: el servidor contesta vacío mientras carga el proyecto y luego manda ese aviso
        # (sourcekit-lsp con un Package.swift). Sólo entonces, y sólo al principio, se espera.
        self.carga = carga
        self._calentando_hasta = None
        self._siguiente = 0
        self._respuestas = {}
        self._publicados = {}       # uri -> (versión o None, diagnósticos)
        self._avisos = {}           # uri -> asyncio.Event que se dispara con cada publicación
        self._versiones = {}        # uri -> versión abierta
        self._lector = None
        self._stderr = None
        self._escritura = asyncio.Lock()
        self.lock = asyncio.Lock()  # una edición a la vez por servidor (los subagentes comparten)
        self._arranque = None
        self.caido = None           # motivo, si el servidor ya no sirve en esta sesión

    # --- ciclo de vida -------------------------------------------------

    def arrancar(self):
        """La tarea de arranque (una sola por sesión); sigue aunque nadie la espere."""
        if self._arranque is None:
            self._arranque = asyncio.ensure_future(self._arrancar())
        return self._arranque

    @property
    def listo(self):
        return (self._arranque is not None and self._arranque.done() and not self._arranque.cancelled()
                and self._arranque.exception() is None and self.caido is None)

    async def _arrancar(self):
        try:
            argv = await self.envolver(self.argv) if self.envolver is not None else self.argv
            self.proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.raiz, env=self.env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
                limit=MAX_MENSAJE)
        except (OSError, ValueError) as exc:
            self.caido = 'no arrancó: ' + (str(exc) or type(exc).__name__)
            raise ServidorCaido(self.caido) from None
        self._lector = asyncio.ensure_future(self._leer())
        self._stderr = asyncio.ensure_future(self._tragar(self.proc.stderr))
        uri = uri_de(self.raiz)
        resultado = await self.pedir('initialize', {
            'processId': os.getpid(), 'rootUri': uri, 'rootPath': self.raiz,
            'workspaceFolders': [{'uri': uri, 'name': Path(self.raiz).name or 'raiz'}],
            'clientInfo': {'name': 'AlfalfaCode'},
            'initializationOptions': self.opciones or {},
            'capabilities': {
                'general': {'positionEncodings': ['utf-16']},
                'window': {'workDoneProgress': True},
                'workspace': {'configuration': True, 'workspaceFolders': True, 'diagnostics': {'refreshSupport': True}},
                'textDocument': {
                    'synchronization': {'didSave': True},
                    'publishDiagnostics': {'versionSupport': True},
                    'diagnostic': {'dynamicRegistration': True, 'relatedDocumentSupport': False},
                }}})
        self.capacidades.update((resultado or {}).get('capabilities') or {})
        self._calentando_hasta = asyncio.get_running_loop().time() + CALENTAMIENTO
        await self.avisar('initialized', {})

    async def cerrar(self):
        """shutdown/exit con poco plazo y, si no sale, se mata su grupo."""
        if self._arranque is not None and not self._arranque.done():
            self._arranque.cancel()
            await asyncio.gather(self._arranque, return_exceptions=True)
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.pedir('shutdown', None), 1.0)
                await self.avisar('exit', None)
            await procesos.esperar_salida(proc, 0.5)
            if proc.returncode is None:
                await procesos.terminar_grupo(proc, espera=1.0, gracia=0.1)
        for tarea in (self._lector, self._stderr):
            if tarea is not None and not tarea.done():
                tarea.cancel()
                await asyncio.gather(tarea, return_exceptions=True)
        if proc is not None:
            procesos.cerrar_tuberias(proc)
        self.caido = self.caido or 'cerrado'

    def matar(self):
        """Sin bucle de eventos: SIGKILL al grupo. Para cuando la sesión cierra de golpe."""
        proc = self.proc
        if proc is not None and proc.returncode is None:
            procesos.senal_grupo(proc.pid, 9)

    # --- transporte ----------------------------------------------------

    async def _enviar(self, mensaje):
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            raise ServidorCaido('el servidor no está en marcha')
        cuerpo = json.dumps(dict(mensaje, jsonrpc='2.0'), ensure_ascii=False).encode()
        async with self._escritura:
            try:
                self.proc.stdin.write(b'Content-Length: ' + str(len(cuerpo)).encode() + b'\r\n\r\n' + cuerpo)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                self.caido = 'se cerró'
                raise ServidorCaido(self.caido) from exc

    async def pedir(self, metodo, params):
        self._siguiente += 1
        ident = self._siguiente
        futuro = asyncio.get_running_loop().create_future()
        self._respuestas[ident] = futuro
        try:
            await self._enviar({'id': ident, 'method': metodo, 'params': params})
            return await futuro
        finally:
            self._respuestas.pop(ident, None)

    async def avisar(self, metodo, params):
        await self._enviar({'method': metodo, 'params': params})

    async def _tragar(self, flujo):
        with contextlib.suppress(Exception):
            while await flujo.read(65536):
                pass

    async def _leer(self):
        flujo = self.proc.stdout
        try:
            while True:
                largo = None
                while True:
                    linea = await flujo.readline()
                    if not linea:
                        raise EOFError
                    linea = linea.strip()
                    if not linea:
                        break
                    clave, _, valor = linea.partition(b':')
                    if clave.strip().lower() == b'content-length':
                        largo = int(valor.strip())
                if largo is None or largo > MAX_MENSAJE:
                    raise ValueError('cabecera inválida')
                mensaje = json.loads(await flujo.readexactly(largo))
                await self._atender(mensaje)
        except (EOFError, asyncio.IncompleteReadError, ValueError, ConnectionResetError):
            pass
        finally:
            self.caido = self.caido or 'se cerró'
            for futuro in self._respuestas.values():
                if not futuro.done():
                    futuro.set_exception(ServidorCaido(self.nombre + ' ' + self.caido))
            for evento in self._avisos.values():
                evento.set()

    async def _atender(self, mensaje):
        if not isinstance(mensaje, dict):
            return
        metodo = mensaje.get('method')
        if metodo is None:
            futuro = self._respuestas.get(mensaje.get('id'))
            if futuro is not None and not futuro.done():
                if 'error' in mensaje:
                    error = mensaje['error'] if isinstance(mensaje['error'], dict) else {}
                    futuro.set_exception(ServidorCaido(str(error.get('message') or 'error')[:300],
                                                       error.get('code')))
                else:
                    futuro.set_result(mensaje.get('result'))
            return
        if 'id' in mensaje:
            # Una petición del servidor: se contesta lo mínimo para que siga.
            params = mensaje.get('params') or {}
            resultado = None
            if metodo == 'workspace/configuration':
                resultado = [None for _ in (params.get('items') or [])]
            elif metodo == 'client/registerCapability':
                # sourcekit-lsp ofrece los diagnósticos «pull» así, no en initialize.
                for registro in params.get('registrations') or []:
                    if isinstance(registro, dict) and registro.get('method') == 'textDocument/diagnostic':
                        self.capacidades['diagnosticProvider'] = registro.get('registerOptions') or True
            elif metodo == 'workspace/diagnostic/refresh':
                # Terminó de cargar (sourcekit-lsp tras leer Package.swift): lo de antes estaba incompleto.
                self._refrescos += 1
                self._refresco.set()
            elif metodo == 'workspace/workspaceFolders':
                resultado = [{'uri': uri_de(self.raiz), 'name': Path(self.raiz).name or 'raiz'}]
            with contextlib.suppress(ServidorCaido):
                await self._enviar({'id': mensaje['id'], 'result': resultado})
            return
        if metodo == 'textDocument/publishDiagnostics':
            params = mensaje.get('params') or {}
            uri = params.get('uri')
            if isinstance(uri, str):
                self._publicados[uri] = (params.get('version'), params.get('diagnostics') or [])
                self._avisos.setdefault(uri, asyncio.Event()).set()

    # --- documentos ----------------------------------------------------

    async def diagnosticos(self, ruta, texto, hasta=None):
        """Abre o actualiza `ruta` con `texto` y devuelve sus diagnósticos (lista LSP).

        Sin plazo propio: quien llama lo envuelve en `asyncio.wait_for`. `hasta` (reloj
        del bucle) es ese plazo: con él, una lista vacía de un servidor que aún no avisó
        de que terminó de cargar se vuelve a pedir si avisa antes.
        """
        uri = uri_de(ruta)
        evento = self._avisos.setdefault(uri, asyncio.Event())
        evento.clear()
        self._publicados.pop(uri, None)
        version = self._versiones.get(uri)
        if version is None:
            version = 1
            await self.avisar('textDocument/didOpen', {'textDocument': {
                'uri': uri, 'languageId': self.idioma_de(ruta), 'version': version, 'text': texto}})
        else:
            version += 1
            await self.avisar('textDocument/didChange', {
                'textDocument': {'uri': uri, 'version': version}, 'contentChanges': [{'text': texto}]})
        self._versiones[uri] = version
        if self._pull is not False:
            try:
                resultado = await self.pedir('textDocument/diagnostic', {'textDocument': {'uri': uri}})
            except ServidorCaido as exc:
                if exc.codigo != METODO_DESCONOCIDO or self.capacidades.get('diagnosticProvider'):
                    raise
                self._pull = False  # no sabe: se esperan los que publica él
            else:
                self._pull = True
                items = resultado.get('items') if isinstance(resultado, dict) else None
                items = items if isinstance(items, list) else []
                ahora = asyncio.get_running_loop().time()
                if (items or self._refrescos or hasta is None or not self.carga
                        or self._calentando_hasta is None or ahora > self._calentando_hasta):
                    return items
                # Vacía y sin aviso de carga terminada: quizá aún no tenía la configuración de
                # compilación. Se espera ese aviso lo que deja el plazo y se pide otra vez.
                margen = min(ESPERA_CARGA, hasta - ahora - 0.4)
                if margen <= 0:
                    return items
                try:
                    await asyncio.wait_for(self._refresco.wait(), margen)
                except asyncio.TimeoutError:
                    return items
                resultado = await self.pedir('textDocument/diagnostic', {'textDocument': {'uri': uri}})
                items = resultado.get('items') if isinstance(resultado, dict) else None
                return items if isinstance(items, list) else []
        while True:
            await evento.wait()
            if self.caido:
                raise ServidorCaido(self.nombre + ' ' + self.caido)
            evento.clear()
            publicado = self._publicados.get(uri)
            if publicado is None:
                continue
            numero, lista = publicado
            if numero is not None and numero != version:
                continue
            # Algunos servidores publican primero una lista vacía y la buena justo después.
            try:
                await asyncio.wait_for(evento.wait(), 0.15)
            except asyncio.TimeoutError:
                return lista
            evento.clear()
            numero, lista = self._publicados.get(uri, publicado)
            return lista

    async def olvidar(self, ruta):
        """El archivo se borró: se cierra el documento."""
        uri = uri_de(ruta)
        if self._versiones.pop(uri, None) is not None:
            with contextlib.suppress(ServidorCaido):
                await self.avisar('textDocument/didClose', {'textDocument': {'uri': uri}})
