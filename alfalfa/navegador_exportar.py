"""Registra sólo pasos comprobables de un turno y genera un spec sin valores de la página."""
import asyncio
import os
import re
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from .navegador_sitios import origen
from .permisos import modos

MAX_PASOS = 60
ROLES = frozenset({'button', 'link', 'textbox', 'searchbox', 'spinbutton', 'checkbox', 'radio', 'tab', 'switch'})
ESCRIBIBLES = frozenset({'textbox', 'searchbox', 'spinbutton'})
SIN_PASO = frozenset({'browser_console', 'browser_network', 'browser_screenshot'})
OBSERVACIONES = SIN_PASO | {'browser_read', 'browser_wait'}
LINEA_REF = re.compile(r'^\s*- ([a-z]+) «([^«»]{1,120})» \[(e\d{1,6})\](.*)$')
LINEA_TEXTO = re.compile(r'^\s*- text «([^«»]{1,400})»$')
RUTA = re.compile(r'tests/e2e/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.spec\.ts')
NO_REPRODUCIBLES = {
    'browser_handoff': 'El relevo requiere un paso manual; no se genera una prueba parcial.',
    'browser_eval': 'El flujo usó JavaScript de página; no se copia ni se simula ese paso.',
    'browser_upload': 'El flujo entregó archivos; la subida debe prepararse de forma manual.',
    'browser_download': 'El flujo descargó archivos; la descarga debe comprobarse de forma manual.',
    'browser_compare': 'La comparación navega y cambia el tamaño en ambos motores; se prepara por separado.',
}


@dataclass(frozen=True)
class Paso:
    accion: str
    rol: str | None = None


@dataclass(frozen=True)
class Elemento:
    rol: str
    nombre: str
    contrasena: bool
    formulario: bool

    @property
    def descripcion(self):
        return f'{self.rol} «{self.nombre}»'


def _clave(tipo, numero):
    return f'ALFALFA_E2E_{tipo}_{numero}'


def _variables(pasos):
    for numero, paso in enumerate(pasos, 1):
        if paso.accion == 'abrir':
            yield {'clave': 'ALFALFA_E2E_BASE_URL', 'paso': numero, 'uso': 'URL inicial del entorno'}
        elif paso.accion == 'ruta':
            yield {'clave': _clave('RUTA', numero), 'paso': numero, 'uso': 'Ruta del mismo origen'}
        elif paso.accion in ('pulsar', 'escribir'):
            yield {'clave': _clave('NOMBRE', numero), 'paso': numero, 'uso': 'Nombre accesible exacto', 'rol': paso.rol}
            if paso.accion == 'escribir':
                yield {'clave': _clave('VALOR', numero), 'paso': numero, 'uso': 'Valor para escribir'}
        elif paso.accion == 'comprobar':
            yield {'clave': _clave('TEXTO', numero), 'paso': numero, 'uso': 'Texto exacto observado'}


@dataclass(frozen=True)
class Propuesta:
    id: str
    pasos: tuple[Paso, ...]

    def cierre(self):
        return {'id': self.id, 'ruta': f'tests/e2e/navegador-{self.id[:8]}.spec.ts',
                'pasos': len(self.pasos), 'variables': list(_variables(self.pasos))}


def _entero(args, resultado):
    if args.get('mode', 'tree') != 'tree' or resultado.get('recortado', False) is not False:
        return False
    if 'lectura' in resultado:
        lectura = resultado['lectura']
        if (not isinstance(lectura, dict) or lectura.get('modo') != 'tree' or lectura.get('solicitado') != 'tree'
                or lectura.get('completa') is not True):
            return False
    return isinstance(resultado.get('arbol'), str) and len(resultado['arbol']) <= 15000


def _etiqueta(texto):
    return texto.isprintable() and not texto.endswith('…')


