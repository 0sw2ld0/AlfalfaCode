"""Los errores de lo que el agente acaba de editar, en el resultado del mismo paso.

Como OpenCode con sus servidores de lenguaje: tras `write_file`, `edit_file` o
`apply_patch` se pregunta a un servidor de lenguaje por los archivos tocados y
al resultado de la herramienta se le añaden los diagnósticos nuevos (los que
no estaban antes de la edición), con su línea y recortados a `MAX_NUEVOS`.

- Swift: `sourcekit-lsp` (viene con Xcode).
- Python: `basedpyright-langserver` o `pyright-langserver`; sin ellos, `ruff check`
  y, sin ruff, al menos los errores de sintaxis del propio Python.
- TypeScript/JavaScript: `typescript-language-server`.
- El linter del proyecto, si tiene uno configurado (ruff, ESLint, SwiftLint),
  sólo sobre los archivos tocados.

Sólo se usa lo que ya está instalado; nada se instala. Los servidores arrancan
la primera vez que hacen falta, siguen vivos durante la sesión y se cierran con
ella. Nada de esto alarga el turno más de `espera()` segundos por edición: lo
que no contesta a tiempo se queda fuera y el resultado lo dice (un servidor que
arranca sigue arrancando y sirve en la siguiente edición).

Los servidores y linters cargan código del proyecto (la configuración de
ESLint, los plugins de tsserver), así que en macOS corren en el mismo sandbox
Seatbelt que las órdenes del agente, sin red y escribiendo sólo en el proyecto
y en las carpetas temporales. Sin Seatbelt sólo corren los que no ejecutan
código del proyecto. La excepción es `sourcekit-lsp`: su sourcekitd es un
servicio XPC de Xcode que Seatbelt bloquea, así que corre fuera y el
manifiesto `Package.swift` queda en el sandbox propio de SwiftPM, como cuando
Xcode abre un paquete.

Los diagnósticos orientan, pero no son una comprobación: la evidencia
(`alfalfa/evidencia`) sólo cuenta `run_checks`, y el resultado lo recuerda.

Se apaga con `ALFALFA_DIAGNOSTICOS=0`, `alfalfa --sin-diagnosticos`, en la app
(Ajustes ▸ General) o con `{"diagnosticos": false}` en `.alfalfa/ajustes.json`
del proyecto o en `$ALFALFA_HOME/ajustes.json` del usuario.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import linters
from .lsp import ClienteLSP, ServidorCaido

VARIABLE = 'ALFALFA_DIAGNOSTICOS'
VARIABLE_ESPERA = 'ALFALFA_DIAGNOSTICOS_ESPERA'
ARCHIVO_AJUSTES = 'ajustes.json'
ESPERA = 3.0
MAX_ESPERA = 30.0
# Lo que puede tardar en arrancar un servidor antes de darlo por perdido en la sesión.
MAX_ARRANQUE = 90.0
MAX_NUEVOS = 20
MAX_MENSAJE = 300
MAX_ARCHIVOS = 10
MAX_CACHE = 400
NOTA = ('Diagnósticos automáticos tras editar: orientan, pero no son una comprobación ni cuentan como '
        'verificación. Verifica con run_checks.')
NOTA_LIMPIO = ('Sin diagnósticos nuevos. No es una comprobación ni cuenta como verificación: verifica con '
               'run_checks.')

SWIFT = ('.swift',)
PYTHON = ('.py', '.pyi')
JAVASCRIPT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
IDIOMAS = {'.swift': 'swift', '.py': 'python', '.pyi': 'python', '.ts': 'typescript', '.mts': 'typescript',
           '.cts': 'typescript', '.tsx': 'typescriptreact', '.js': 'javascript', '.mjs': 'javascript',
           '.cjs': 'javascript', '.jsx': 'javascriptreact'}
APAGADO = ('0', 'no', 'false', 'off', 'apagado')


def _leer_ajustes(ruta):
    try:
        valor = json.loads(Path(ruta).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def activos(raiz, hogar, forzado=None):
    """¿Se miran los diagnósticos? El primero que diga algo decide.

    `forzado` (--sin-diagnosticos o la app), `ALFALFA_DIAGNOSTICOS`, el
    `.alfalfa/ajustes.json` del proyecto y el `ajustes.json` del hogar del usuario.
    Por defecto, sí. Se lee en cada edición: cambiar el archivo vale ya.
    """
    if isinstance(forzado, bool):
        return forzado
    variable = os.environ.get(VARIABLE, '').strip().lower()
    if variable:
        return variable not in APAGADO
    for archivo in (Path(raiz) / '.alfalfa' / ARCHIVO_AJUSTES, Path(hogar) / ARCHIVO_AJUSTES):
        valor = _leer_ajustes(archivo).get('diagnosticos')
        if isinstance(valor, bool):
            return valor
    return True


def espera():
    """Segundos que una edición espera a los diagnósticos (ALFALFA_DIAGNOSTICOS_ESPERA)."""
    try:
        valor = float(os.environ.get(VARIABLE_ESPERA, ESPERA))
    except ValueError:
        return ESPERA
    return min(max(valor, 0.1), MAX_ESPERA)


def _ejecutable(nombre, env, *extra):
    """Ruta del programa en las carpetas `extra` (las del proyecto) o en el PATH."""
    for carpeta in extra:
        candidato = Path(carpeta) / nombre
        if candidato.is_file() and os.access(candidato, os.X_OK):
            return str(candidato)
    return shutil.which(nombre, path=(env or os.environ).get('PATH'))


def _sourcekit(env):
    ruta = _ejecutable('sourcekit-lsp', env)
    if ruta or sys.platform != 'darwin':
        return ruta
    with contextlib.suppress(OSError, ValueError):
        import subprocess
        salida = subprocess.run(['xcrun', '--find', 'sourcekit-lsp'], capture_output=True, text=True, timeout=5)
        ruta = salida.stdout.strip()
        if salida.returncode == 0 and ruta and os.access(ruta, os.X_OK):
            return ruta
    return None


def _subiendo(desde, raiz, nombres):
    """La carpeta más cercana a `desde`, sin salir de `raiz`, que tiene alguno de `nombres`."""
    raiz = Path(raiz)
    carpeta = Path(desde)
    while True:
        if any((carpeta / n).exists() for n in nombres):
            return carpeta
        if carpeta == raiz or raiz not in carpeta.parents:
            return None
        carpeta = carpeta.parent


def _bins_de_node(desde, raiz):
    """Las carpetas node_modules/.bin desde el archivo hasta la raíz del proyecto."""
    carpetas, carpeta, raiz = [], Path(desde), Path(raiz)
    while True:
        bins = carpeta / 'node_modules' / '.bin'
        if bins.is_dir():
            carpetas.append(bins)
        if carpeta == raiz or raiz not in carpeta.parents:
            return carpetas
        carpeta = carpeta.parent


def _clave(d):
    return (d['fuente'], d.get('codigo') or '', d['mensaje'], d['gravedad'])


def nuevos(antes, despues):
    """Los de `despues` que no estaban en `antes`: por fuente, código, texto y gravedad, sin mirar la línea
    (una línea añadida arriba desplaza los que ya había y no por eso son nuevos)."""
    quedan = {}
    for d in antes:
        quedan[_clave(d)] = quedan.get(_clave(d), 0) + 1
    resultado = []
    for d in despues:
        if quedan.get(_clave(d), 0) > 0:
            quedan[_clave(d)] -= 1
        else:
            resultado.append(d)
    return resultado


def desde_lsp(fuente, ruta, lista):
    """Diagnósticos LSP → los nuestros. Sólo errores (1) y avisos (2)."""
    filas = []
    for d in lista if isinstance(lista, list) else []:
        if not isinstance(d, dict):
            continue
        gravedad = d.get('severity', 1)
        if gravedad not in (1, 2):
            continue
        inicio = ((d.get('range') or {}).get('start') or {})
        codigo = d.get('code')
        filas.append({'path': ruta, 'linea': int(inicio.get('line', 0)) + 1,
                      'columna': int(inicio.get('character', 0)) + 1,
                      'gravedad': 'error' if gravedad == 1 else 'aviso',
                      'mensaje': ' '.join(str(d.get('message') or '').split())[:MAX_MENSAJE],
                      'fuente': str(d.get('source') or fuente)[:40],
                      'codigo': str(codigo)[:60] if codigo not in (None, '') else None})
    return filas


def sintaxis_python(ruta, texto):
    """Sin pyright ni ruff: al menos el error de sintaxis, con el compilador de este Python."""
    try:
        compile(texto, ruta, 'exec', dont_inherit=True)
    except SyntaxError as exc:
        return [{'path': ruta, 'linea': exc.lineno or 1, 'columna': exc.offset or 1, 'gravedad': 'error',
                 'mensaje': ' '.join(str(exc.msg).split())[:MAX_MENSAJE], 'fuente': 'python', 'codigo': None}]
    except (ValueError, TypeError):
        return []
    return []


def texto_de(d):
    codigo = ' ' + d['codigo'] if d.get('codigo') else ''
    return (d['path'] + ':' + str(d['linea']) + ':' + str(d['columna']) + ' ' + d['gravedad']
            + ' [' + d['fuente'] + codigo + '] ' + d['mensaje'])


class Sandbox:
    """El prefijo de sandbox-exec del proyecto, calculado una vez por sesión."""

    def __init__(self, raiz):
        self.raiz = str(raiz)
        self._prefijo = None
        self.disponible = sys.platform == 'darwin' and os.access('/usr/bin/sandbox-exec', os.X_OK)

    async def envolver(self, argv):
        if not self.disponible:
            return list(argv)
        if self._prefijo is None:
            from ..seatbelt import SandboxUnavailable, SeatbeltSandbox
            try:
                orden = await asyncio.to_thread(SeatbeltSandbox(self.raiz, network=False).command_args, ['true'])
            except SandboxUnavailable:
                self.disponible = False
                return list(argv)
            self._prefijo = orden[:-1]
        return self._prefijo + list(argv)


class Diagnosticos:
    """Los servidores de lenguaje y linters de una sesión."""

    def __init__(self, raiz, hogar, *, forzado=None, env=None, sandbox=None):
        self.raiz = Path(raiz).resolve()
        self.hogar = Path(hogar)
        self.forzado = forzado
        self._env = env
        self.sandbox = sandbox if sandbox is not None else Sandbox(self.raiz)
        self.servidores = {}       # (lenguaje, raíz) -> ClienteLSP
        self._cache = {}           # (fuente, ruta, sha) -> diagnósticos de ese contenido
        self._dichos = set()       # avisos de «se cerró» que ya se dieron
        self._cerrado = False

    @property
    def env(self):
        """El entorno de los servidores, leído al lanzarlos, con la caché de módulos de Clang/Swift
        donde el sandbox deja escribir."""
        from ..seatbelt import compiladores
        return dict(self._env if self._env is not None else os.environ, **compiladores.entorno())

    # --- ajustes ---------------------------------------------------------

    def cambiar(self, activo):
        """El interruptor de la app: apagado manda; encendido vuelve a lo que digan variable y archivos."""
        self.forzado = False if activo is False else None

    def activos(self):
        return not self._cerrado and activos(self.raiz, self.hogar, self.forzado)

    # --- qué hay para cada archivo ----------------------------------------

    def _servidor(self, ruta):
        """(clave, ClienteLSP sin arrancar) para el archivo, o None si no hay servidor instalado."""
        sufijo = ruta.suffix.lower()
        if sufijo in SWIFT:
            argv = _sourcekit(self.env)
            if not argv:
                return None
            paquete = _subiendo(ruta.parent, self.raiz, ('Package.swift',))
            if paquete is not None and sys.platform != 'darwin':
                return None  # fuera de macOS SwiftPM no aísla el manifiesto, que es código del proyecto
            raiz = paquete or self.raiz
            cache = Path(tempfile.gettempdir()) / 'alfalfa-sourcekit' / hashlib.sha256(
                str(raiz).encode()).hexdigest()[:16]
            # sourcekitd es un servicio XPC de Xcode y el sandbox de AlfalfaCode los bloquea: va
            # fuera de él, con el sandbox propio de SwiftPM para el manifiesto (como Xcode al abrir
            # un paquete), sin indexado en segundo plano y con su compilación fuera del proyecto.
            opciones = {'backgroundIndexing': False, 'swiftPM': {'scratchPath': str(cache)}}
            return ('swift', str(raiz)), ('sourcekit-lsp', [argv], raiz, opciones, False, paquete is not None)
        if sufijo in PYTHON:
            for nombre in ('basedpyright-langserver', 'pyright-langserver'):
                argv = _ejecutable(nombre, self.env)
                if argv:
                    return ('python', str(self.raiz)), (nombre.replace('-langserver', ''),
                                                        [argv, '--stdio'], self.raiz, None, True, False)
            return None
        if sufijo in JAVASCRIPT:
            if not self.sandbox.disponible:
                return None  # tsserver carga plugins de node_modules
            argv = _ejecutable('typescript-language-server', self.env, *_bins_de_node(ruta.parent, self.raiz))
            if not argv:
                return None
            raiz = _subiendo(ruta.parent, self.raiz, ('tsconfig.json', 'jsconfig.json', 'package.json')) \
                or self.raiz
            return ('typescript', str(raiz)), ('typescript-language-server', [argv, '--stdio'], raiz,
                                               {'preferences': {}}, True, False)
        return None

    def _cliente(self, ruta):
        hallado = self._servidor(ruta)
        if hallado is None:
            return None
        clave, (nombre, argv, raiz, opciones, aislado, carga) = hallado
        cliente = self.servidores.get(clave)
        if cliente is None:
            cliente = self.servidores[clave] = ClienteLSP(
                nombre, argv, raiz, env=self.env, opciones=opciones,
                idioma_de=lambda r: IDIOMAS.get(Path(r).suffix.lower(), 'plaintext'),
                envolver=self.sandbox.envolver if aislado else None, carga=carga)
        return cliente

    # --- la edición --------------------------------------------------------

    async def tras_editar(self, root, cambios, antes_de):
        """El informe para el resultado de la herramienta, o None si no hay nada que mirar.

        `cambios`: los `{'path', 'before', 'after'}` de la edición. `antes_de(sha)`:
        el contenido anterior (las copias `before-<sha>` del runtime) o None.
        """
        if not self.activos():
            return None
        root = Path(root).resolve()
        archivos = []
        for cambio in cambios[:MAX_ARCHIVOS]:
            if not isinstance(cambio, dict) or not isinstance(cambio.get('path'), str):
                continue
            ruta = root / cambio['path']
            if cambio.get('after') == 'DELETED':
                for cliente in self.servidores.values():
                    if cliente.listo:
                        await cliente.olvidar(ruta)
                continue
            if ruta.suffix.lower() not in IDIOMAS:
                continue
            try:
                texto = ruta.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                continue
            antes = None
            if cambio.get('before') not in (None, 'NEW'):
                datos = antes_de(cambio['before'])
                with contextlib.suppress(UnicodeError, AttributeError):
                    antes = datos.decode('utf-8') if datos is not None else None
            archivos.append({'ruta': ruta, 'rel': cambio['path'], 'texto': texto, 'antes': antes,
                             'nuevo': cambio.get('before') == 'NEW', 'before': cambio.get('before'),
                             'after': cambio.get('after')})
        if not archivos:
            return None
        plazo = espera()
        hasta = asyncio.get_running_loop().time() + plazo
        trabajos = {}   # nombre -> corrutina que devuelve [(antes, después)] o lanza
        por_cliente = {}
        for archivo in archivos:
            cliente = self._cliente(archivo['ruta'])
            if cliente is not None and not cliente.caido:
                por_cliente.setdefault(id(cliente), (cliente, []))[1].append(archivo)
        for cliente, lista in por_cliente.values():
            trabajos[cliente.nombre] = self._con_servidor(cliente, lista, hasta)
        python_sin_servidor = [a for a in archivos if a['ruta'].suffix.lower() in PYTHON
                               and not any(a in lista for _, lista in por_cliente.values())]
        for nombre, herramienta in linters.del_proyecto(archivos, self.raiz, self.env, self.sandbox.disponible,
                                                         python_sin_servidor=bool(python_sin_servidor)).items():
            trabajos[nombre] = self._con_linter(nombre, herramienta)
        locales = []
        if python_sin_servidor and 'ruff' not in trabajos:
            for archivo in python_sin_servidor:
                base = [] if archivo['nuevo'] or archivo['antes'] is None else sintaxis_python(archivo['rel'],
                                                                                                archivo['antes'])
                locales.append((base, sintaxis_python(archivo['rel'], archivo['texto'])))
        if not trabajos and not locales:
            return None
        return await self._informe(trabajos, locales, plazo)

    async def _informe(self, trabajos, locales, plazo):
        tareas = {nombre: asyncio.ensure_future(c) for nombre, c in trabajos.items()}
        if tareas:
            await asyncio.wait(tareas.values(), timeout=plazo)
        todos_nuevos, ya_habia, con, sin_respuesta = [], 0, [], []
        if locales:
            con.append('python (sólo sintaxis)')
        for base, despues in locales:
            todos_nuevos += nuevos(base, despues)
            ya_habia += len(despues) - len(nuevos(base, despues))
        for nombre, tarea in tareas.items():
            if not tarea.done():
                tarea.cancel()
                await asyncio.gather(tarea, return_exceptions=True)
                cliente = next((c for c in self.servidores.values() if c.nombre == nombre), None)
                arrancando = cliente is not None and not cliente.listo and cliente.caido is None
                sin_respuesta.append(nombre + ' no contestó en ' + _segundos(plazo)
                                     + (' (sigue arrancando; servirá en las próximas ediciones)' if arrancando else '')
                                     + '; esta vez sin sus diagnósticos.')
                continue
            exc = tarea.exception()
            if exc is not None:
                motivo = str(exc)[:200] or type(exc).__name__
                if nombre not in self._dichos:
                    self._dichos.add(nombre)
                    sin_respuesta.append(nombre + ' falló (' + motivo + '); sin sus diagnósticos.')
                continue
            con.append(nombre)
            for base, despues in tarea.result():
                lista = nuevos(base, despues)
                todos_nuevos += lista
                ya_habia += len(despues) - len(lista)
        if not con and not sin_respuesta:
            return None
        todos_nuevos.sort(key=lambda d: (d['gravedad'] != 'error', d['path'], d['linea'], d['columna']))
        informe = {'nuevos': [texto_de(d) for d in todos_nuevos[:MAX_NUEVOS]]}
        errores = sum(1 for d in todos_nuevos if d['gravedad'] == 'error')
        if errores:
            informe['errores'] = errores
        if len(todos_nuevos) - errores:
            informe['avisos'] = len(todos_nuevos) - errores
        if len(todos_nuevos) > MAX_NUEVOS:
            informe['omitidos'] = len(todos_nuevos) - MAX_NUEVOS
        if ya_habia:
            informe['ya_habia'] = ya_habia
        if con:
            informe['revisado_con'] = con
        if sin_respuesta:
            informe['sin_respuesta'] = sin_respuesta
        informe['nota'] = NOTA if todos_nuevos or not con else NOTA_LIMPIO
        return informe

    def _guardar(self, clave, valor):
        self._cache[clave] = valor
        while len(self._cache) > MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))

    async def _con_servidor(self, cliente, archivos, hasta):
        arranque = cliente.arrancar()
        try:
            await asyncio.wait_for(asyncio.shield(arranque), MAX_ARRANQUE)
        except asyncio.TimeoutError:
            cliente.caido = 'no terminó de arrancar en ' + _segundos(MAX_ARRANQUE)
            await cliente.cerrar()
            raise ServidorCaido(cliente.caido) from None
        resultado = []
        async with cliente.lock:
            for archivo in archivos:
                base = self._cache.get((cliente.nombre, archivo['rel'], archivo['before']))
                if base is None:
                    if archivo['nuevo'] or archivo['antes'] is None:
                        base = []
                    else:
                        base = desde_lsp(cliente.nombre, archivo['rel'],
                                         await cliente.diagnosticos(archivo['ruta'], archivo['antes'], hasta))
                despues = desde_lsp(cliente.nombre, archivo['rel'],
                                    await cliente.diagnosticos(archivo['ruta'], archivo['texto'], hasta))
                self._guardar((cliente.nombre, archivo['rel'], archivo['after']), despues)
                resultado.append((base, despues))
        return resultado

    async def _con_linter(self, nombre, herramienta):
        resultado = []

        async def uno(archivo):
            base = self._cache.get((nombre, archivo['rel'], archivo['before']))
            consultas = [herramienta.lint(archivo['rel'], archivo['texto'], self.sandbox.envolver)]
            if base is None and not archivo['nuevo'] and archivo['antes'] is not None:
                consultas.append(herramienta.lint(archivo['rel'], archivo['antes'], self.sandbox.envolver))
            hechas = await asyncio.gather(*consultas)
            despues = hechas[0]
            if base is None:
                base = hechas[1] if len(hechas) > 1 else []
            self._guardar((nombre, archivo['rel'], archivo['after']), despues)
            return base, despues
        for par in await asyncio.gather(*(uno(a) for a in herramienta.archivos)):
            resultado.append(par)
        return resultado

    # --- cierre ------------------------------------------------------------

    async def cerrar(self):
        self._cerrado = True
        clientes = list(self.servidores.values())
        self.servidores = {}
        await asyncio.gather(*(c.cerrar() for c in clientes), return_exceptions=True)

    def matar(self):
        self._cerrado = True
        for cliente in self.servidores.values():
            cliente.matar()


def _segundos(valor):
    return (str(int(valor)) if float(valor).is_integer() else str(round(valor, 1))) + ' s'


__all__ = ['Diagnosticos', 'activos', 'espera', 'nuevos']
