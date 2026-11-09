"""Los errores de lo que el agente acaba de editar, en el resultado del mismo paso.

Como OpenCode con sus servidores de lenguaje: tras `write_file`, `edit_file` o
`apply_patch` se pregunta a un servidor de lenguaje por los archivos tocados y
al resultado de la herramienta se le añaden los diagnósticos nuevos (los que
no estaban antes de la edición), con su línea y recortados a `MAX_NUEVOS`.

- Swift: `sourcekit-lsp` (viene con Xcode).
- Python: `basedpyright-langserver` o `pyright-langserver`; sin ellos, `ruff check`
  y, sin ruff (o si falla), al menos los errores de sintaxis del propio Python.
- TypeScript/JavaScript: `typescript-language-server`.
- El linter del proyecto, si tiene uno configurado (ruff, ESLint, SwiftLint),
  sólo sobre los archivos tocados.

Sólo se usa lo que ya está instalado; nada se instala. Los servidores arrancan
la primera vez que hacen falta, siguen vivos durante la sesión y se cierran con
ella. Nada de esto alarga el turno más de `espera()` segundos por edición (sin
contar el permiso que haya que pedir): lo que no contesta a tiempo se queda
fuera y el resultado lo dice; un servidor que arranca sigue arrancando y sirve
en la siguiente edición, salvo que pase de `lsp.MAX_ARRANQUE`.

Permisos y aislamiento, porque estos programas cargan código del proyecto:

- pyright, typescript-language-server, ESLint, ruff y SwiftLint corren en el
  sandbox Seatbelt de las órdenes (sin red, escribiendo sólo en el proyecto y
  en las carpetas temporales). Sin sandbox no se lanzan.
- En Manual y «Aceptar ediciones», donde el usuario aprueba cada orden, los que
  ejecutan código del proyecto (pyright con su intérprete, tsserver con sus
  plugins, ESLint con su configuración JS) piden permiso una vez por sesión.
  Un programa de `node_modules/.bin` lo pide en cualquier modo; se prefiere el
  del PATH. ruff y SwiftLint sólo leen y no lo piden.
- `sourcekit-lsp` no puede ir en Seatbelt (su sourcekitd es un servicio XPC que
  el sandbox bloquea), así que pide el permiso de «fuera del sandbox» una vez
  por raíz y sesión (Omitir permisos lo concede). No se lanza si el proyecto
  trae algo que le haga ejecutar órdenes (`buildServer.json`, `.bsp/`,
  `.sourcekit-lsp/`, `compile_commands.json`, `compile_flags.txt`) ni en un
  paquete con dependencias (tendría que resolverlas). `Package.swift` queda en
  el sandbox propio de SwiftPM y compila en `~/Library/Caches/alfalfa/sourcekit`,
  donde el sandbox de las órdenes no escribe.

Los diagnósticos orientan, pero no son una comprobación: la evidencia
(`alfalfa/evidencia`) sólo cuenta `run_checks`, y el resultado lo recuerda.

Se apaga con `ALFALFA_DIAGNOSTICOS=0`, `alfalfa --sin-diagnosticos`, en la app
(Ajustes ▸ General) o con `{"diagnosticos": false}` en el `ajustes.json` del
usuario o en `.alfalfa/ajustes.json` del proyecto. El proyecto sólo puede
apagarlo: nunca enciende lo que el usuario apagó.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import re
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
MAX_NUEVOS = 20
MAX_MENSAJE = 300
MAX_ARCHIVOS = 10
MAX_CACHE = 400
# Procesos de linter a la vez en una edición.
MAX_PROCESOS = 4
# Por encima, «nuevos» compara sin alinear las líneas (difflib es cuadrático en el peor caso).
MAX_LINEAS_ALINEADAS = 20000
# Lo que puede tardar `swift package dump-package` en leer el manifiesto (compilarlo la primera vez).
MAX_VOLCADO = 60.0
NOTA = ('Diagnósticos automáticos tras editar: orientan, pero no son una comprobación ni cuentan como '
        'verificación. Verifica con run_checks.')
NOTA_LIMPIO = ('Sin diagnósticos nuevos. No es una comprobación ni cuenta como verificación: verifica con '
               'run_checks.')
SOLO_SINTAXIS = 'python (sólo sintaxis)'

SWIFT = ('.swift',)
PYTHON = ('.py', '.pyi')
JAVASCRIPT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
IDIOMAS = {'.swift': 'swift', '.py': 'python', '.pyi': 'python', '.ts': 'typescript', '.mts': 'typescript',
           '.cts': 'typescript', '.tsx': 'typescriptreact', '.js': 'javascript', '.mjs': 'javascript',
           '.cjs': 'javascript', '.jsx': 'javascriptreact'}
APAGADO = ('0', 'no', 'false', 'off', 'apagado')
# Lo que haría que sourcekit-lsp ejecute órdenes del proyecto (un servidor de compilación, una base de
# datos de compilación con su compilador) o cambie su configuración (desactivar el sandbox de SwiftPM).
TRAMPAS_SOURCEKIT = ('buildServer.json', '.bsp', '.sourcekit-lsp', 'compile_commands.json', 'compile_flags.txt',
                     'build/compile_commands.json', 'build/compile_flags.txt')

# Permisos que puede necesitar un servidor o linter.
FUERA = 'fuera'   # «orden fuera del sandbox»
MODO = 'modo'     # el modo pide aprobar cada orden


def _leer_ajustes(ruta):
    try:
        valor = json.loads(Path(ruta).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def activos(raiz, hogar, forzado=None):
    """¿Se miran los diagnósticos? Se lee en cada edición: cambiar el archivo vale ya.

    Primero decide el usuario: `forzado` (--sin-diagnosticos o la app), luego
    `ALFALFA_DIAGNOSTICOS` y luego su `ajustes.json`; por defecto, sí. Después el
    proyecto (`.alfalfa/ajustes.json`) sólo puede apagarlo.
    """
    if forzado is False:
        return False
    variable = os.environ.get(VARIABLE, '').strip().lower()
    if variable:
        del_usuario = variable not in APAGADO
    else:
        valor = _leer_ajustes(Path(hogar) / ARCHIVO_AJUSTES).get('diagnosticos')
        del_usuario = valor if isinstance(valor, bool) else True
    if not del_usuario:
        return False
    return _leer_ajustes(Path(raiz) / '.alfalfa' / ARCHIVO_AJUSTES).get('diagnosticos') is not False


def espera():
    """Segundos que una edición espera a los diagnósticos (ALFALFA_DIAGNOSTICOS_ESPERA)."""
    try:
        valor = float(os.environ.get(VARIABLE_ESPERA, ESPERA))
    except ValueError:
        return ESPERA
    return min(max(valor, 0.1), MAX_ESPERA)


def carpeta_de_sourcekit(raiz):
    """Donde compila sourcekit-lsp: fuera del proyecto y de lo que el sandbox de las órdenes puede escribir
    (en $TMPDIR se podrían plantar dependencias o macros ya compiladas)."""
    return (Path.home() / 'Library' / 'Caches' / 'alfalfa' / 'sourcekit'
            / hashlib.sha256(str(raiz).encode()).hexdigest()[:16])


def dentro_de(ruta, raiz):
    """¿La ruta, ya resuelta (enlaces incluidos), queda dentro de `raiz`? Así un `node_modules/.bin` o un
    `.venv/bin` del repo que el PATH del usuario incluye (direnv, una entrada relativa) cuenta como del
    proyecto."""
    if raiz is None or ruta is None:
        return False
    real, base = os.path.realpath(ruta), os.path.realpath(raiz)
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def _ejecutable(nombre, env, *extra, raiz=None):
    """(ruta, del_proyecto): el del PATH antes que el de las carpetas del proyecto (`extra`). Uno del PATH
    que en realidad está dentro de `raiz` también es del proyecto."""
    global_ = shutil.which(nombre, path=(env or os.environ).get('PATH'))
    if global_:
        return global_, dentro_de(global_, raiz)
    for carpeta in extra:
        candidato = Path(carpeta) / nombre
        if candidato.is_file() and os.access(candidato, os.X_OK):
            return str(candidato), True
    return None, False


def _sourcekit(env):
    ruta, _ = _ejecutable('sourcekit-lsp', env)
    if ruta or sys.platform != 'darwin':
        return ruta
    with contextlib.suppress(OSError, ValueError):
        import subprocess
        salida = subprocess.run(['xcrun', '--find', 'sourcekit-lsp'], capture_output=True, text=True, timeout=5)
        ruta = salida.stdout.strip()
        if salida.returncode == 0 and ruta and os.access(ruta, os.X_OK):
            return ruta
    return None


def _carpetas(desde, raiz):
    """Desde `desde` hacia arriba, sin salir de `raiz`."""
    raiz, carpeta = Path(raiz), Path(desde)
    while True:
        yield carpeta
        if carpeta == raiz or raiz not in carpeta.parents:
            return
        carpeta = carpeta.parent


def _subiendo(desde, raiz, nombres):
    """La carpeta más cercana a `desde`, sin salir de `raiz`, que tiene alguno de `nombres`."""
    return next((c for c in _carpetas(desde, raiz) if any((c / n).exists() for n in nombres)), None)


def trampa_de_sourcekit(desde, raiz):
    """El primer archivo que haría a sourcekit-lsp ejecutar algo del proyecto, o None."""
    for carpeta in _carpetas(desde, raiz):
        for nombre in TRAMPAS_SOURCEKIT:
            if os.path.lexists(carpeta / nombre):
                return carpeta / nombre
    return None


def manifiestos(paquete):
    """Package.swift y sus variantes por versión (Package@swift-6.0.swift), las que SwiftPM puede elegir."""
    return sorted(p for p in Path(paquete).glob('Package*.swift') if p.is_file() or p.is_symlink())


def huella_de_manifiestos(paquete):
    """Lo que cambia si cambia cualquier manifiesto (también a mitad de sesión), o None si no se pueden leer."""
    resumen = hashlib.sha256()
    try:
        for ruta in manifiestos(paquete):
            resumen.update(ruta.name.encode() + b'\0' + ruta.read_bytes() + b'\0')
    except OSError:
        return None
    return resumen.hexdigest()


# Lo que, escrito en un manifiesto, puede hacer que el compilador cargue o ejecute algo del repositorio, o
# que SwiftPM descargue código. Swift no tiene `eval`: sin escribir estos nombres un manifiesto no puede
# pedirlo, aunque se comporte distinto dentro y fuera del sandbox (por eso no basta con `dump-package`).
TEXTO_PELIGROSO = (
    (r'\bunsafeFlags\b', 'usa unsafeFlags (opciones del compilador que pueden cargar programas del repo)'),
    (r'\.package\s*\(', 'tiene dependencias y sourcekit-lsp tendría que resolverlas'),
    (r'\bbinaryTarget\b', 'tiene un binario precompilado'),
    (r'\.macro\s*\(', 'tiene una macro'),
    (r'\.plugin\s*\(|\bplugins\s*:', 'tiene o usa plugins'),
    (r'\bsystemLibrary\b', 'tiene una biblioteca del sistema'),
)


def motivo_en_el_texto(paquete):
    """Una primera mirada a los manifiestos, sin ejecutarlos (también a Package@swift-6.0.swift y demás)."""
    for ruta in manifiestos(paquete):
        try:
            texto = ruta.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ruta.name + ' no se puede leer'
        for patron, motivo in TEXTO_PELIGROSO:
            if re.search(patron, texto):
                return ruta.name + ' ' + motivo
    return None


TIPOS_PELIGROSOS = {'macro': 'una macro', 'plugin': 'un plugin', 'binary': 'un binario precompilado',
                    'system': 'una biblioteca del sistema'}


def motivo_en_el_volcado(volcado):
    """Lo que dice `swift package dump-package` (el manifiesto ya evaluado) que impide lanzar sourcekit-lsp."""
    if not isinstance(volcado, dict):
        return 'el manifiesto no se pudo leer'
    if _contiene_clave(volcado, 'unsafeFlags'):
        return 'el paquete usa unsafeFlags (opciones del compilador que pueden cargar programas del repo)'
    if volcado.get('dependencies'):
        return 'el paquete tiene dependencias y sourcekit-lsp tendría que resolverlas'
    for objetivo in volcado.get('targets') or []:
        tipo = objetivo.get('type') if isinstance(objetivo, dict) else None
        if tipo in TIPOS_PELIGROSOS:
            return 'el paquete tiene ' + TIPOS_PELIGROSOS[tipo] + ' (' + str(objetivo.get('name'))[:60] + ')'
        if isinstance(objetivo, dict) and objetivo.get('pluginUsages'):
            return 'el objetivo ' + str(objetivo.get('name'))[:60] + ' usa plugins'
    return None


def _contiene_clave(valor, clave):
    if isinstance(valor, dict):
        return clave in valor or any(_contiene_clave(v, clave) for v in valor.values())
    if isinstance(valor, list):
        return any(_contiene_clave(v, clave) for v in valor)
    return False


def _bins_de_node(desde, raiz):
    """Las carpetas node_modules/.bin desde el archivo hasta la raíz del proyecto."""
    return [c / 'node_modules' / '.bin' for c in _carpetas(desde, raiz) if (c / 'node_modules' / '.bin').is_dir()]


def _clave(d):
    return (d['fuente'], d.get('codigo') or '', d['mensaje'], d['gravedad'])


def _lineas_viejas(texto_antes, texto_despues):
    """{línea nueva: línea vieja} de las líneas que siguen ahí o se cambiaron en su sitio, o None."""
    if texto_antes is None or texto_despues is None:
        return None
    viejas, nuevas = texto_antes.splitlines(), texto_despues.splitlines()
    if len(viejas) > MAX_LINEAS_ALINEADAS or len(nuevas) > MAX_LINEAS_ALINEADAS:
        return None
    mapa = {}
    for etiqueta, i1, i2, j1, j2 in difflib.SequenceMatcher(None, viejas, nuevas, autojunk=False).get_opcodes():
        if etiqueta in ('equal', 'replace'):
            for k in range(min(i2 - i1, j2 - j1)):
                mapa[j1 + k + 1] = i1 + k + 1
    return mapa


def nuevos(antes, despues, texto_antes=None, texto_despues=None):
    """Los de `despues` que no estaban en `antes`.

    Con los dos textos, cada diagnóstico se lleva a su línea de antes (difflib):
    es viejo sólo si allí había uno igual. Así no cuenta como nuevo uno que se
    desplazó porque se añadieron líneas encima, y sí uno igual a otro que se
    arregló en otra parte. Sin los textos, se compara por fuente, código, texto
    y gravedad, sin mirar la línea.
    """
    mapa = _lineas_viejas(texto_antes, texto_despues)
    quedan = {}
    for d in antes:
        clave = (_clave(d), d.get('linea')) if mapa is not None else _clave(d)
        quedan[clave] = quedan.get(clave, 0) + 1
    resultado = []
    for d in despues:
        if mapa is not None:
            vieja = mapa.get(d.get('linea'))
            clave = (_clave(d), vieja) if vieja is not None else None
        else:
            clave = _clave(d)
        if clave is not None and quedan.get(clave, 0) > 0:
            quedan[clave] -= 1
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
    except Exception:  # noqa: BLE001 — MemoryError, RecursionError…: no se sabe, y no es un error de la edición
        return []
    return []


def texto_de(d):
    codigo = ' ' + d['codigo'] if d.get('codigo') else ''
    return (d['path'] + ':' + str(d['linea']) + ':' + str(d['columna']) + ' ' + d['gravedad']
            + ' [' + d['fuente'] + codigo + '] ' + d['mensaje'])


class Sandbox:
    """El prefijo de sandbox-exec del proyecto, calculado una vez por sesión. Sin sandbox no se lanza nada."""

    def __init__(self, raiz):
        self.raiz = str(raiz)
        self._prefijo = None
        self.disponible = sys.platform == 'darwin' and os.access('/usr/bin/sandbox-exec', os.X_OK)

    async def envolver(self, argv):
        from ..seatbelt import SandboxUnavailable, SeatbeltSandbox
        if not self.disponible:
            raise SandboxUnavailable('no hay sandbox Seatbelt en este sistema')
        if self._prefijo is None:
            orden = await asyncio.to_thread(SeatbeltSandbox(self.raiz, network=False).command_args, ['true'])
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
        self._cache = {}           # (fuente, raíz, ruta, sha) -> diagnósticos de ese contenido
        self._dichos = set()       # avisos que ya se dieron en la sesión
        self._permisos = {}        # clave -> True/False: la respuesta del usuario, una vez por sesión
        self._paquetes = {}        # carpeta del paquete -> (huella de sus manifiestos, motivo o None)
        self._procesos = None
        self._cerrado = False

    @property
    def env(self):
        """El entorno de lo que corre en el sandbox, leído al lanzarlo, con la caché de módulos de Clang/Swift
        donde el sandbox deja escribir."""
        from ..seatbelt import compiladores
        return dict(self._env if self._env is not None else os.environ, **compiladores.entorno())

    def entorno_de_sourcekit(self, cache):
        """El de sourcekit-lsp, que corre fuera del sandbox: su caché de módulos y sus temporales dentro de
        `cache`, nunca en $TMPDIR, donde las órdenes del sandbox podrían dejarle módulos preparados."""
        base = {k: v for k, v in (self._env if self._env is not None else os.environ).items()
                if k not in ('CLANG_MODULE_CACHE_PATH', 'SWIFTPM_MODULECACHE_OVERRIDE', 'TMPDIR')}
        modulos = str(Path(cache) / 'modulos')
        return dict(base, CLANG_MODULE_CACHE_PATH=modulos, SWIFTPM_MODULECACHE_OVERRIDE=modulos,
                    TMPDIR=str(Path(cache) / 'tmp') + os.sep)

    # --- ajustes ---------------------------------------------------------

    def cambiar(self, activo):
        """El interruptor de la app: apagado manda; encendido vuelve a lo que digan variable y archivos."""
        self.forzado = False if activo is False else None

    def activos(self):
        return not self._cerrado and activos(self.raiz, self.hogar, self.forzado)

    # --- qué hay para cada archivo ----------------------------------------

    def _etiqueta(self, nombre, raiz):
        raiz = Path(raiz)
        if raiz == self.raiz:
            return nombre
        with contextlib.suppress(ValueError):
            return nombre + ' (' + str(raiz.relative_to(self.raiz)) + ')'
        return nombre + ' (' + str(raiz) + ')'

    def _servidor(self, ruta, modo):
        """El plan del servidor para el archivo: un dict (con `motivo` si no se debe lanzar) o None si no hay."""
        from ..permisos import modos
        sufijo = ruta.suffix.lower()
        pide = modos.pide_ordenes(modo)
        if sufijo in SWIFT:
            argv = _sourcekit(self.env)
            if not argv:
                return None
            paquete = _subiendo(ruta.parent, self.raiz, ('Package.swift',))
            raiz = paquete or self.raiz
            etiqueta = self._etiqueta('sourcekit-lsp', raiz)
            clave = ('swift', str(raiz))
            if sys.platform != 'darwin':
                return {'clave': clave, 'motivo': etiqueta + ': fuera de macOS no hay con qué aislar Package.swift.'}
            if dentro_de(argv, self.raiz):
                return {'clave': clave, 'motivo': etiqueta + ': no se lanza porque el sourcekit-lsp del PATH está '
                                                             'dentro del proyecto.'}
            trampa = trampa_de_sourcekit(ruta.parent, self.raiz)
            if trampa is not None:
                return {'clave': clave, 'motivo': (
                    etiqueta + ': no se lanza porque el proyecto trae ' + str(trampa.relative_to(self.raiz))
                    + ', que le haría ejecutar órdenes del proyecto fuera del sandbox.')}
            if paquete is not None:
                motivo = motivo_en_el_texto(paquete)
                if motivo:
                    return {'clave': clave, 'motivo': etiqueta + ': no se lanza porque ' + motivo
                                                      + '. Comprueba con run_checks.'}
            # Sin indexado en segundo plano ni plugins y compilando fuera del proyecto y de $TMPDIR. Antes de
            # lanzarlo, el manifiesto se evalúa en el sandbox (`_revisar_paquete`).
            cache = carpeta_de_sourcekit(raiz)
            opciones = {'backgroundIndexing': False, 'generatedFilesPath': str(cache / 'generados'),
                        'swiftPM': {'scratchPath': str(cache / 'compilacion'), 'skipPlugins': True}}
            return {'clave': clave, 'nombre': 'sourcekit-lsp', 'etiqueta': etiqueta,
                    'argv': [argv], 'raiz': raiz, 'opciones': opciones, 'aislado': False,
                    'carga': paquete is not None, 'permiso': FUERA, 'paquete': paquete, 'cache': cache,
                    'env': self.entorno_de_sourcekit(cache),
                    'proposito': ('Lanzar sourcekit-lsp FUERA del sandbox para ver los errores de los .swift que '
                                  'se editen en ' + str(raiz) + ' durante esta sesión. Ejecuta el compilador de '
                                  'Swift sobre el código del repositorio'
                                  + (' y lee su Package.swift' if paquete is not None else '')
                                  + ' con los permisos del usuario. Antes se rechazan los paquetes con '
                                    'unsafeFlags, dependencias, macros, plugins o binarios. Su sourcekitd es un '
                                    'servicio de Xcode que el sandbox bloquea.')}
        if sufijo in PYTHON:
            for nombre in ('basedpyright-langserver', 'pyright-langserver'):
                argv, propio = _ejecutable(nombre, self.env, raiz=self.raiz)
                if argv:
                    corto = nombre.replace('-langserver', '')
                    return {'clave': ('python', str(self.raiz)), 'nombre': corto, 'etiqueta': corto,
                            'argv': [argv, '--stdio'], 'raiz': self.raiz, 'opciones': None, 'aislado': True,
                            'carga': False, 'permiso': MODO if pide or propio else None,
                            'proposito': ('Lanzar ' + (corto + ' del proyecto (' + argv + ')' if propio else corto)
                                          + ' en el sandbox para ver los errores de los .py que se editen en '
                                            'esta sesión. Puede usar el intérprete que diga la configuración '
                                            'del proyecto.')}
            return None
        if sufijo in JAVASCRIPT:
            argv, propio = _ejecutable('typescript-language-server', self.env,
                                       *_bins_de_node(ruta.parent, self.raiz), raiz=self.raiz)
            if not argv:
                return None
            raiz = _subiendo(ruta.parent, self.raiz, ('tsconfig.json', 'jsconfig.json', 'package.json')) \
                or self.raiz
            etiqueta = self._etiqueta('typescript-language-server', raiz)
            return {'clave': ('typescript', str(raiz)), 'nombre': 'typescript-language-server',
                    'etiqueta': etiqueta, 'argv': [argv, '--stdio'], 'raiz': raiz,
                    'opciones': {'preferences': {}}, 'aislado': True, 'carga': False,
                    'permiso': MODO if pide or propio else None,
                    'proposito': ('Lanzar ' + ('el typescript-language-server del proyecto (' + argv + ')'
                                               if propio else 'typescript-language-server')
                                  + ' en el sandbox para ver los errores de los .ts/.js que se editen en esta '
                                    'sesión. tsserver carga los plugins del proyecto.')}
        return None

    def _cliente(self, plan):
        cliente = self.servidores.get(plan['clave'])
        if cliente is None:
            if plan.get('cache') is not None:
                for carpeta in ('tmp', 'modulos'):
                    (Path(plan['cache']) / carpeta).mkdir(mode=0o700, parents=True, exist_ok=True)
            cliente = self.servidores[plan['clave']] = ClienteLSP(
                plan['nombre'], plan['argv'], plan['raiz'], env=plan.get('env') or self.env,
                opciones=plan['opciones'],
                idioma_de=lambda r: IDIOMAS.get(Path(r).suffix.lower(), 'plaintext'),
                envolver=self.sandbox.envolver if plan['aislado'] else None, carga=plan['carga'])
            cliente.etiqueta = plan['etiqueta']
            cliente.clave = plan['clave']
            cliente.paquete = plan.get('paquete')
            cliente.huella = self._paquetes.get(str(plan.get('paquete')), (None,))[0]
        return cliente

    async def _revisar_paquete(self, paquete):
        """Por qué no lanzar sourcekit-lsp sobre este paquete, o None. Se evalúa el manifiesto con
        `swift package dump-package` en el sandbox, una vez por versión de sus manifiestos."""
        huella = huella_de_manifiestos(paquete)
        if huella is None:
            return 'sus manifiestos no se pueden leer'
        guardado = self._paquetes.get(str(paquete))
        if guardado is not None and guardado[0] == huella:
            return guardado[1]
        motivo = motivo_en_el_texto(paquete)
        if motivo is None:
            try:
                motivo = motivo_en_el_volcado(await self._volcar_paquete(paquete))
            except Exception as exc:  # noqa: BLE001 — sin manifiesto evaluado, no se lanza
                motivo = ('no se pudo evaluar Package.swift en el sandbox ('
                          + (str(exc) or type(exc).__name__)[:150] + ')')
        # Si cambió mientras se evaluaba, lo evaluado no vale: se mira otra vez en la siguiente edición.
        if huella_de_manifiestos(paquete) != huella:
            return 'Package.swift cambió mientras se revisaba'
        self._paquetes[str(paquete)] = (huella, motivo)
        return motivo

    async def _volcar_paquete(self, paquete):
        """El manifiesto evaluado por SwiftPM, en el sandbox sin red y compilando en un temporal."""
        from ..seatbelt.compiladores import dentro_del_sandbox
        swift, propio = _ejecutable('swift', self.env, raiz=self.raiz)
        if not swift or propio:
            raise ValueError('no hay un swift del sistema con el que leerlo')
        with tempfile.TemporaryDirectory(prefix='alfalfa-manifiesto-') as temporal:
            argv = await self.sandbox.envolver(dentro_del_sandbox(
                [swift, 'package', '--package-path', str(paquete), '--scratch-path', temporal, 'dump-package']))
            # Sus temporales (los cerrojos de SwiftPM) también en el temporal, que se borra al acabar.
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(paquete), env=dict(self.env, TMPDIR=temporal + os.sep), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            try:
                salida, error = await asyncio.wait_for(proc.communicate(), MAX_VOLCADO)
            finally:
                if proc.returncode is None:
                    from .. import procesos
                    procesos.senal_grupo(proc.pid, 9)
                    await asyncio.gather(proc.wait(), return_exceptions=True)
        if proc.returncode != 0:
            ultima = (error or b'').decode(errors='replace').strip().splitlines()
            raise ValueError('código ' + str(proc.returncode) + (': ' + ultima[-1][:150] if ultima else ''))
        return json.loads(salida)

    def _decir(self, avisos, clave, texto):
        """Un aviso que sólo se da una vez por sesión."""
        if clave not in self._dichos:
            self._dichos.add(clave)
            avisos.append(texto)

    async def _permitido(self, clave, etiqueta, argv, proposito, tipo, pedir, avisos):
        """¿Dio el usuario permiso? Se pregunta una vez por sesión; un «no» también se recuerda."""
        if clave in self._permisos:
            return self._permisos[clave]
        if pedir is None:
            self._decir(avisos, ('permiso',) + tuple(clave),
                        etiqueta + ': necesita permiso y esta sesión no puede pedirlo; sin sus diagnósticos.')
            return False
        try:
            await pedir(list(argv), proposito, tipo == FUERA)
        except (ValueError, asyncio.TimeoutError):
            self._permisos[clave] = False
            self._decir(avisos, ('permiso',) + tuple(clave),
                        etiqueta + ': no se lanzó porque no se dio permiso; en esta sesión no se vuelve a '
                        'preguntar.')
            return False
        self._permisos[clave] = True
        return True

    # --- la edición --------------------------------------------------------

    async def tras_editar(self, root, cambios, antes_de, *, pedir=None, modo='auto'):
        """El informe para el resultado de la herramienta, o None si no hay nada que mirar.

        `cambios`: los `{'path', 'before', 'after'}` de la edición. `antes_de(sha)`: el
        contenido anterior (las copias `before-<sha>` del runtime) o None. `pedir(argv,
        propósito, sin_sandbox)`: pide permiso al usuario y lanza ValueError si no lo da.
        `modo`: el de permisos de la sesión.
        """
        if not self.activos():
            return None
        root = Path(root).resolve()
        archivos, avisos = [], []
        candidatos = [c for c in cambios if isinstance(c, dict) and isinstance(c.get('path'), str)
                      and c.get('after') != 'DELETED' and Path(c['path']).suffix.lower() in IDIOMAS]
        for cambio in candidatos[:MAX_ARCHIVOS]:
            ruta = root / cambio['path']
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
        # Qué servidor mira cada archivo, con los permisos ya pedidos: esperar al usuario no cuenta en el plazo.
        por_cliente, sin_servidor = {}, []
        for archivo in archivos:
            plan = self._servidor(archivo['ruta'], modo)
            if plan is not None and plan.get('motivo'):
                self._decir(avisos, ('omitido', plan['motivo']), plan['motivo'])
                # Si ya estaba en marcha (el archivo apareció después), se cierra: no se usa más.
                abierto = self.servidores.pop(plan['clave'], None)
                if abierto is not None:
                    await abierto.cerrar()
                sin_servidor.append(archivo)
                continue
            if plan is None:
                sin_servidor.append(archivo)
                continue
            if plan['permiso'] and not await self._permitido(('servidor',) + plan['clave'], plan['etiqueta'],
                                                             plan['argv'], plan['proposito'], plan['permiso'],
                                                             pedir, avisos):
                sin_servidor.append(archivo)
                continue
            if plan.get('paquete') is not None:
                # El manifiesto evaluado en el sandbox, antes de lanzar y cada vez que cambie: si ya no vale,
                # el servidor que lo cargó se cierra.
                motivo = await self._revisar_paquete(plan['paquete'])
                abierto = self.servidores.get(plan['clave'])
                if motivo or (abierto is not None
                              and getattr(abierto, 'huella', None) != self._paquetes[str(plan['paquete'])][0]):
                    if abierto is not None:
                        self.servidores.pop(plan['clave'], None)
                        await abierto.cerrar()
                    if motivo:
                        texto = plan['etiqueta'] + ': no se lanza porque ' + motivo + '. Comprueba con run_checks.'
                        self._decir(avisos, ('omitido', texto), texto)
                        sin_servidor.append(archivo)
                        continue
            cliente = self._cliente(plan)
            if cliente.caido:
                # Se perdió entre dos ediciones (p. ej. no terminó de arrancar): se dice una vez.
                self._decir(avisos, ('fallo', cliente.etiqueta),
                            cliente.etiqueta + ' falló (' + cliente.caido + '); sin sus diagnósticos.')
                sin_servidor.append(archivo)
                continue
            por_cliente.setdefault(plan['clave'], (cliente, []))[1].append(archivo)
        python_sin_servidor = [a for a in sin_servidor if a['ruta'].suffix.lower() in PYTHON]
        herramientas = linters.del_proyecto(archivos, self.raiz, self.env,
                                            python_sin_servidor=bool(python_sin_servidor))
        from ..permisos import modos
        for nombre, linter in list(herramientas.items()):
            if linter.del_proyecto or (linter.carga_codigo and modos.pide_ordenes(modo)):
                proposito = ('Pasar ' + (nombre + ' del proyecto (' + linter.programa + ')' if linter.del_proyecto
                                         else nombre)
                             + ' en el sandbox por los archivos que se editen en esta sesión. Carga la '
                               'configuración del proyecto, que es código.')
                if not await self._permitido(('linter', nombre, str(linter.cwd)), nombre, [linter.programa],
                                             proposito, MODO, pedir, avisos):
                    del herramientas[nombre]
        plazo = espera()
        hasta = asyncio.get_running_loop().time() + plazo
        trabajos = {}   # etiqueta -> corrutina que devuelve [(archivo, antes, después)] o lanza
        for cliente, lista in por_cliente.values():
            trabajos[cliente.etiqueta] = self._con_servidor(cliente, lista, hasta)
        for nombre, linter in herramientas.items():
            trabajos[nombre] = self._con_linter(nombre, linter)
        omitidos = len(candidatos) - min(len(candidatos), MAX_ARCHIVOS)
        if not trabajos and not python_sin_servidor and not avisos:
            return None
        return await self._informe(trabajos, python_sin_servidor, plazo, avisos, omitidos)

    async def _informe(self, trabajos, python_sin_servidor, plazo, avisos, omitidos):
        tareas = {etiqueta: asyncio.ensure_future(c) for etiqueta, c in trabajos.items()}
        try:
            if tareas:
                await asyncio.wait(tareas.values(), timeout=plazo)
        finally:
            # También si cancelan el turno: nada sigue corriendo ni se queda con el turno de un servidor.
            pendientes = [t for t in tareas.values() if not t.done()]
            for tarea in pendientes:
                tarea.cancel()
            if pendientes:
                await asyncio.gather(*pendientes, return_exceptions=True)
        todos_nuevos, ya_habia, con, sin_respuesta = [], 0, [], list(avisos)
        for etiqueta, tarea in tareas.items():
            exc = None if tarea.cancelled() else tarea.exception()
            if tarea.cancelled() or isinstance(exc, asyncio.TimeoutError):
                cliente = next((c for c in self.servidores.values() if getattr(c, 'etiqueta', None) == etiqueta),
                               None)
                arrancando = cliente is not None and cliente.arrancando
                sin_respuesta.append(etiqueta + ' no contestó en ' + _segundos(plazo)
                                     + (' (sigue arrancando; servirá en las próximas ediciones)' if arrancando else '')
                                     + '; esta vez sin sus diagnósticos.')
                continue
            if exc is not None:
                motivo = str(exc)[:200] or type(exc).__name__
                self._decir(sin_respuesta, ('fallo', etiqueta), etiqueta + ' falló (' + motivo
                            + '); sin sus diagnósticos.')
                continue
            con.append(etiqueta)
            for archivo, base, despues in tarea.result():
                lista = nuevos(base, despues, archivo['antes'], archivo['texto'])
                todos_nuevos += lista
                ya_habia += len(despues) - len(lista)
        if python_sin_servidor and 'ruff' not in con:
            # Sin servidor de Python y sin ruff (o ruff falló): al menos la sintaxis.
            con.append(SOLO_SINTAXIS)
            for archivo in python_sin_servidor:
                base = [] if archivo['antes'] is None else sintaxis_python(archivo['rel'], archivo['antes'])
                despues = sintaxis_python(archivo['rel'], archivo['texto'])
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
        if omitidos:
            informe['archivos_sin_mirar'] = omitidos
            sin_respuesta.append('Sólo se miraron los primeros ' + str(MAX_ARCHIVOS) + ' archivos de la edición.')
            informe['sin_respuesta'] = sin_respuesta
        informe['nota'] = NOTA if todos_nuevos or not con or sin_respuesta else NOTA_LIMPIO
        return informe

    def _guardar(self, clave, valor):
        self._cache[clave] = valor
        while len(self._cache) > MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))

    async def _con_servidor(self, cliente, archivos, hasta):
        await asyncio.shield(cliente.arrancar())
        fuente = cliente.nombre
        resultado = []
        async with cliente.lock:
            try:
                bases = {a['rel']: self._cache.get((fuente, cliente.raiz, a['rel'], a['before'])) for a in archivos}
                faltan = [a for a in archivos if bases[a['rel']] is None and a['antes'] is not None]
                if faltan:
                    # Primero todos con su contenido de antes: la base de A no se calcula con B ya cambiado.
                    for archivo in archivos:
                        if archivo['antes'] is not None:
                            await cliente.abrir(archivo['ruta'], archivo['antes'])
                    for archivo in faltan:
                        bases[archivo['rel']] = desde_lsp(fuente, archivo['rel'],
                                                          await cliente.diagnosticos(archivo['ruta'], hasta))
                for archivo in archivos:
                    await cliente.abrir(archivo['ruta'], archivo['texto'])
                for archivo in archivos:
                    despues = desde_lsp(fuente, archivo['rel'], await cliente.diagnosticos(archivo['ruta'], hasta))
                    self._guardar((fuente, cliente.raiz, archivo['rel'], archivo['after']), despues)
                    resultado.append((archivo, bases[archivo['rel']] or [], despues))
            finally:
                for archivo in archivos:
                    await cliente.cerrar_documento(archivo['ruta'])
        return resultado

    async def _con_linter(self, nombre, linter):
        if self._procesos is None:
            self._procesos = asyncio.Semaphore(MAX_PROCESOS)

        async def lint(texto, rel):
            async with self._procesos:
                return await linter.lint(rel, texto, self.sandbox.envolver)

        async def uno(archivo):
            clave = (nombre, str(linter.cwd), archivo['rel'])
            base = self._cache.get(clave + (archivo['before'],))
            despues = await lint(archivo['texto'], archivo['rel'])
            if base is None:
                base = [] if archivo['antes'] is None else await lint(archivo['antes'], archivo['rel'])
            self._guardar(clave + (archivo['after'],), despues)
            return archivo, base, despues
        return list(await asyncio.gather(*(uno(a) for a in linter.archivos)))

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


__all__ = ['Diagnosticos', 'ServidorCaido', 'activos', 'espera', 'nuevos']