class RegistroExportacion:
    """Sólo en memoria del turno; los nombres observados no pasan a la propuesta ni al spec."""

    def __init__(self):
        self.pasos = []
        self.motivo = None
        self.usado = False
        self._cerrado = False
        self._origen = None
        self._url = None
        self._elementos = {}
        self._textos = Counter()

    def _limpiar(self):
        self._elementos.clear()
        self._textos.clear()

    def _bloquear(self, motivo):
        self.motivo = self.motivo or motivo
        self._limpiar()

    def fallo(self, nombre):
        if self._cerrado:
            return
        self.usado = True
        if nombre not in OBSERVACIONES:
            self._bloquear('No se confirmó el efecto de una acción del navegador.')
        elif nombre == 'browser_read':
            self._limpiar()

    def _agregar(self, paso):
        if len(self.pasos) >= MAX_PASOS:
            self._bloquear('El flujo supera el límite de 60 pasos.')
        else:
            self.pasos.append(paso)

    def _navegar(self, args, url, destino):
        if args.get('action', 'open') != 'open':
            self._bloquear('Atrás, adelante y recarga no se exportan como una navegación nueva.')
            return
        try:
            solicitado = origen(args.get('url'))
        except ValueError:
            self._bloquear('La navegación necesita una URL HTTP(S) explícita y sin credenciales.')
            return
        if solicitado != destino or (self._origen is not None and self._origen != destino):
            self._bloquear('El turno recorrió más de un origen; no se genera una prueba parcial.')
            return
        self._agregar(Paso('abrir' if self._origen is None else 'ruta'))
        self._origen, self._url = destino, url
        self._limpiar()

    def _leer(self, args, resultado):
        self._limpiar()
        if not _entero(args, resultado):
            return
        for linea in resultado['arbol'].splitlines():
            texto = LINEA_TEXTO.fullmatch(linea)
            if texto and _etiqueta(texto[1]):
                self._textos[texto[1]] += 1
            campo = LINEA_REF.fullmatch(linea)
            if not campo or campo[1] not in ROLES or not _etiqueta(campo[2]):
                continue
            rol, nombre, ref, extras = campo.groups()
            if ref in self._elementos:
                self._limpiar()
                return
            self._elementos[ref] = Elemento(rol, nombre, 'contraseña' in extras, 'envía el formulario' in extras)

    def _accion(self, nombre, args, resultado, url):
        descripcion = resultado.get('elemento')
        candidatos = [e for e in self._elementos.values() if e.descripcion == descripcion]
        ref = args.get('ref')
        if isinstance(ref, str):
            ref = ref.strip()
        elif ref is not None:
            self._bloquear('La acción no confirmó una referencia válida.')
            return
        if len(candidatos) != 1 or (ref and self._elementos.get(ref) != candidatos[0]):
            self._bloquear('Falta un elemento efectivo y único en un árbol completo reciente.')
            return
        elemento = candidatos[0]
        if nombre == 'browser_type':
            if (elemento.rol not in ESCRIBIBLES or elemento.contrasena or resultado.get('valor') == '(oculto)'
                    or args.get('clear') is False or args.get('submit') is True):
                self._bloquear('La escritura no es un relleno simple reproducible; el paso debe ser manual.')
                return
            self._agregar(Paso('escribir', elemento.rol))
        else:
            if elemento.formulario or resultado.get('envio_formulario') is True:
                self._bloquear('El flujo envió un formulario; ese paso se debe preparar de forma manual.')
                return
            self._agregar(Paso('pulsar', elemento.rol))
        self._url = url
        self._limpiar()

    def registrar(self, nombre, args, resultado):
        if self._cerrado:
            return
        self.usado = True
        if self.motivo:
            return
        if not isinstance(resultado, dict) or 'error' in resultado or resultado.get('ok') is False:
            self.fallo(nombre)
            return
        if nombre in SIN_PASO:
            return
        if nombre not in OBSERVACIONES and nombre not in ('browser_navigate', 'browser_click', 'browser_type'):
            self._bloquear(NO_REPRODUCIBLES.get(nombre, 'Hay una acción fuera de los pasos reproducibles.'))
            return
        url = resultado.get('url')
        try:
            destino = origen(url)
        except ValueError:
            if nombre not in OBSERVACIONES:
                self._bloquear('El navegador no confirmó el origen de una acción.')
            else:
                self._limpiar()
            return
        if nombre == 'browser_navigate':
            self._navegar(args, url, destino)
            return
        if self._origen is None:
            if nombre not in OBSERVACIONES:
                self._bloquear('Falta una navegación inicial ejecutada en este turno.')
            return
        if destino != self._origen:
            self._bloquear('El turno recorrió más de un origen; no se genera una prueba parcial.')
        elif nombre in ('browser_click', 'browser_type'):
            self._accion(nombre, args, resultado, url)
        elif url != self._url:
            self._bloquear('La URL cambió sin una navegación observada en el turno.')
        elif nombre == 'browser_read':
            self._leer(args, resultado)
        elif nombre == 'browser_wait' and resultado.get('cumplida') is True:
            texto = args.get('text')
            if isinstance(texto, str) and self._textos[texto] == 1:
                self._agregar(Paso('comprobar'))

    def cerrar(self, completado):
        if self._cerrado:
            return None, None
        self._cerrado = True
        self._limpiar()
        self._origen = self._url = None
        if not completado or not self.usado:
            return None, None
        if self.motivo:
            return None, {'motivo': self.motivo}
        if (not self.pasos or self.pasos[-1].accion != 'comprobar'
                or not any(p.accion in ('pulsar', 'escribir') for p in self.pasos)):
            return None, {'motivo': 'Falta una acción comprobable y una espera final de texto exacto único.'}
        propuesta = Propuesta(uuid4().hex, tuple(self.pasos))
        return propuesta, propuesta.cierre()


