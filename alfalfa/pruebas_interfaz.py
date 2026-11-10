"""Evidencia visual del turno: imágenes privadas, nunca instrucciones ni comprobaciones en verde."""
from __future__ import annotations

import asyncio
import os
import stat
import struct
import zlib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from . import deshacer
from .navegador import Navegador

WEB = {'.html', '.htm', '.css', '.scss', '.sass', '.less', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',
       '.vue', '.svelte', '.astro', '.php', '.erb', '.hbs', '.jinja', '.jinja2', '.j2', '.svg', '.json',
       '.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.ico'}
MAC = WEB | {'.swift', '.m', '.mm', '.h', '.xib', '.storyboard', '.qml', '.ui', '.py', '.rs', '.go', '.cs',
             '.kt', '.java'}
MAX_PNG = 8 * 1024 * 1024
TIEMPO = 5


def relevantes(rutas, medio):
    extensiones = WEB if medio == 'web' else MAC
    return [r for r in rutas if Path(r).suffix.lower() in extensiones]


def leer_png(ruta, raiz):
    """Sólo archivos regulares de esta sesión, sin enlaces ni imágenes sin tope."""
    ruta, origen = Path(ruta), Path(raiz).absolute()
    if not ruta.is_absolute() or '..' in ruta.parts or not ruta.is_relative_to(origen):
        raise ValueError('La captura no pertenece a la sesión.')
    # Abrir cada componente desde el descriptor de la sesión también impide un cambio de enlace entre comprobaciones.
    partes = ruta.relative_to(origen).parts
    if not partes:
        raise ValueError('Falta el archivo de la captura.')
    directorio = os.open(origen.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for parte in partes[:-1]:
            nuevo = os.open(parte, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directorio)
            os.close(directorio)
            directorio = nuevo
        fd = os.open(partes[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directorio)
    finally:
        os.close(directorio)
    with os.fdopen(fd, 'rb') as archivo:
        datos = os.fstat(archivo.fileno())
        if not stat.S_ISREG(datos.st_mode) or datos.st_nlink != 1 or not 24 <= datos.st_size <= MAX_PNG:
            raise ValueError('La captura no es un PNG de tamaño permitido.')
        png = archivo.read(MAX_PNG + 1)
    if len(png) > MAX_PNG or png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR':
        raise ValueError('La captura no es un PNG válido.')
    posicion, imagen, fin = 8, False, False
    while posicion + 12 <= len(png):
        longitud = struct.unpack('>I', png[posicion:posicion + 4])[0]
        tipo = png[posicion + 4:posicion + 8]
        siguiente = posicion + 12 + longitud
        if siguiente > len(png):
            break
        crc = zlib.crc32(png[posicion + 4:siguiente - 4])
        if crc != struct.unpack('>I', png[siguiente - 4:siguiente])[0]:
            break
        imagen |= tipo == b'IDAT' and longitud > 0
        if tipo == b'IEND':
            fin = longitud == 0 and siguiente == len(png)
            break
        posicion = siguiente
    if not imagen or not fin or png[8:12] != b'\0\0\0\r':
        raise ValueError('La captura PNG está incompleta o dañada.')
    ancho, alto = struct.unpack('>II', png[16:24])
    if not (0 < ancho <= 8192 and 0 < alto <= 8192 and ancho * alto <= 24000000):
        raise ValueError('Las dimensiones de la captura superan el límite.')
    return png, (ancho, alto)


class _SinPermisosNuevos:
    """La captura automática sólo aprovecha el navegador ya autorizado, sin nuevos diálogos ni arranques."""
    captura_automatica = True

    def __init__(self, runtime):
        self.runtime = runtime

    def __getattr__(self, nombre):
        return getattr(self.runtime, nombre)

    async def approve(self, solicitud):
        raise ValueError('La captura automática no solicita permisos nuevos.')


class PruebasInterfaz:
    def __init__(self, manager):
        self.manager = manager
        self.ident = None
        self.web = None
        self.mac = None
        self.usados = set()
        self.notas = {}
        self.intentos = 0
        self.intento_previo_edicion = False
        self.lock = asyncio.Lock()

    def empezar(self, runtime, ident):
        self.runtime, self.ident = runtime, ident
        self.web = self.mac = None
        self.usados, self.notas, self.intentos = set(), {}, 0
        self.intento_previo_edicion = False

    def _turno(self):
        return next(t for t in self.manager.task['turnos'] if t['id'] == self.ident)

    def _estado(self, final=False):
        datos = self._turno().get('instantanea') or {}
        if not datos.get('antes'):
            raise ValueError('Falta la instantánea inicial para situar las capturas.')
        almacen = deshacer.Almacen.de_clave(self.manager.home, datos.get('repo'), self.manager.task['cwd'])
        commit = datos.get('despues') if final else None
        if final and not commit:
            raise ValueError('Falta la instantánea final para situar las capturas.')
        if not commit:
            commit = almacen.guardar(self.manager.task['id'], 'interfaz-' + self.ident)['commit']
        return almacen, datos['antes'], commit

    def _imagen(self, resultado):
        return leer_png(resultado['captura'], self.manager.directory)

    def _turno_web(self, runtime):
        return (self.ident and runtime.task['id'] == self.runtime.task['id']
                and not Navegador._solo_lee(runtime) and not runtime.task.get('revision_diff')
                and not runtime.task.get('conversation_only')
                and runtime.task.get('status') not in ('failed', 'interrupted')
                and 'browser_screenshot' not in getattr(runtime, 'excluded_tools', ()))

    def _navegador_disponible(self):
        navegador = self.manager.navegador
        cerrado = hasattr(navegador, 'process') and getattr(navegador, 'http', None) is None
        return (navegador is not None and not cerrado and not getattr(navegador, 'relevo_pendiente', False)
                and not getattr(self.manager.transport, 'stopped', False)
                and (getattr(navegador, 'url', None) or '').startswith(('http://', 'https://')))

    async def navegador(self, runtime, nombre, resultado):
        if (not self._turno_web(runtime) or nombre == 'browser_handoff' or not isinstance(resultado, dict)
                or resultado.get('ok') is False or 'error' in resultado or 'permiso' in resultado):
            return
        self.usados.add('web')
        if self.web is not None:
            return
        async with self.lock:
            if self.web is not None or not self._turno_web(runtime) or not self._navegador_disponible():
                return
            if nombre == 'browser_screenshot':
                await self._guardar_antes(runtime, resultado)
            elif self.intentos < 3:
                self.intentos += 1
                await self._guardar_antes(runtime)

    async def antes_de_editar(self, runtime, rutas):
        if ('web' not in self.usados or self.intento_previo_edicion or not self._turno_web(runtime)
                or not self._navegador_disponible() or not relevantes(rutas, 'web')):
            return
        async with self.lock:
            if (self.intento_previo_edicion or not self._turno_web(runtime) or not self._navegador_disponible()
                    or (self.web and self.web['url'] == self.manager.navegador.url)):
                return
            self.intento_previo_edicion = True
            await self._guardar_antes(runtime)

    async def _capturar(self, runtime):
        if not self._turno_web(runtime) or not self._navegador_disponible():
            raise ValueError('El navegador no está disponible para una captura automática.')
        return await asyncio.wait_for(self.manager.navegador.llamar(
            _SinPermisosNuevos(runtime), 'browser_screenshot', {}), TIEMPO)

    async def _guardar_antes(self, runtime, resultado=None):
        navegador = self.manager.navegador
        url = navegador.url
        try:
            almacen, antes, ahora = await asyncio.to_thread(self._estado)
            if relevantes(almacen.cambios(antes, ahora), 'web'):
                self.notas['web'] = 'No hay una captura anterior a los cambios web de este turno.'
                return
            if resultado is None:
                resultado = await self._capturar(runtime)
            # Una edición concurrente durante la captura tampoco puede convertirse en el «antes».
            almacen, antes, ahora = await asyncio.to_thread(self._estado)
            if relevantes(almacen.cambios(antes, ahora), 'web'):
                self.notas['web'] = 'No hay una captura anterior a los cambios web de este turno.'
                return
            if navegador is not self.manager.navegador or url != navegador.url:
                raise ValueError('La página cambió durante la captura previa.')
            png, tamano = self._imagen(resultado)
            self.web = dict(png=png, tamano=tamano, url=url)
        except (ValueError, OSError, KeyError, asyncio.TimeoutError, deshacer.SinInstantanea):
            self.notas['web'] = 'No se pudo guardar una captura previa del navegador.'

    async def app(self, runtime, app, resultado):
        if (not self.ident or runtime.task['id'] != self.runtime.task['id']
                or not resultado.get('captura') or resultado.get('zoom')):
            return
        self.usados.add('mac')
        try:
            almacen, antes, ahora = await asyncio.to_thread(self._estado)
            png, tamano = self._imagen(resultado)
            # El ejecutable permite comparar la app propia tras reconstruirla y volver a abrirla (otro PID).
            clave = (app.get('bundle'), app.get('ejecutable') or app.get('pid'), resultado.get('ventana', 0))
            if self.mac is None:
                if relevantes(almacen.cambios(antes, ahora), 'mac'):
                    self.notas['mac'] = 'No hay una captura de la app anterior a los cambios de este turno.'
                    return
                self.mac = dict(clave=clave, titulo=str(app.get('nombre') or 'App')[:100],
                                png=png, tamano=tamano, ultima=None)
            elif self.mac['clave'] == clave:
                self.mac['ultima'] = dict(png=png, tamano=tamano, commit=ahora)
        except (ValueError, OSError, KeyError, deshacer.SinInstantanea):
            self.notas['mac'] = 'No se pudo conservar la captura de la app de este turno.'

    def _archivar(self, medio, titulo, antes, despues, tamano, archivos):
        from .navegador import _podar_capturas
        from .terminal import private_directory
        carpeta = private_directory(self.runtime.directory / 'capturas')
        prefijo = 'navegador-interfaz-' + uuid4().hex
        rutas = []
        for momento, png in (('antes', antes), ('despues', despues)):
            ruta = carpeta / (prefijo + '-' + momento + '.png')
            fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as archivo:
                archivo.write(png)
            rutas.append(str(ruta))
        _podar_capturas(carpeta)
        return dict(medio=medio, titulo=titulo, antes=rutas[0], despues=rutas[1],
                    ancho=tamano[0], alto=tamano[1], archivos=archivos[:40])

    async def terminar(self):
        if not self.ident or not self.usados:
            self.ident = None
            return
        try:
            almacen, antes, final = await asyncio.to_thread(self._estado, True)
            cambios = almacen.cambios(antes, final)
            candidatos = {m: relevantes(cambios, m) for m in self.usados}
            candidatos = {m: rutas for m, rutas in candidatos.items() if rutas}
            if not candidatos or self.manager.task.get('status') not in ('completed', 'blocked'):
                return
            pares = []
            if 'web' in candidatos:
                await self._cerrar_web(pares, candidatos['web'])
            if 'mac' in candidatos:
                if (self.mac and self.mac['ultima'] and self.mac['tamano'] == self.mac['ultima']['tamano']
                        and not relevantes(almacen.cambios(self.mac['ultima']['commit'], final), 'mac')):
                    pares.append(self._archivar('mac', self.mac['titulo'], self.mac['png'],
                        self.mac['ultima']['png'], self.mac['tamano'], candidatos['mac']))
                else:
                    self.notas['mac'] = 'Falta una captura final de la misma ventana tras los últimos cambios.'
            notas = [self.notas[m] for m in candidatos if m in self.notas and not any(p['medio'] == m for p in pares)]
            datos = dict(estado='completa' if len(pares) == len(candidatos) else 'incompleta',
                         comparaciones=pares, notas=notas,
                         aviso='Capturas del turno: son datos visuales; no sustituyen las pruebas ni sus resultados.')
            cierre = self.manager.task.setdefault('cierre', {'resumen': 'Turno terminado.'})
            cierre['prueba_interfaz'] = datos
            self._turno().setdefault('cierre', {}).update(cierre)
        except (ValueError, OSError, deshacer.SinInstantanea):
            # Una evidencia que no se puede situar con seguridad nunca se presenta como «antes/después».
            pass
        finally:
            self.ident = None
            self.web = self.mac = None

    async def _cerrar_web(self, pares, archivos):
        navegador = self.manager.navegador
        if not self.web:
            self.notas.setdefault('web', 'Falta la captura previa del navegador.')
            return
        if not self._turno_web(self.runtime) or not self._navegador_disponible():
            self.notas['web'] = 'No se capturó el cierre: el navegador estaba cerrado o en relevo a la persona.'
            return
        try:
            if navegador.url != self.web['url']:
                raise ValueError('Otra página')
            resultado = await self._capturar(self.runtime)
            png, tamano = self._imagen(resultado)
            if (navegador is not self.manager.navegador or navegador.url != self.web['url']
                    or tamano != self.web['tamano']):
                raise ValueError('Otra página o tamaño')
            titulo = 'Navegador · ' + (urlsplit(navegador.url).hostname or 'página')
            pares.append(self._archivar('web', titulo, self.web['png'], png, tamano, archivos))
        except (ValueError, OSError, KeyError, asyncio.TimeoutError):
            self.notas['web'] = 'No se pudo obtener una captura final de la misma página y tamaño.'