class ExportacionNavegador:
    """Conserva la propuesta del último turno sólo en el proceso que lo ejecutó."""

    def __init__(self, manager):
        self.manager = manager
        self.registro = self.propuesta = None

    def empezar(self):
        self.propuesta = None
        self.registro = RegistroExportacion()

    def terminar(self):
        registro, self.registro = self.registro, None
        if registro is not None:
            tarea = self.manager.task
            self.propuesta, datos = registro.cerrar(tarea.get('status') == 'completed')
            if datos is not None:
                tarea.setdefault('cierre', {})['prueba_navegador'] = datos

    async def llamar(self, navegador, runtime, nombre, args):
        registro = self.registro
        try:
            resultado = await navegador.llamar(runtime, nombre, args)
        except (Exception, asyncio.CancelledError):
            if registro is not None:
                registro.fallo(nombre)
            raise
        if registro is not None:
            registro.registrar(nombre, args, resultado)
        return resultado

    def _comprobar(self, ident):
        manager = self.manager
        transporte = manager.transport
        if (transporte is None or transporte.stopped or self.propuesta is None or self.propuesta.id != ident
                or manager.task.get('status') != 'completed'):
            raise ValueError('La propuesta ya no está disponible en el motor de esta sesión.')
        modo = modos.modo_de(manager.task)
        pendiente = transporte.pending_settings.get('modo', modo)
        if manager.read_only or modos.solo_lee(modo) or modos.solo_lee(pendiente):
            raise ValueError('Plan y solo lectura no permiten guardar una prueba.')
        if pendiente != modo:
            raise ValueError('El cambio de modo se aplica al siguiente turno. Guarda la prueba desde ese cierre.')
        revision = transporte.revision_codigo
        if (transporte.in_turn or transporte.compacting or transporte.inbox or transporte.continue_new
                or transporte.compact_now or manager.cola_encargos.activa
                or (revision is not None and not revision.done())):
            raise ValueError('Espera a que termine el trabajo en marcha antes de guardar la prueba.')
        return modos.pide_ediciones(modo)

    async def guardar(self, ident, ruta):
        """Sólo el comando de la tarjeta puede invocar este guardado."""
        from .proveedores.nativo.herramientas import WorkspaceTools

        ruta = validar_ruta(ruta)
        manual = self._comprobar(ident)
        herramientas = WorkspaceTools(SimpleNamespace(task=self.manager.task, manager=self.manager))
        herramientas.path(ruta, escritura=True)
        if manual:
            respuesta = await self.manager.approval({'argv': ['write_file', ruta], 'purpose': 'Guardar la prueba',
                                                     'motivo': modos.MOTIVO_EDICION, 'motivos': [modos.MOTIVO_EDICION]})
            if respuesta is False:
                raise ValueError('No se autorizó el guardado de la prueba.')
        # La escritura pequeña y excluyente no cede el bucle entre comprobar y crear.
        self._comprobar(ident)
        herramientas.path(ruta, escritura=True)
        guardar_spec(self.manager.task['cwd'], ruta, self.propuesta)
        self.propuesta = None
        return ruta


ENCABEZADO = '''import { expect, test } from '@playwright/test';

// Los nombres, rutas, textos y valores se suministran sólo desde el entorno.
function variable(clave: string): string {
  const valor = process.env[clave];
  if (valor === undefined) throw new Error(`Falta ${clave}.`);
  return valor;
}

function ruta(base: URL, valor: string): string {
  const destino = new URL(valor, base);
  if (!valor.startsWith('/') || valor.startsWith('//') || destino.origin !== base.origin) {
    throw new Error('Usa una ruta del mismo origen del entorno de prueba.');
  }
  return destino.href;
}

test.use({ serviceWorkers: 'block' });
test('Flujo de navegador exportado', async ({ page }) => {
  const base = new URL(variable('ALFALFA_E2E_BASE_URL'));
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) {
    throw new Error('ALFALFA_E2E_BASE_URL debe ser HTTP(S) y no contener credenciales.');
  }
  await page.context().route('**/*', route => {
    const destino = new URL(route.request().url());
    return destino.origin === base.origin ? route.continue() : route.abort();
  });
'''


def renderizar(propuesta):
    """Nunca interpola un nombre, texto, ruta, host, ref ni valor observado."""
    if (not propuesta.pasos or propuesta.pasos[0].accion != 'abrir'
            or propuesta.pasos[-1].accion != 'comprobar'):
        raise ValueError('La propuesta no es un flujo completo.')
    lineas = [ENCABEZADO.rstrip()]
    for i, paso in enumerate(propuesta.pasos, 1):
        if paso.accion == 'abrir' and i == 1:
            lineas.append('  await page.goto(base.href);')
        elif paso.accion == 'ruta':
            lineas.append(f"  await page.goto(ruta(base, variable('{_clave('RUTA', i)}')));")
        elif paso.accion in ('pulsar', 'escribir') and paso.rol in ROLES:
            lineas.append(f"  const paso{i} = page.getByRole('{paso.rol}', "
                          f"{{ name: variable('{_clave('NOMBRE', i)}'), exact: true }});")
            lineas.append(f'  await expect(paso{i}).toHaveCount(1);')
            operacion = 'click()' if paso.accion == 'pulsar' else f"fill(variable('{_clave('VALOR', i)}'))"
            lineas.append(f'  await paso{i}.{operacion};')
        elif paso.accion == 'comprobar':
            lineas.append(f"  const texto{i} = page.getByText(variable('{_clave('TEXTO', i)}'), {{ exact: true }});")
            lineas.append(f'  await expect(texto{i}).toHaveCount(1);')
            lineas.append(f'  await expect(texto{i}).toBeVisible();')
        else:
            raise ValueError('La propuesta contiene un paso no reproducible.')
        if paso.accion in ('abrir', 'ruta', 'pulsar', 'comprobar'):
            lineas.append('  await expect(page).toHaveURL(url => url.origin === base.origin);')
    return '\n'.join([*lineas, '});', ''])


def validar_ruta(ruta):
    if not isinstance(ruta, str) or not RUTA.fullmatch(ruta):
        raise ValueError('Guarda la prueba en tests/e2e con un nombre simple terminado en .spec.ts.')
    return ruta


def guardar_spec(proyecto, ruta, propuesta):
    """Sólo tras el permiso del motor. Crea un archivo nuevo y no sigue enlaces de directorio."""
    ruta = validar_ruta(ruta)
    raiz = Path(proyecto)
    if not raiz.is_absolute() or '..' in raiz.parts:
        raise ValueError('El motor necesita la raíz canónica del proyecto.')
    contenido = renderizar(propuesta).encode('utf-8')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directorio = os.open(raiz.anchor, flags)
        stack.callback(os.close, directorio)
        for parte in raiz.parts[1:]:
            directorio = os.open(parte, flags, dir_fd=directorio)
            stack.callback(os.close, directorio)
        for parte in ('tests', 'e2e'):
            try:
                os.mkdir(parte, mode=0o755, dir_fd=directorio)
            except FileExistsError:
                pass
            directorio = os.open(parte, flags, dir_fd=directorio)
            stack.callback(os.close, directorio)
        nombre = ruta.rsplit('/', 1)[1]
        try:
            descriptor = os.open(nombre, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directorio)
        except FileExistsError:
            raise ValueError('Ya existe una prueba en esa ruta; no se sobrescribe.') from None
        identidad = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, 'wb') as archivo:
                archivo.write(contenido)
        except OSError:
            try:
                actual = os.stat(nombre, dir_fd=directorio, follow_symlinks=False)
                if (actual.st_dev, actual.st_ino) == (identidad.st_dev, identidad.st_ino):
                    os.unlink(nombre, dir_fd=directorio)
            except OSError:
                pass
            raise
    return ruta
