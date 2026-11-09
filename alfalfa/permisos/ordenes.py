"""Qué permisos puede conceder AlfalfaCode sin molestar al usuario.

Es una lista de permitidos, nunca de prohibidos. Una lista negra sobre una
línea de shell no se sostiene: `;`, `&&`, `$(...)`, las comillas invertidas y
las redirecciones convierten cualquier orden inocente en otra distinta, y basta
un caso olvidado para que la lista entera deje de significar nada. Aquí sólo
pasa lo que se reconoce por completo; todo lo demás va a la cola del usuario.

El criterio no es "parece inofensivo" sino "no puede tocar nada fuera del
worktree del ticket". Un worktree es desechable y su trabajo pasa por revisión
y pull request antes de existir en `main`, así que leer y escribir dentro de él
no necesita tu permiso. Salir de él, sí.
"""
import ast
import glob
import os
import re
import shlex
import shutil
import subprocess
from itertools import dropwhile
from pathlib import Path

from .rutas import (  # noqa: F401
    ALMACENES_WEB,
    CONTENEDORES_DE_APPS,
    SENSIBLES,
    VOLUMEN_DE_DATOS,
    _alcanza_por_inodo,
    _expone_por_nombre,
    _sin_volumen_de_datos,
    expone_sesiones,
    sensible,
)

# Cualquiera de estos encadena, redirige o sustituye: si aparece, no se analiza
# la orden, se pregunta.
# Encadenar y redirigir lo detecta el tokenizador, que distingue un operador de
# un argumento entrecomillado. Sustituir y escapar, no: al quitar las comillas
# se pierde la diferencia entre '$HOME' literal y $HOME expandido, así que estos
# se buscan sobre el texto crudo y se rechazan estén donde estén.
SUSTITUCION = set('`$\n\r\\')

# Órdenes que sólo leen. `env` y `printenv` quedan fuera a propósito: imprimen
# el token del forge que vive en el entorno del proceso.
LECTURA = {
    'ls', 'cat', 'head', 'tail', 'wc', 'file', 'stat', 'pwd', 'echo', 'true',
    'basename', 'dirname', 'which', 'date', 'find', 'grep', 'rg', 'sort',
    'uniq', 'diff', 'cmp', 'tree', 'du', 'cut', 'tr', 'nl', 'realpath', 'md5', 'shasum',
    # Saber si una compilación sigue viva (`pgrep -fl xcodebuild`): sólo lista procesos.
    'pgrep',
    # Validar un JSON contra su esquema: lee los dos y sólo informa (un agente
    # pidió permiso para revalidar un ejemplo de su propuesta).
    'jsonschema',
}

# Órdenes con subcomando: el ejecutable no basta para saber si escribe.
SUBCOMANDOS = {
    # `push`, `reset`, `clean`, `config` y `remote set-url` salen del worktree
    # o reescriben historia: los decide el usuario.
    'git': {'status', 'diff', 'log', 'show', 'branch', 'rev-parse', 'ls-files',
            'describe', 'blame', 'shortlog', 'tag', 'cat-file', 'grep',
            # Un agente esperó permiso por ls-tree y merge-base --is-ancestor.
            'ls-tree', 'merge-base', 'rev-list', 'check-ignore'},
    # El registro unificado de macOS, sólo para leer (log show); nunca config ni erase.
    'log': {'show'},
}

# Subcomandos de git que sólo tocan el worktree del ticket y su índice. No son
# de lectura, pero no salen de una rama desechable que todavía tiene que pasar
# por revisión y pull request. Lo que publica, reescribe historia o cambia la
# configuración del usuario sigue fuera de esta lista a propósito.
# `checkout` sólo en su forma de archivos (`git checkout -- ruta`), que es lo
# mismo que `git restore`: un desarrollador que devolvía catalog-data.json a su
# estado se quedó esperando a una persona. Cambiar de rama con él, no.
GIT_ESCRITURA = {'add', 'commit', 'restore', 'stash', 'switch', 'merge', 'mv', 'checkout'}
SUBCOMANDOS['git'] |= GIT_ESCRITURA

# Opciones que convierten una orden de lectura en otra cosa.
BANDERAS_PROHIBIDAS = {
    # -fls, -fprintf y -fprint0 escriben su salida en un archivo, como -fprint.
    'find': {'-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprint0', '-fprintf', '-fls'},
    'grep': set(),
    'git': {'--global', '--system', '-c', '--output', '-o', '--output-directory'},
}


# Segundo escalón: las herramientas del propio proyecto. Esto NO es de sólo
# lectura y no se finge que lo sea: `npm ci` ejecuta los postinstall que vengan
# en las dependencias, es decir código de terceros bajado de la red. Se concede
# porque sin ello un agente no puede compilar nada y el trabajo se para cada
# pocos minutos, y porque el daño queda acotado al worktree del ticket, que es
# desechable y pasa por revisión y pull request antes de tocar `main`.
HERRAMIENTAS = {
    'npm': {'ci', 'install', 'i', 'run', 'test', 'exec', 'ls', 'view', 'outdated'},
    'pnpm': {'install', 'i', 'run', 'test', 'exec', 'build', 'list'},
    'yarn': {'install', 'run', 'test', 'build', 'list'},
    'npx': None,
    'pytest': None,
    'tsc': None,
    'eslint': None,
    'prettier': None,
    'vitest': None,
    'jest': None,
    'cwebp': None,
    'magick': None,
    'convert': None,
    'sips': None,
    'ffmpeg': None,
    'swift': {'build', 'test'},
    'make': None,
    'cargo': {'build', 'test', 'check', 'clippy'},
    'go': {'build', 'test', 'vet'},
    # Genera el .xcodeproj desde project.yml del propio worktree (un agente esperó permiso por esto).
    'xcodegen': {'generate'},
    # Analizadores de Python: el verificador de la línea los pide siempre.
    'mypy': None,
    'ruff': {'check'},
}

# Scripts de paquete que arrancan un servidor y no terminan nunca: un agente
# que los lanza se queda colgado hasta que vence su tiempo.
SCRIPTS_SIN_FIN = {'dev', 'start', 'serve', 'watch', 'preview'}

# Borrar caché es rutina de cualquier build, pero `rm` no se parece a nada más
# de esta lista: un fallo aquí no se traduce en una pregunta de más, sino en
# archivos que ya no están. Por eso no basta con que la ruta caiga dentro del
# worktree; el nombre tiene que ser de una caché conocida, y se comprueba uno
# por uno. Nada de comodines, nada de rutas con '..', nada de la raíz.
CACHES = {'.next', '.nuxt', '.turbo', '.svelte-kit', '.parcel-cache', '.vite',
          '.cache', '.pytest_cache', '__pycache__', '.mypy_cache', '.ruff_cache',
          '.gradle', '.venv-test', 'coverage', '.coverage', '.nyc_output',
          # Salida de Playwright: la regenera cada ejecución de pruebas.
          'test-results', 'playwright-report', 'blob-report'}
# Directorios de trabajo que un agente se crea y se lleva: `.tmp-vitest`.
PREFIJOS_DE_CACHE = ('.tmp-', '.temp-')  # incluye .tmp-alfalfa/, donde los agentes dejan sus temporales
BANDERAS_DE_BORRADO = {'-r', '-f', '-rf', '-fr', '-R', '-rF', '--recursive', '--force'}


def _es_cache(argumento, espacio):
    """¿Este argumento nombra una caché regenerable dentro del worktree?"""
    if not argumento or argumento.startswith('-'):
        return False
    if '*' in argumento or '?' in argumento or argumento.startswith('~'):
        return False
    destino = Path(argumento)
    raiz = Path(espacio).resolve()
    resuelto = (raiz / destino).resolve() if not destino.is_absolute() else destino.resolve()
    # Dentro del worktree y nunca el worktree entero.
    if resuelto == raiz or raiz not in resuelto.parents:
        return False
    if '..' in destino.parts:
        return False
    nombre = resuelto.name
    if nombre in CACHES or nombre.startswith(PREFIJOS_DE_CACHE):
        return True
    # Lo que hay dentro de una caché también se regenera: el agente borraba
    # test-results/<su-evidencia> y quedaba esperando a una persona.
    if any(p in CACHES or p.startswith(PREFIJOS_DE_CACHE) for p in resuelto.relative_to(raiz).parts[:-1]):
        return True
    # `node_modules/.cache`, pero no `node_modules` entero.
    return resuelto.parent.name == 'node_modules' and nombre == '.cache'


def _entrar_en_el_espacio(programa, argumentos, espacio):
    """`cd` dentro del worktree: (veredicto, motivo), o None si no es ese caso.

    Los agentes encadenan `cd <worktree>/sub && <orden>` en casi todo. `cd` no
    lee ni escribe nada, y moverse hacia dentro no puede sacar a las órdenes
    siguientes del worktree, que es lo que comprueba el resto de la lista.
    """
    if programa != 'cd':
        return None
    rutas = [a for a in argumentos if not a.startswith('-')]
    if len(rutas) != 1 or len(argumentos) != len(rutas):
        return False, 'cd con algo más que una ruta'
    destino = rutas[0]
    if '*' in destino or '?' in destino or destino.startswith('~') or destino == '-':
        return False, 'cd a un destino que no puedo resolver'
    raiz = Path(espacio).resolve()
    ruta = Path(destino)
    resuelto = (raiz / ruta).resolve() if not ruta.is_absolute() else ruta.resolve()
    if resuelto != raiz and raiz not in resuelto.parents:
        return False, 'cd fuera del worktree: ' + destino
    return True, 'entra en una carpeta del worktree'


def _es_evidencia(ruta, espacio):
    """Capturas o informes de evidencia de un ticket, dentro del worktree.

    Rehacerlas empieza por borrar las viejas; están en git, así que se
    recuperan. Un agente se paró a pedir permiso para regenerar las suyas en
    mitad de una carrera. Nunca la carpeta de evidencia entera.
    """
    if any(c in ruta for c in '*?$`'):
        return False
    raiz = Path(espacio).resolve()
    destino = (raiz / ruta).resolve() if not Path(ruta).is_absolute() else Path(ruta).resolve()
    if raiz not in destino.parents:
        return False
    partes = destino.relative_to(raiz).parts
    return any(p in ('evidence', 'evidencia') and len(partes) - i >= 3 for i, p in enumerate(partes))


def _carpeta_de_evidencia(ruta, espacio):
    """Una carpeta bajo evidence/ o evidencia/ del worktree (no esa carpeta misma)."""
    if ruta.startswith('-') or ruta.startswith('~') or any(c in ruta for c in '*?$`'):
        return False
    raiz = Path(espacio).resolve()
    destino = (raiz / ruta).resolve() if not Path(ruta).is_absolute() else Path(ruta).resolve()
    if raiz not in destino.parents:
        return False
    partes = destino.relative_to(raiz).parts
    return any(p in ('evidence', 'evidencia') for p in partes[:-1])


# Xcode para proyectos de iPhone (una app de iPhone): un revisor se paró a pedir
# permiso para `xcrun simctl list devices booted`. Compilar y probar se trata
# como `swift build/test`; publicar o exportar, nunca.
XCODEBUILD_PROHIBIDO = {'archive', 'install', 'installsrc', '-exportArchive', '-exportNotarizedApp',
                        '-importLocalizations', '-exportLocalizations', '-allowProvisioningUpdates'}
SIMCTL_LECTURA = {'list', 'bootstatus', 'getenv', 'get_app_container', 'help'}
SIMCTL_ESCRITURA = SIMCTL_LECTURA | {'boot', 'shutdown', 'install', 'launch', 'terminate', 'io', 'ui',
                                     'status_bar', 'openurl', 'uninstall', 'create'}


def _sed_que_solo_imprime(argumentos):
    """`sed -n 'N,Mp'` (uno o varios -e): imprime líneas y no escribe nada."""
    if '-n' not in argumentos:
        return False
    guiones, i = [], 0
    while i < len(argumentos):
        a = argumentos[i]
        if a == '-e' and i + 1 < len(argumentos):
            guiones.append(argumentos[i + 1])
            i += 2
            continue
        if a.startswith('-') and a not in ('-n', '-E', '-r'):
            return False  # -i, -f, -w… pueden escribir o leer guiones ajenos
        i += 1
    sueltos = [a for j, a in enumerate(argumentos) if not a.startswith('-')
               and not (j and argumentos[j - 1] == '-e')]
    if not guiones and sueltos:
        guiones.append(sueltos.pop(0))
    imprime = re.compile(r'(?:\d+|\$)(?:,(?:\d+|\$))?p')
    return bool(guiones) and all(imprime.fullmatch(g) for g in guiones) and bool(sueltos)


DERIVED_DATA = Path.home() / 'Library' / 'Developer' / 'Xcode' / 'DerivedData'


def _resultados_de_xcode(argumentos):
    """`xcresulttool get … --path` sobre los resultados que Xcode guarda en DerivedData.

    Sólo lee: un agente se paró a pedir permiso para leer el resultado de sus
    propias pruebas, que xcodebuild deja ahí si no se le da otra carpeta.
    """
    if 'xcresulttool' not in argumentos or '--path' not in argumentos:
        return set()
    resto = argumentos[argumentos.index('xcresulttool') + 1:]
    if next((a for a in resto if not a.startswith('-')), '') not in ('get', 'graph'):
        return set()
    i = argumentos.index('--path') + 1
    if i >= len(argumentos):
        return set()
    try:
        ruta = Path(argumentos[i]).resolve()
    except OSError:
        return set()
    temporales = (DERIVED_DATA.resolve(), Path('/private/tmp'), Path('/tmp').resolve())
    return {argumentos[i]} if ruta.suffix == '.xcresult' and any(t in ruta.parents for t in temporales) else set()


def _xcode(programa, argumentos, escritura, espacio=None):
    """xcrun / xcodebuild acotados: (veredicto, motivo), o None si no es ese caso."""
    if programa == 'xcodebuild':
        if XCODEBUILD_PROHIBIDO & set(argumentos):
            return False, 'xcodebuild que publica o exporta'
        return True, 'compila o prueba con Xcode'
    if programa != 'xcrun':
        return None
    sueltos = [a for a in argumentos if not a.startswith('-')]
    if not sueltos:
        if set(argumentos) <= {'--find', '-f', '--show-sdk-path', '--show-sdk-version', '--sdk', '-sdk'}:
            return True, 'consulta la instalación de Xcode'
        return False, 'xcrun con opciones que no reconozco'
    herramienta = sueltos[0]
    resto = argumentos[argumentos.index(herramienta) + 1:]
    if argumentos.index(herramienta) \
            and not set(argumentos[:argumentos.index(herramienta)]) <= {'--sdk', '-sdk', 'iphonesimulator', 'iphoneos',
                                                                        'macosx'}:
        return False, 'xcrun con opciones que no reconozco'
    if herramienta == 'xcodebuild':
        return _xcode('xcodebuild', resto, escritura)
    if herramienta == 'simctl':
        accion = next((a for a in resto if not a.startswith('-')), '')
        if accion in (SIMCTL_ESCRITURA if escritura else SIMCTL_LECTURA):
            return True, 'usa el simulador de iPhone (' + accion + ')'
        return False, 'xcrun simctl ' + (accion or '(sin acción)') + (' no está permitido' if escritura
                                                                     else ': un revisor sólo consulta')
    if herramienta in ('--find', 'swift', 'swiftc'):
        return True, 'herramienta de Xcode'
    # Sólo lista los dispositivos que ve Xcode (iPhone por cable o por red): el
    # revisor de seguridad de una app esperó permiso por `xcrun xcdevice list`.
    if herramienta == 'xcdevice' and [a for a in resto if not a.startswith('-')][:1] == ['list'] \
            and set(a for a in resto if a.startswith('-')) <= {'--timeout', '--both'}:
        return True, 'lista los dispositivos que ve Xcode'
    if herramienta == 'xcresulttool' and next((a for a in resto if not a.startswith('-')), '') == 'export' \
            and '--output-path' in resto and resto.index('--output-path') + 1 < len(resto) \
            and _bajo_cache(resto[resto.index('--output-path') + 1], espacio or '/nonexistent'):
        return True, 'saca las capturas de un resultado de pruebas a una carpeta temporal'
    if herramienta == 'xcresulttool' \
            and next((a for a in resto if not a.startswith('-')), '') in ('get', 'graph', 'version'):
        return True, 'lee los resultados de las pruebas de Xcode'
    return False, 'xcrun ' + herramienta + ' no está entre las herramientas de Xcode permitidas'


def _temporal_del_ticket(ruta, espacio):
    """¿Es /tmp/<algo con el ID de este ticket>? Su carpeta temporal, regenerable.

    Un agente esperó permiso por `rm -rf /tmp/agente-b5e7e16bde` al terminar
    su tarea: era su propia carpeta de pruebas.
    """
    ticket = Path(espacio).name.removesuffix('-rival') if espacio else ''
    destino = Path(ruta)
    partes = destino.parts
    if not re.fullmatch(r'[0-9a-f]{10}', ticket) or not destino.is_absolute() or '..' in partes:
        return False
    if len(partes) == 3 and partes[1] == 'tmp':
        nombre = partes[2]
    elif len(partes) == 4 and partes[1:3] == ('private', 'tmp'):
        nombre = partes[3]
    else:
        return False
    return ticket in nombre


def _borrado_de_cache(programa, argumentos, espacio, escritura):
    """rm acotado a cachés: (veredicto, motivo), o None si no es ese caso."""
    if programa not in ('rm', 'rmdir'):
        return None
    rutas = [a for a in argumentos if not a.startswith('-')]
    banderas = [a for a in argumentos if a.startswith('-')]
    if not rutas:
        return False, programa + ' sin ruta que borrar'
    if any(b not in BANDERAS_DE_BORRADO | {'-p', '--parents'} for b in banderas):
        return False, programa + ' con una opción que no reconozco'
    rutas = [r for r in rutas if not _temporal_del_ticket(r, espacio)]
    if not rutas:
        return True, 'borra su carpeta temporal en /tmp, con el nombre de su ticket'
    if not escritura:
        # Un revisor limpia sus temporales (.tmp-alfalfa, test-results), que están
        # fuera de la huella; los archivos del trabajo que juzga, nunca.
        if all(_es_cache(r, espacio) for r in rutas):
            return True, 'borra sus carpetas temporales dentro del worktree'
        return False, 'un revisor no borra nada en la rama que juzga'
    fuera = [r for r in rutas if not _es_cache(r, espacio) and not _es_propio(r, espacio)
             and not _es_evidencia(r, espacio)]
    if fuera:
        return False, 'rm sobre algo que no es una caché ni un archivo nuevo del agente: ' + fuera[0]
    return True, 'borra cachés o archivos nuevos del agente dentro del worktree'


BANDERAS_DE_COPIA = {'-r', '-R', '-f', '-p', '-a', '-n', '-v', '-rf', '-Rf', '-fr'}


def _copia_dentro(programa, argumentos, espacio, escritura):
    """cp/mv con origen y destino dentro del worktree: (veredicto, motivo) o None.

    El agente ya escribe cualquier archivo de su carpeta; copiarlo no le da
    nada nuevo. Un desarrollador que restauraba catalog-data.json desde su
    propia copia en .tmp-alfalfa dejó el ticket de la web parado esperándote.
    """
    if programa not in ('cp', 'mv'):
        return None
    rutas = [a for a in argumentos if not a.startswith('-')]
    if any(a.startswith('-') and a not in BANDERAS_DE_COPIA for a in argumentos):
        return False, programa + ' con una opción que no reconozco'
    if len(rutas) < 2:
        return False, programa + ' sin origen y destino'
    raiz = Path(espacio).resolve()
    for ruta in rutas:
        if ruta.startswith('~') or any(c in ruta for c in '*?['):
            return False, programa + ' con una ruta que no puedo acotar: ' + ruta
        destino = Path(ruta) if Path(ruta).is_absolute() else raiz / ruta
        resuelto = destino.resolve()
        if resuelto == raiz or raiz not in resuelto.parents:
            return False, 'apunta fuera del worktree: ' + ruta
        if '.git' in resuelto.relative_to(raiz).parts:
            return False, programa + ' dentro de .git'
    if not escritura and not _es_cache(rutas[-1], espacio):
        return False, 'un revisor no escribe en la rama que juzga'
    return True, programa + ' entre archivos del propio worktree'


def _sin_seguimiento(ruta, espacio):
    """¿git no sigue nada en esta ruta? Entonces no es del repositorio."""
    try:
        r = subprocess.run(['git', '-C', str(espacio), 'ls-files', '--error-unmatch', '--', str(ruta)],
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode != 0


def _es_propio(argumento, espacio):
    """Archivo o carpeta que el agente creó y git no sigue, o un comodín
    cuyas coincidencias lo son todas.

    Un agente se hacía specs y capturas de comprobación (zz-nav-check.spec.ts)
    y al limpiarlas quedaba esperando a una persona. Nada del repositorio se
    puede borrar así: git lo sigue y la regla lo rechaza.
    """
    if not argumento or argumento.startswith(('-', '~')):
        return False
    raiz = Path(espacio).resolve()
    if not (raiz / '.git').exists():
        return False
    patron = Path(argumento) if Path(argumento).is_absolute() else raiz / argumento
    if '..' in Path(argumento).parts:
        return False
    try:
        padre = patron.parent.resolve()
    except OSError:
        return False
    if padre != raiz and raiz not in padre.parents:
        return False
    if any(c in patron.name for c in '*?['):
        if any(c in str(patron.parent) for c in '*?['):
            return False
        coincidencias = [Path(m) for m in glob.glob(str(patron))]
        return all(_es_cache(str(m), espacio) or _sin_seguimiento(m, raiz) for m in coincidencias)
    destino = padre / patron.name
    if destino == raiz:
        return False
    return _sin_seguimiento(destino, raiz)



def _herramienta_del_proyecto(programa, argumentos):
    """(¿aprobable?, motivo) para el escalón de herramientas."""
    if programa not in HERRAMIENTAS:
        return None
    permitidos = HERRAMIENTAS[programa]
    if programa == 'npm' and argumentos in (['root'], ['root', '-g'], ['prefix'], ['prefix', '-g']):
        return True, 'npm sólo dice dónde instala'
    if programa == 'npm' and '--prefix' in argumentos[:-1]:
        # `npm --prefix demo test`: la carpeta no es el subcomando. Que no salga
        # del worktree lo comprueba después el recorrido de los argumentos.
        posicion = argumentos.index('--prefix')
        argumentos = argumentos[:posicion] + argumentos[posicion + 2:]
    sub = next((a for a in argumentos if not a.startswith('-')), '')
    if argumentos in (['--version'], ['-version'], ['version']):
        # `swift --version && swift build …`: un revisor esperó permiso por esto.
        return True, programa + ' sólo dice su versión'
    if programa == 'go' and sub == 'run':
        # Un paquete del propio worktree corre como sus pruebas: un revisor de
        # un proyecto Go esperó permiso por `go -C server run ./cmd/labred`. Nada
        # descargado (@versión) ni fuera de la carpeta.
        resto = argumentos[argumentos.index('run') + 1:]
        paquete = next((a for a in resto if not a.startswith('-')), '')
        if (paquete == '.' or paquete.startswith('./')) and '@' not in paquete and '..' not in Path(paquete).parts:
            return True, 'go run de un paquete del worktree'
    if permitidos is not None and sub not in permitidos:
        return False, programa + ' ' + (sub or '(sin subcomando)') + ' no es una orden de construcción'
    if sub in ('run', 'exec'):
        siguientes = [a for a in argumentos if not a.startswith('-')]
        script = siguientes[1] if len(siguientes) > 1 else ''
        if script in SCRIPTS_SIN_FIN:
            return False, programa + ' ' + script + ' no termina nunca y colgaría al agente'
    return True, programa + ' es una herramienta del proyecto, dentro del worktree'


# -clonedSourcePackagesDirPath y -packageCachePath: descargas de paquetes,
# regenerables. Un agente esperó permiso por `xcodebuild … -clonedSourcePackagesDirPath
# /private/tmp/541545c60a-packages … test` en una app de Mac.
SALIDA_DE_COMPILACION = {'--scratch-path', '--build-path', '-derivedDataPath', '-resultBundlePath',
                         '-clonedSourcePackagesDirPath', '-packageCachePath'}


def _salida_de_compilacion_temporal(programa, argumentos, espacio=''):
    """Carpetas temporales donde compilar sin ensuciar el worktree.

    El verificador de la línea de una app de iPhone compiló con
    `swift build --scratch-path /tmp/integracion-swiftpm` para no dejar
    .build en la rama común; pedía permiso, ocupaba la línea y nadie podía
    resolver un conflicto. Sólo cuenta el valor de esas opciones, en una
    subcarpeta de /tmp o /private/tmp.
    """
    if programa not in ('swift', 'xcodebuild', 'xcrun'):
        return set()
    sueltos = set()
    for i, a in enumerate(argumentos[:-1]):
        if a in SALIDA_DE_COMPILACION:
            valor = argumentos[i + 1]
            ruta = Path(valor)
            # Cualquier subcarpeta de /tmp: es salida de compilación regenerable.
            # Exigir un prefijo o el id del ticket en el nombre dejó a agentes
            # esperando permiso por /tmp/ollama-cloud-chat-… y /tmp/occ-retention-build.
            partes = ruta.parts
            if ruta.is_absolute() and '..' not in partes and (
                    (len(partes) >= 3 and partes[1] == 'tmp')
                    or (len(partes) >= 4 and partes[1:3] == ('private', 'tmp'))):
                sueltos.add(valor)
    return sueltos


def _sdk_de_xcode(programa, argumentos):
    """El SDK que trae Xcode (`swift build --sdk …/iPhoneSimulator26.5.sdk`) sólo se lee.

    Un revisor esperó permiso por compilar el paquete de un juego para el
    simulador de iPhone: el SDK está fuera del worktree, pero dentro de Xcode.
    """
    if programa != 'swift':
        return set()
    return {argumentos[i + 1] for i, a in enumerate(argumentos[:-1])
            if a == '--sdk' and '..' not in Path(argumentos[i + 1]).parts and re.search(
                r'^/[^\s]*/Xcode[^/]*\.app/Contents/Developer/Platforms/[^/]+/Developer/SDKs/[^/]+\.sdk/?$',
                argumentos[i + 1])}


# Mirar si un programa está instalado no toca nada personal: el agente esperó diez
# minutos por `ls /opt/homebrew/lib/node_modules` varias veces.
# Sólo datos del archivo, no su contenido; el resto del disco sigue preguntando,
# incluidos el repo principal y las carpetas de otros tickets.
SOLO_METADATOS = {'ls', 'stat', 'file', 'realpath', 'which'}
RUTAS_INSTALADAS = re.compile(r'/(opt/homebrew|usr/local)/lib/node_modules(/.*)?|/Applications/[^/]+\.app(/.*)?')


def _ruta_instalada(argumento):
    """¿La ruta, ya resuelta, cae en una carpeta de programas instalados?"""
    if not argumento or not Path(argumento).is_absolute() or any(c in argumento for c in '*?['):
        return False
    return bool(RUTAS_INSTALADAS.fullmatch(os.path.realpath(argumento)))


def _fuera_del_espacio(argumento, espacio):
    """¿Este argumento apunta fuera del worktree del ticket?"""
    if argumento and argumento.startswith('-'):
        # El valor pegado a una opción también es una ruta: `git diff
        # --output=/Users/…/.zshrc` escribía fuera porque toda opción se daba
        # por buena, y `sort -o/ruta` igual.
        if '=' in argumento and argumento.startswith('--'):
            return _fuera_del_espacio(argumento.split('=', 1)[1], espacio)
        if re.match(r'-[A-Za-z][/~]', argumento):
            return _fuera_del_espacio(argumento[2:], espacio)
        return False
    if not argumento:
        return False
    if '/' not in argumento and not argumento.startswith('~'):
        return False
    if argumento.startswith('~'):
        return True
    destino = Path(argumento)
    raiz = Path(espacio).resolve()
    resuelto = (raiz / destino).resolve() if not destino.is_absolute() else destino.resolve()
    return raiz != resuelto and raiz not in resuelto.parents


# Codex no ejecuta nada directamente: todo llega envuelto en `/bin/zsh -lc '...'`.
# Sin desenvolverlo, la lista blanca rechazaría hasta `git status`.
INTERPRETES = {'sh', 'bash', 'zsh', 'dash'}


# Carpetas donde viven los programas instalados en el Mac. La lista blanca juzga
# por el nombre (`git`, `cat`, `pytest`), así que `/tmp/x/git status` pasaba por
# el git del sistema aunque fuera cualquier otro programa llamado igual.
# Un programa escrito con ruta sólo cuenta como el conocido si está aquí o
# dentro del worktree del ticket (.venv/bin/pytest, node_modules/.bin/vitest).
CARPETAS_DE_PROGRAMAS = (
    r'/bin', r'/sbin', r'/usr/bin', r'/usr/sbin', r'/usr/libexec',
    r'/usr/local/bin', r'/usr/local/sbin', r'/usr/local/opt/[^/]+/bin',
    r'/opt/homebrew/bin', r'/opt/homebrew/sbin', r'/opt/homebrew/opt/[^/]+/bin',
    r'/opt/homebrew/Cellar/[^/]+/[^/]+/bin',
    r'/Library/Frameworks/Python\.framework/Versions/[^/]+/bin',
    r'/Library/Developer/CommandLineTools/usr/bin',
    # Las herramientas de Xcode (swift, xcodebuild…) y los programas de las apps
    # instaladas (Chrome sin ventana, Blender -b).
    r'/Applications/Xcode[^/]*\.app/Contents/Developer/.+',
    r'/Applications/[^/]+\.app/Contents/MacOS',
)


def _en_carpeta_del_sistema(programa):
    """¿Es una ruta absoluta a un programa instalado en el Mac?"""
    if not Path(programa).is_absolute():
        return False
    carpeta = os.path.dirname(os.path.normpath(programa))
    return any(re.fullmatch(patron, carpeta) for patron in CARPETAS_DE_PROGRAMAS)


def _repo_del_integrador():
    """Raíz del repositorio de quien integra el arnés, o None.

    El integrador la registra en `ajustes.repo_del_integrador`. Sin integrador no
    hay ninguna.
    """
    from .. import ajustes
    repo = ajustes.repo_del_integrador()
    return Path(os.path.normpath(repo)) if repo else None


def _venv_del_integrador():
    """`.venv/bin` del repositorio del integrador, o None si no se encuentra."""
    repo = _repo_del_integrador()
    return repo / '.venv' / 'bin' if repo else None


# Órdenes que leen sin poder escribir ni ejecutar nada, ni con sus opciones. Son
# las únicas que se conceden fuera del worktree, sobre una carpeta de referencia:
# sort -o, uniq ENTRADA SALIDA y tree -o escriben, y por eso no están aunque sí
# estén en LECTURA. rg sí está: las opciones con las que ejecuta programas
# (RG_EJECUTA) se niegan en cualquier carpeta.
LECTURA_DE_REFERENCIA = {'ls', 'cat', 'head', 'tail', 'wc', 'stat', 'find', 'grep', 'diff', 'cmp',
                         'du', 'nl', 'realpath', 'md5', 'shasum', 'sed', 'rg'}
# Opciones que siguen enlaces al recorrer: sacarían la lectura de la carpeta.
SIGUEN_ENLACES = {'find': {'-L', '-H', '-follow'}, 'grep': {'-R', '--dereference-recursive'},
                  'rg': {'-L', '--follow'}}
# Letra que, agrupada con otras (`-nR`, `-nL`), sigue enlaces.
LETRA_DE_ENLACES = {'grep': 'R', 'rg': 'L'}


def rg_ejecuta(argumento):
    """¿Esta opción hace que rg lance otro programa?

    `--pre CMD` ejecuta CMD por cada archivo, `--hostname-bin CMD` lo ejecuta para
    los enlaces y `-z`/`--search-zip` lanza descompresores del PATH. Antes `rg
    --pre ./x patrón .` se concedía como lectura dentro del worktree: era ejecutar
    cualquier guion sin permiso. Una letra z agrupada (`-nz`) cuenta igual; con
    una opción corta que lleve valor pegado (`-ezeta`) se pregunta de más, nunca
    de menos.
    """
    if argumento in ('--pre', '--hostname-bin', '--search-zip') \
            or argumento.startswith(('--pre=', '--hostname-bin=')):
        return True
    return bool(re.fullmatch(r'-[A-Za-z0-9]*z[A-Za-z0-9]*', argumento))


def carpetas_de_referencia():
    """Carpetas de sólo lectura que cualquier agente puede consultar sin preguntar.

    `referencias/` junto al repositorio del integrador (copias de proyectos de
    referencia), más las de ALFALFA_REFERENCIAS (separadas por «:»). Un agente
    esperó y perdió diez permisos de `grep`/`sed -n` sobre referencias/codex, que
    su propio encargo le mandaba contrastar.
    """
    candidatas = [c for c in os.environ.get('ALFALFA_REFERENCIAS', '').split(os.pathsep) if c]
    repo = _repo_del_integrador()
    if repo:
        candidatas.append(str(repo.parent / 'referencias'))
    carpetas = []
    for candidata in candidatas:
        real = os.path.realpath(os.path.expanduser(candidata))
        if os.path.isdir(real) and len(Path(real).parts) >= 4 and not sensible(real) \
                and not expone_sesiones(real) and real not in carpetas:
            carpetas.append(real)
    return carpetas


def _lectura_de_referencia(programa, argumentos, argumento, espacio, lecturas):
    """¿`argumento` es una ruta de lectura dentro de una carpeta de referencia?"""
    if programa not in LECTURA_DE_REFERENCIA or argumento.startswith('-') or '..' in Path(argumento).parts:
        return False
    if any(c in argumento for c in '*?[{'):
        # La shell de Codex o Claude expandiría `referencias/.*` también a `..`.
        return False
    if programa == 'sed' and not _sed_que_solo_imprime(argumentos):
        return False
    siguen = SIGUEN_ENLACES.get(programa, set())
    letra = LETRA_DE_ENLACES.get(programa)
    if any(a in siguen or (letra and re.fullmatch(r'-[A-Za-z]*' + letra + r'[A-Za-z]*', a)) for a in argumentos):
        return False
    ruta = os.path.expanduser(argumento)
    if not os.path.isabs(ruta):
        ruta = os.path.join(str(Path(espacio).resolve()), ruta)
    real = os.path.realpath(ruta)
    if sensible(real) or sensible(ruta):
        return False
    for carpeta in [*carpetas_de_referencia(), *(os.path.realpath(c) for c in lecturas)]:
        if real == carpeta or real.startswith(carpeta.rstrip('/') + '/'):
            return True
    return False


def programa_de_confianza(programa, espacio=None):
    """¿Se puede juzgar este programa por su nombre?

    Un nombre suelto (`git`) lo busca la shell en el PATH: vale. Con ruta, sólo
    si la carpeta es del sistema, está dentro del worktree del ticket o es el
    `.venv` del repositorio del integrador. Un programa que no existe tampoco ejecuta
    nada, así que no se le niega aquí.

    El `.venv` del integrador es el Python que él mismo manda usar para su suite
    (`cd server && <repo>/.venv/bin/python -m pytest -q`) y ningún agente puede
    escribir en él. Sin reconocerlo, cada revisión de un cambio al integrador pedía permiso
    para ejecutar las pruebas: 96 avisos en una semana, que caducaban sin
    respuesta y dejaban la revisión «sin verificar».
    """
    if '/' not in programa:
        return True
    if programa.startswith('~'):
        return False
    ruta = Path(programa)
    if _en_carpeta_del_sistema(programa):
        return True
    venv = _venv_del_integrador()
    if venv is not None and ruta.is_absolute() and Path(os.path.dirname(os.path.normpath(programa))) == venv:
        return True
    if not ruta.is_absolute() and espacio and '..' not in ruta.parts:
        # `.venv/bin/pytest` o `./node_modules/.bin/vitest`: dentro del worktree.
        return True
    if espacio:
        absoluto = Path(os.path.normpath(str(Path(espacio) / ruta)))
        for raiz in {Path(os.path.normpath(espacio)), Path(espacio).resolve()}:
            if raiz in absoluto.parents:
                return True
    return not os.path.lexists(programa if ruta.is_absolute() else str(Path(espacio or '.') / ruta))


def desenvolver(command):
    """Saca la orden real de dentro de `zsh -lc '...'`, si viene envuelta."""
    try:
        partes = shlex.split(command)
    except ValueError:
        return command
    if not partes or Path(partes[0]).name not in INTERPRETES:
        return command
    if '/' in partes[0] and not _en_carpeta_del_sistema(partes[0]):
        # `/tmp/x/zsh -lc 'git status'` no lo ejecuta la zsh del sistema.
        return command
    resto = partes[1:]
    banderas = [a for a in resto if a.startswith('-')]
    guion = [a for a in resto if not a.startswith('-')]
    if len(guion) != 1 or any(set(b[1:]) - set('lc') for b in banderas):
        return command
    return guion[0]


FILTROS = {'tail', 'head', 'grep', 'egrep', 'wc', 'sort', 'uniq', 'cut', 'xxd', 'od', 'hexdump'}

# Ver los bytes de lo que llega por la tubería (`tail -c 1 f | xxd`: ¿acaba en
# salto de línea?). xxd escribe en su segundo suelto y `-r` reconstruye bytes,
# así que sólo se admiten sus banderas y los números que siguen a las que llevan
# valor. Un agente esperó permiso por eso.
XXD_CON_VALOR = {'-c', '-cols', '-g', '-groupsize', '-l', '-len', '-n', '-name', '-o', '-s', '-seek'}


def _xxd_solo_muestra(argumentos):
    previo = None
    for a in argumentos:
        if a.startswith('-r') or a == '-revert':
            return False
        if not a.startswith('-') and not (previo in XXD_CON_VALOR and re.fullmatch(r'[+-]?\d+|0x[0-9a-fA-F]+', a)):
            return False
        previo = a
    return True


def _llaves_que_expanden(command, inicio):
    """¿Las llaves sin comillas que abren en `inicio` se expanden (`{a,b}`, `{1..3}`)?

    Se busca su cierre sin comillas; una coma o `..` sin comillas dentro basta.
    Sin cierre no se sabe qué hará la shell, así que también cuenta.
    """
    i, n, comilla, profundidad, anterior = inicio + 1, len(command), None, 1, ''
    while i < n:
        c = command[i]
        if comilla:
            if c == comilla:
                comilla = None
        elif c in "'\"":
            comilla = c
        elif c == '{':
            profundidad += 1
        elif c == '}':
            profundidad -= 1
            if not profundidad:
                return False
        elif c == ',' or (c == '.' and anterior == '.'):
            return True
        anterior = c if not comilla else ''
        i += 1
    return True


def _sustitucion_real(command):
    """¿Hay algo que la shell sustituiría o un escape que cambie el sentido?

    Entre comillas simples nada se interpreta; entre dobles sólo `$nombre`,
    `$(`, `${` y las comillas invertidas. Un patrón de grep como
    "\\.swift:[0-9]+" o "seconds$" es texto: el ticket de la ventana de
    una app de Mac esperó permiso para compilar y filtrar la salida así. Fuera de
    comillas sólo se acepta `\\ ` (un espacio de una ruta).
    """
    i, n, comilla = 0, len(command), None
    while i < n:
        c = command[i]
        siguiente = command[i + 1] if i + 1 < n else ''
        if c in '\n\r':
            return True
        if comilla == "'":
            if c == "'":
                comilla = None
        elif comilla == '"':
            if c == '"':
                comilla = None
            elif c == '`' or (c == '$' and (siguiente.isalnum() or siguiente in '_{(!?@*#-')):
                return True
            elif c == '\\':
                i += 1  # escapa el siguiente carácter, que queda literal
        else:
            if c in "'\"":
                comilla = c
            elif c in '`$':
                return True
            elif c == '{' and _llaves_que_expanden(command, i):
                # `cat {~/.ssh/id_rsa,x}`: la shell expande las llaves antes de
                # ejecutar, y la ruta de dentro ya no parece una ruta.
                return True
            elif c == '\\':
                if siguiente != ' ':
                    return True
                i += 1
        i += 1
    return False


def _salida_a_evidencia(absoluto, espacio):
    """Un archivo (no la carpeta) bajo evidence/ o evidencia/ del worktree.

    Guardar ahí la salida de las pruebas es dejar constancia: un agente esperó
    permiso por `make check > evidence/1767b922cd-check-final.txt 2>&1`.
    """
    if any(c in absoluto for c in '*?$`'):
        return False
    raiz = Path(espacio).resolve()
    destino = Path(absoluto).resolve()
    if raiz not in destino.parents:
        return False
    partes = destino.relative_to(raiz).parts
    return any(p in ('evidence', 'evidencia') and len(partes) - i >= 2 for i, p in enumerate(partes))


def partir_en_y(command, espacio=None, directorio=None, escritura=True):
    """Trocea `a && b && c` respetando comillas, o None si hay algo más.

    `&&` es lo único que se trocea, y sólo porque no cambia lo que hace cada
    trozo: es ejecución secuencial condicionada, sin tuberías ni redirecciones
    ni sustituciones. Cada trozo se somete después al mismo criterio entero, así
    que trocear no concede nada que no se concediera por separado. Todo lo demás
    (`;`, `|`, `if`, `$(...)`) sigue sin analizarse.
    """
    lector = shlex.shlex(command, posix=True, punctuation_chars=True)
    lector.whitespace_split = True
    try:
        piezas = list(lector)
    except ValueError:
        return None
    # `2>&1` sólo junta errores y salida; no escribe en ningún archivo.
    limpias = []
    i = 0
    while i < len(piezas):
        if piezas[i:i + 3] == ['2', '>&', '1']:
            i += 3
            continue
        limpias.append(piezas[i])
        i += 1
    piezas = limpias
    # Tramos separados por `&&`; cada tramo es una tubería cuya primera orden
    # se analiza y el resto deben ser filtros puros que sólo leen la tubería
    # («npm test 2>&1 | grep -E "not ok" && git diff --check»). Antes sólo se
    # aceptaba la tubería al final, y ese patrón habitual acababa pidiéndote
    # permiso para correr las pruebas.
    tramos, tuberia, actual = [], [], []
    # `;` encadena igual que `&&` (sólo que sigue aunque falle la anterior):
    # cada orden se juzga entera por separado.
    # Con palabras de control (if, for, while, case…) no es una lista de órdenes
    # simples: eso sigue sin analizarse.
    if ';' in piezas and {'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done',
                          'case', 'esac', 'function', '{', '}'} & set(piezas):
        return None
    piezas = ['&&' if p == ';' else p for p in piezas]
    # `> registro.log` o `>> registro.log` hacia una carpeta temporal del
    # worktree sólo guarda la salida: un agente esperó permiso por
    # `xcodebuild … > ../.tmp-alfalfa/run5.log 2>&1`. Cualquier otro destino sigue
    # sin analizarse.
    if espacio:
        referencia = directorio or espacio
        limpias, i = [], 0
        while i < len(piezas):
            if piezas[i] in ('>', '>>') and i + 1 < len(piezas):
                destino = piezas[i + 1]
                absoluto = destino if os.path.isabs(destino) else os.path.normpath(os.path.join(referencia, destino))
                if not (_bajo_cache(absoluto, espacio)
                        or escritura and _salida_a_evidencia(absoluto, espacio)):
                    return None
                if limpias and limpias[-1] in ('1', '2'):
                    limpias.pop()
                i += 2
                continue
            limpias.append(piezas[i])
            i += 1
        piezas = limpias
    for pieza in piezas:
        if pieza in ('|', '&&'):
            if not actual:
                return None
            tuberia.append(actual)
            actual = []
            if pieza == '&&':
                tramos.append(tuberia)
                tuberia = []
        elif pieza and set(pieza) <= set('&;|<>()'):
            # Sólo un token hecho ENTERO de puntuación es un operador. shlex ya
            # los separa, así que mirar si un token "contiene" uno rechazaba
            # argumentos legítimos: el patrón con el que un agente busca los
            # marcadores de conflicto, '<<<<<<<|=======|>>>>>>>', es texto
            # entrecomillado, no una tubería.
            return None
        else:
            actual.append(pieza)
    if not actual:
        return None
    tuberia.append(actual)
    tramos.append(tuberia)
    trozos = []
    for tuberia in tramos:
        for filtro in tuberia[1:]:
            if Path(filtro[0]).name not in FILTROS or (
                    '/' in filtro[0] and not _en_carpeta_del_sistema(filtro[0])):
                return None
            if Path(filtro[0]).name == 'xxd' and not _xxd_solo_muestra(filtro[1:]):
                return None
            argumentos = [a for a in filtro[1:] if not a.startswith('-')]
            # El primer suelto de grep es el patrón, no un archivo: el diseñador
            # de una app de Mac esperó permiso por `xcodebuild … | grep -E
            # "warning: .*Sources/DoctorApp"`. Con -e o -f no hay patrón suelto.
            # (Arreglo propuesto por el doctor.)
            if Path(filtro[0]).name in ('grep', 'egrep') and argumentos and not any(
                    re.match(r'-[A-Za-z]*[ef]', a) or a.startswith(('--regexp', '--file'))
                    for a in filtro[1:]):
                argumentos = argumentos[1:]
            if any('/' in a for a in argumentos):
                return None  # un filtro con archivos ya no lee sólo la tubería
        trozos.append(tuberia[0])
    # `! orden` sólo invierte el código de salida; la orden negada se somete
    # después al mismo criterio, así que la negación no concede nada.
    return [t[1:] if t[0] == '!' and len(t) > 1 else t for t in trozos if t]


def safe_command(command, espacio, escritura=True, directorio=None, merge_base=None, citadas=()):
    """(¿aprobable?, motivo). El motivo se registra siempre, apruebe o no.

    `citadas`: rutas absolutas del requerimiento del ticket (`rutas_citadas`). Sus
    carpetas, como `referencias/`, se pueden leer con órdenes que no escriben.

    Con `escritura=False` se niegan además los subcomandos de git que escriben:
    es lo que se aplica a un revisor, que puede compilar y probar pero no tocar
    la rama sobre la que va a dar un veredicto.
    """
    if isinstance(command, (list, tuple)):
        command = ' '.join(str(parte) for parte in command)
    if not isinstance(command, str) or not command.strip():
        return False, 'sin orden que analizar'
    if not espacio:
        return False, 'el ticket no tiene worktree propio'
    command = desenvolver(command)
    # La sustitución y el escape sí se miran sobre el texto crudo: al quitar las
    # comillas se pierde la diferencia entre '$HOME' literal y $HOME expandido,
    # y esa diferencia es justo la que importa.
    # «\ » sólo escapa un espacio de una ruta (Application\ Support): Claude lo
    # escribe así al entrar en el worktree y el ticket base de una app de Mac quedó
    # bloqueado esperando permiso para borrar su .tmp-alfalfa. shlex lo entiende.
    if _sustitucion_real(command):
        return False, 'sustituye o escapa: no se analiza entera'
    trozos = partir_en_y(command, espacio, directorio, escritura)
    if trozos is None:
        return False, 'encadena, redirige o sustituye órdenes'
    # Las mismas salvaguardas que una lectura de Claude: nada sensible ni la casa entera.
    lecturas = [c for c in dict.fromkeys(carpeta_de_lectura(c, [c]) for c in citadas or ()) if c]
    motivos = []
    base = Path(espacio).resolve()
    if directorio:
        # La orden corre en una subcarpeta del worktree (Codex manda su cwd):
        # `../.tmp-alfalfa` desde ahí sigue dentro.
        inicio = Path(directorio).resolve()
        if inicio == base or base in inicio.parents:
            base = inicio
    for partes in trozos:
        if base != Path(espacio).resolve() and partes and Path(partes[0]).name != 'cd':
            # Tras `cd sub`, las rutas relativas son de `sub`, no de la raíz.
            # Las variables del principio (`TMPDIR=… GIT_CEILING_DIRECTORIES=… python …`)
            # no son rutas: reescrita, la segunda pasaba por un programa compilado
            # y un revisor dio 35 vueltas sin poder correr pytest.
            inicio = 0
            while inicio < len(partes) - 1 and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', partes[inicio]):
                inicio += 1
            programa = partes[inicio]
            if '/' in programa and not programa.startswith(('/', '~')):
                # `cd sub && ../../x/pytest`: el programa también es relativo a `sub`.
                programa = os.path.normpath(str(base / programa))
            # El subcomando de una herramienta no es una ruta: con una carpeta
            # demo/test, `cd demo && npm test` se convertía en `npm /…/demo/test` y
            # esperó permiso diez minutos en cada intento.
            subcomando = next((n for n, a in enumerate(partes[inicio + 1:], inicio + 1)
                               if not a.startswith('-')), None) \
                if Path(programa).name in HERRAMIENTAS else None
            partes = partes[:inicio] + [programa] + [
                a if n == subcomando or a.startswith('-') or Path(a).is_absolute() or '/' not in a
                and not (base / a).exists() and not any(c in a for c in '*?[')
                else os.path.normpath(str(base / a)) for n, a in enumerate(partes[inicio + 1:], inicio + 1)]
        aprobable, motivo = _analizar(partes, espacio, escritura, merge_base=merge_base, lecturas=lecturas)
        if not aprobable:
            return False, motivo
        if Path(partes[0]).name == 'cd' and len(partes) == 2:
            destino = Path(partes[1])
            base = (base / destino).resolve() if not destino.is_absolute() else destino.resolve()
        motivos.append(motivo)
    return True, ' y además '.join(dict.fromkeys(motivos))


def _bajo_cache(argumento, espacio):
    """¿La ruta cae dentro de una caché o carpeta temporal del worktree (.tmp-alfalfa/…)?"""
    if not argumento or argumento.startswith(('-', '~')) or '*' in argumento or '..' in Path(argumento).parts:
        return False
    raiz = Path(espacio).resolve()
    destino = Path(argumento)
    resuelto = (raiz / destino).resolve() if not destino.is_absolute() else destino.resolve()
    if resuelto == raiz or raiz not in resuelto.parents:
        return False
    return any(p in CACHES or p.startswith(PREFIJOS_DE_CACHE) for p in resuelto.relative_to(raiz).parts)


def _copia_de_referencia(programa, argumentos, espacio, escritura):
    """`git archive HEAD` y `tar -x` hacia .tmp-alfalfa: una copia del «antes».

    El rediseño de una app de iPhone compila la versión anterior en
    .tmp-alfalfa/baseline para sacar las capturas de «antes» y esperó permiso.
    """
    if programa == 'git' and argumentos[:1] == ['archive']:
        salida = next((a.split('=', 1)[1] for a in argumentos if a.startswith('--output=')), None)
        if salida is None and '-o' in argumentos and argumentos.index('-o') + 1 < len(argumentos):
            salida = argumentos[argumentos.index('-o') + 1]
        # También un revisor: arma en .tmp-alfalfa la versión «antes» para comparar
        # y eso no toca la rama que juzga (uno esperó por esto).
        if salida and _bajo_cache(salida, espacio):
            return True, 'copia el commit actual a una carpeta temporal del worktree'
        return False, 'git archive sólo hacia una carpeta temporal del worktree'
    if programa != 'tar':
        return None
    banderas = ''.join(a.lstrip('-') for a in argumentos if a.startswith('-') and not a.startswith('--'))
    rutas = [a for a in argumentos if not a.startswith('-')]
    if 't' in banderas and 'x' not in banderas and 'c' not in banderas:
        if all(not _fuera_del_espacio(r, espacio) for r in rutas):
            return True, 'tar sólo lista'
    if 'x' in banderas and '-C' in argumentos and argumentos.index('-C') + 1 < len(argumentos):
        destino = argumentos[argumentos.index('-C') + 1]
        if _bajo_cache(destino, espacio) and all(_bajo_cache(r, espacio) for r in rutas):
            return True, 'tar extrae una copia dentro de una carpeta temporal del worktree'
    return False, 'tar sólo puede extraer de y hacia una carpeta temporal del worktree'


def _extraccion_a_cache(programa, argumentos, espacio):
    """`unzip` para mirar un zip o sacar su contenido a una salida regenerable.

    Un revisor sacaba el último fotograma de la traza de Playwright a
    test-results como evidencia, y cada vez quedaba esperando a una persona.
    Listar (-l) o volcar (-p) no escribe; extraer sólo se admite hacia una
    caché dentro del worktree, que no cuenta como cambio.
    """
    if programa != 'unzip':
        return None
    if '-l' in argumentos or '-p' in argumentos or '-t' in argumentos:
        return True, 'unzip sólo lee'
    if '-d' in argumentos:
        i = argumentos.index('-d')
        destino = argumentos[i + 1] if i + 1 < len(argumentos) else ''
        if _es_cache(destino, espacio) or any(p in CACHES for p in Path(destino).parts):
            return True, 'unzip extrae a una salida regenerable del worktree'
    return False, 'unzip sólo puede extraer a una caché del worktree'


# Variables que cambian qué código se ejecuta: nunca se conceden solas.
VARIABLES_PELIGROSAS = {'PATH', 'NODE_OPTIONS', 'BASH_ENV', 'ENV', 'ZDOTDIR', 'PROMPT_COMMAND',
                        'GIT_SSH_COMMAND', 'GIT_SSH', 'GIT_EXEC_PATH', 'GIT_CONFIG', 'GIT_CONFIG_GLOBAL',
                        'PYTHONSTARTUP', 'PERL5OPT', 'PERL5LIB', 'RUBYOPT', 'EDITOR', 'VISUAL', 'SHELL'}
PREFIJOS_PELIGROSOS = ('DYLD_', 'LD_', 'GIT_CONFIG_')
TEMPORALES = ('/tmp/', '/private/tmp/', '/var/folders/', '/private/var/folders/')
MODULOS_DE_PRUEBA = {'pytest': 'pytest', 'unittest': 'pytest', 'mypy': 'mypy', 'ruff': 'ruff'}


def _quitar_entorno(partes, espacio):
    """Separa `env A=1 B=2 orden` o `A=1 orden`: (orden, motivo_de_rechazo).

    Un revisor lanzaba la prueba de la reparación con
    `env PROJECTS_ROOT=/tmp/… PYTHONPATH=server python3 -m pytest` y
    se paraba sólo por la forma. Se admite fijar variables cuyo valor
    queda dentro del worktree o en una carpeta temporal; las que deciden qué
    código se ejecuta (PATH, NODE_OPTIONS, DYLD_*…) siguen preguntándose.
    """
    resto = list(partes)
    if resto and Path(resto[0]).name == 'env':
        resto = resto[1:]
        # `env -u VAR` sólo quita una variable (el revisor aísla así la prueba
        # de la configuración real); las demás opciones siguen preguntándose.
        while resto and resto[0].startswith('-'):
            if resto[0] in ('-u', '--unset') and len(resto) > 1:
                resto = resto[2:]
            elif resto[0].startswith('--unset=') or (resto[0].startswith('-u') and len(resto[0]) > 2):
                resto = resto[1:]
            else:
                return None, 'env con opciones'
    while resto and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', resto[0]):
        nombre, valor = resto[0].split('=', 1)
        if nombre in VARIABLES_PELIGROSAS or nombre.startswith(PREFIJOS_PELIGROSOS):
            return None, 'fija ' + nombre + ', que cambia qué se ejecuta'
        for trozo in valor.split(':'):
            if trozo.startswith('~') or (trozo.startswith('/') and not trozo.startswith(TEMPORALES)
                                         and _fuera_del_espacio(trozo, espacio)):
                return None, 'una variable apunta fuera del worktree: ' + nombre
        resto = resto[1:]
    return resto, None


BANDERAS_DE_LECTURA = {'-s', '-S', '-sS', '-i', '-I', '-L', '-sL', '-sSL', '-fsSL', '-f', '--silent', '--show-error',
                       '--include', '--head', '--location', '--fail'}


def _leer_robots(programa, argumentos):
    """`curl https://sitio/robots.txt`: leer las reglas públicas de un sitio.

    El revisor de un recolector tiene que comprobar que respeta el robots.txt,
    y se quedaba esperando a una persona para leerlo. Sólo esa ruta, sólo GET
    y sin guardar nada: cualquier otra bandera o dirección sigue preguntándose.
    """
    if programa != 'curl':
        return None
    urls, i = [], 0
    while i < len(argumentos):
        a = argumentos[i]
        if a in ('--max-time', '-m', '--connect-timeout') and i + 1 < len(argumentos):
            i += 2
            continue
        if a.startswith('-'):
            if a not in BANDERAS_DE_LECTURA:
                return None
        else:
            urls.append(a)
        i += 1
    from urllib.parse import urlparse
    if len(urls) != 1:
        return None
    url = urlparse(urls[0])
    if url.scheme not in ('http', 'https') or not url.hostname or url.path != '/robots.txt' or url.query:
        return None
    return True, 'lee el robots.txt público de ' + url.hostname


NAVEGADORES = {'Google Chrome', 'Chromium', 'chromium', 'google-chrome', 'Microsoft Edge'}
# Opciones que sólo cambian cómo se pinta la página; las que guardan algo van aparte.
OPCIONES_DE_PINTADO = {'--headless', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                       '--run-all-compositor-stages-before-draw', '--disable-extensions',
                       '--no-first-run', '--no-default-browser-check', '--mute-audio'}
OPCIONES_CON_VALOR = ('--headless=', '--window-size=', '--virtual-time-budget=',
                      '--force-device-scale-factor=', '--default-background-color=')
OPCIONES_DE_SALIDA = ('--screenshot=', '--print-to-pdf=', '--user-data-dir=')


def _captura_sin_ventana(programa, argumentos, espacio, escritura=True):
    """Chrome sin ventana haciendo una captura de una página del propio proyecto.

    Un revisor de una web quiso mirar cómo se veía el diseño y
    se quedó esperando a una persona. Se concede sólo lo que no sale del
    worktree: la página es un archivo del proyecto o un servidor local, y la
    captura (y el perfil, si lo pide) van a una carpeta temporal. Un perfil
    tuyo, depuración remota o extensiones siguen necesitando tu permiso.
    """
    if programa not in NAVEGADORES:
        return None
    if not any(a == '--headless' or a.startswith('--headless=') for a in argumentos):
        return False, 'abre el navegador con ventana'
    paginas = []
    for a in argumentos:
        if a in OPCIONES_DE_PINTADO or a.startswith(OPCIONES_CON_VALOR):
            continue
        if a.startswith(OPCIONES_DE_SALIDA):
            valor = a.split('=', 1)[1] if '=' in a else ''
            # La captura misma puede ir a la carpeta de diseño del proyecto: un
            # agente guardaba design/maquetas/…/captura-1440.png y esperaba permiso.
            # Un revisor no escribe en la rama que juzga: él, sólo a temporales.
            if escritura and a.startswith(('--screenshot', '--print-to-pdf')) and valor \
                    and Path(valor).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.pdf') \
                    and not _fuera_del_espacio(valor if '/' in valor else './' + valor, espacio) \
                    and '..' not in Path(valor).parts:
                continue
            if not _es_cache(valor, espacio):
                return False, 'el navegador guardaría fuera de una carpeta temporal: ' + a
            continue
        if a.startswith('-'):
            return False, 'el navegador con una opción no reconocida: ' + a
        paginas.append(a)
    if len(paginas) != 1:
        return False, 'el navegador debe abrir exactamente una página'
    from urllib.parse import unquote, urlparse
    url = urlparse(paginas[0])
    if url.scheme == 'file':
        ruta = Path(unquote(url.path)).resolve()
        raiz = Path(espacio).resolve()
        if ruta != raiz and raiz not in ruta.parents:
            return False, 'el navegador abriría un archivo fuera del worktree'
    elif not (url.scheme in ('http', 'https') and url.hostname in ('localhost', '127.0.0.1')):
        return False, 'el navegador abriría una página que no es del proyecto'
    return True, 'captura sin ventana de una página del propio proyecto'


def _ejecutable_construido(partes, espacio):
    """Ejecutar el programa que el propio ticket acaba de compilar.

    El revisor de una app de Mac esperó permiso por
    `.tmp-alfalfa/review-derived/Build/Products/Debug/doctor --version`:
    es el código del proyecto, igual que correr sus pruebas. Sólo vale un
    binario dentro de una salida de compilación del worktree.
    """
    programa = partes[0]
    if '/' not in programa:
        return None
    raiz = Path(espacio).resolve()
    destino = Path(programa)
    resuelto = (raiz / destino).resolve() if not destino.is_absolute() else destino.resolve()
    if resuelto == raiz or raiz not in resuelto.parents or '..' in destino.parts:
        return None
    relativas = resuelto.relative_to(raiz).parts
    salida = ('.build', 'DerivedData', 'Products', 'build')
    if not (_bajo_cache(programa, espacio) or any(p in salida for p in relativas[:-1])):
        return None
    if any(_fuera_del_espacio(a, espacio) for a in partes[1:]):
        return False, 'el programa compilado apunta fuera del worktree'
    return True, 'ejecuta el programa que el proyecto acaba de compilar, dentro del worktree'


BLENDER = {'Blender', 'blender'}
OPCIONES_DE_BLENDER = {'--factory-startup', '-noaudio', '--disable-autoexec', '-Y'}


def _blender_sin_ventana(partes, espacio):
    """Blender sin ventana ejecutando un script del propio worktree.

    Los escenarios 3D de un juego se generan con `Blender -b -P
    tools/escenarios3d/generar.py -- manifiesto`. Dentro del sandbox de Codex
    Blender se cae (código 139) y el permiso para salir caducaba sin respuesta:
    un agente no pudo renderizar un escenario en tres rondas y el revisor tampoco
    comprobarlo. Es el código del proyecto,
    igual que correr sus pruebas: sólo se concede con -b, un único script
    dentro del worktree y argumentos que no salen de él. Un .blend ajeno,
    otro script u otra opción siguen preguntándose.
    """
    if Path(partes[0]).name not in BLENDER:
        return None
    if '--' in partes:
        corte = partes.index('--')
        propias, del_script = partes[1:corte], partes[corte + 1:]
    else:
        propias, del_script = partes[1:], []
    fondo, scripts, i = False, [], 0
    while i < len(propias):
        a = propias[i]
        if a in ('-b', '--background'):
            fondo = True
        elif a in ('-P', '--python') and i + 1 < len(propias):
            scripts.append(propias[i + 1])
            i += 1
        elif a == '--python-exit-code' and i + 1 < len(propias) and propias[i + 1].isdigit():
            i += 1
        elif a not in OPCIONES_DE_BLENDER:
            return False, 'Blender con una opción no reconocida: ' + a
        i += 1
    if not fondo:
        return False, 'abre Blender con ventana'
    if len(scripts) != 1:
        return False, 'Blender debe ejecutar exactamente un script del proyecto'
    raiz = Path(espacio).resolve()
    script = Path(scripts[0]) if Path(scripts[0]).is_absolute() else Path(espacio) / scripts[0]
    if raiz not in script.resolve().parents or '..' in Path(scripts[0]).parts:
        return False, 'Blender ejecutaría un script fuera del worktree'
    if any(_fuera_del_espacio(a, espacio) and not _es_cache(a, espacio) for a in del_script):
        return False, 'el script de Blender apunta fuera del worktree'
    return True, 'Blender sin ventana ejecuta un script del propio worktree'


SEÑALES_DE_CIERRE = {'-TERM', '-INT', '-KILL', '-HUP', '-QUIT', '-9', '-15', '-2', '-1', '-3',
                     '-s', 'TERM', 'INT', 'KILL', 'HUP', 'QUIT'}


def _directorio_de(pid):
    """Carpeta actual de un proceso (lsof), o None."""
    import subprocess
    try:
        salida = subprocess.run(['lsof', '-a', '-d', 'cwd', '-p', str(pid), '-Fn'],
                                capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((linea[1:] for linea in salida.splitlines() if linea.startswith('n')), None)


def _ejecutable_de(pid):
    """Ruta del ejecutable de un proceso (ps), o None."""
    import subprocess
    try:
        salida = subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)],
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return salida or None


# Diagnóstico de memoria y CPU sobre la app que el propio agente compiló: un
# agente de QA esperó permiso por `heap --forkCorpse -s <pid>` y por `sample`
# mientras buscaba los 6,5 GB de una app de Mac. Sólo leen el proceso; las opciones que
# escriben archivos (-file, -o) no se conceden por aquí.
DIAGNOSTICO = {'heap', 'vmmap', 'sample', 'leaks', 'footprint'}
DIAGNOSTICO_ESCRIBE = {'-file', '-f', '-o', '--outputGraph', '-outputGraph', '-e', '--exec'}


def _diagnostico(programa, argumentos, espacio):
    if programa not in DIAGNOSTICO:
        return None
    if DIAGNOSTICO_ESCRIBE & set(argumentos) or any(a.startswith(('-file=', '--outputGraph=')) for a in argumentos):
        return False, programa + ' con una opción que escribe archivos'
    pids = [a for a in argumentos if a.isdigit()]
    if not pids or not espacio:
        return False, programa + ' sin un proceso del worktree'
    raiz = Path(espacio).resolve()
    for pid in pids[:1]:
        ejecutable = _ejecutable_de(pid)
        if not ejecutable or not ejecutable.startswith('/'):
            return False, programa + ' de un proceso que no puedo ubicar: ' + pid
        destino = Path(ejecutable).resolve()
        if raiz not in destino.parents:
            return False, programa + ' de un proceso que no es del worktree: ' + pid
    return True, programa + ' mide la app que el agente compiló en su worktree'


# Opciones con las que un agente arranca su VM de ensayo (con topes de CPU,
# memoria y disco). Nada
# que monte carpetas del Mac (--mount sólo «none»), cambie la red o la active como
# contexto Docker por defecto.
COLIMA_OPCIONES = {'--vm-type': 'vz', '--vz-rosetta': None, '--cpu': 2, '--cpus': 2, '--memory': 2,
                   '--disk': 40, '--runtime': ('docker', 'containerd'), '--arch': ('aarch64', 'x86_64'),
                   '--activate=false': None, '--save-config': None, '--mount': ('none',),
                   '--ssh-config=false': None, '--root-disk': 40}


def _vm_de_ensayo(argumentos):
    """`colima start/stop` de un perfil propio (nunca «default») con opciones de recursos."""
    perfil = None
    i = 0
    while i < len(argumentos):
        a = argumentos[i]
        if a in ('--profile', '-p') and i + 1 < len(argumentos):
            perfil = argumentos[i + 1]
            i += 2
            continue
        if a.startswith('--profile='):
            perfil = a.split('=', 1)[1]
            i += 1
            continue
        if not a.startswith('-') and perfil is None:
            perfil = a  # `colima start vm-de-ensayo`
            i += 1
            continue
        nombre, _, valor = a.partition('=')
        clave = a if a in COLIMA_OPCIONES else nombre
        if clave not in COLIMA_OPCIONES:
            return False
        esperado = COLIMA_OPCIONES[clave]
        if esperado is None:
            i += 1
            continue
        if not valor:
            if i + 1 >= len(argumentos):
                return False
            valor = argumentos[i + 1]
            i += 2
        else:
            i += 1
        # Un número es un tope: una VM de ensayo no debe comerse el Mac.
        if isinstance(esperado, int) and not (valor.isdigit() and int(valor) <= esperado):
            return False
        if isinstance(esperado, tuple) and valor not in esperado:
            return False
        if esperado == 'vz' and valor != 'vz':
            return False
    return bool(perfil) and perfil != 'default'


def _orden_de(pid):
    try:
        return subprocess.run(['ps', '-o', 'command=', '-p', str(int(pid))], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ''


def _ssh_de_vm_de_ensayo(pid):
    """El SSH maestro que deja Colima de una VM propia (no «default») ya parada.

    La limpieza de un ensayo quedó dando vueltas once horas porque
    `kill` de ese proceso preguntaba y nadie respondía. Sólo es la conexión a
    la VM de ensayo: cerrarlo no toca la VM del usuario ni nada más.
    """
    orden = _orden_de(pid)
    encontrado = re.match(r'^ssh: \S*/\.colima/_lima/colima-([A-Za-z0-9._-]+)/ssh\.sock \[mux\]$', orden)
    return bool(encontrado) and encontrado.group(1) not in ('default', '')


def _kill(argumentos, espacio):
    """Cerrar procesos propios: sólo si cada pid trabaja dentro del worktree.

    A un agente se le colgó un `swift test` y otro dejó un python suyo en una
    app de Mac: los dos pidieron permiso para `kill -TERM <pid>`. Un pid
    de fuera (una app del usuario, el propio arnés) sigue preguntando.
    """
    pids = [a for a in argumentos if a.isdigit()]
    otros = [a for a in argumentos if not a.isdigit() and a not in SEÑALES_DE_CIERRE]
    if not pids or otros or not espacio:
        return False, 'kill con opciones o destinos que no reconozco'
    raiz = Path(espacio).resolve()
    for pid in pids:
        if _ssh_de_vm_de_ensayo(pid):
            continue
        carpeta = _directorio_de(pid)
        if not carpeta:
            return False, 'kill de un proceso que no puedo ubicar: ' + pid
        destino = Path(carpeta).resolve()
        if destino != raiz and raiz not in destino.parents:
            return False, 'kill de un proceso que no trabaja en este worktree: ' + pid
    return True, 'cierra procesos que el propio agente lanzó en su worktree'


def _ps(argumentos):
    """`ps` sólo lista procesos, salvo -e/-E o `e`, que en macOS enseñan el
    entorno de cada proceso (y con él el token del forge, como `env`).

    Un agente esperó permiso por `ps -axo pid,ppid,comm` al revisar el cierre
    de las apps de prueba de una app de Mac.
    """
    formato = False
    for a in argumentos:
        if formato:
            formato = False
            continue
        letras = a[1:] if a.startswith('-') else a
        if 'e' in letras or 'E' in letras:
            return False, 'ps que muestra el entorno de los procesos'
        formato = a.startswith('-') and letras[-1:] in ('o', 'O', 'p', 'U', 'u', 't', 'g', 'G')
    return True, 'ps sólo lista procesos'


def _programa_ajeno(programa):
    return ('el programa ' + programa + ' no es el ' + Path(programa).name
            + ' del sistema ni uno del worktree')


PYTHON_DEL_SISTEMA = ('/opt/homebrew/', '/usr/bin/', '/usr/local/', '/Library/Frameworks/Python.framework/')


def _analizar(partes, espacio, escritura, merge_base=None, lecturas=()):
    """Juzga una sola orden ya troceada en argumentos."""
    if not partes:
        return False, 'sin orden que analizar'
    if not programa_de_confianza(partes[0], espacio):
        return False, _programa_ajeno(partes[0])
    partes, rechazo = _quitar_entorno(partes, espacio)
    if rechazo:
        return False, rechazo
    if not partes:
        return False, 'sólo fija variables, sin orden'
    if partes[0].startswith('~') or any(c in partes[0] for c in '*?[{'):
        # La shell expande comodines y `~` en el programa antes de ejecutarlo:
        # `/bin/s? -c id` es /bin/sh y `~/x/cat` no es el cat del sistema.
        return False, 'el programa ' + partes[0] + ' lleva comodines o ~ que la shell expande'
    if not programa_de_confianza(partes[0], espacio):
        return False, _programa_ajeno(partes[0])
    if len(partes) == 2 and partes[1] == '--version' \
            and (('/' not in partes[0] and shutil.which(partes[0])) or
                 (Path(partes[0]).is_absolute() and _fuera_del_espacio(partes[0], espacio)
                  and Path(partes[0]).is_file() and os.access(partes[0], os.X_OK))):
        # Un programa ya instalado (no uno del worktree) que sólo dice su versión:
        # un agente esperó permiso por `/opt/homebrew/bin/opencode --version`.
        return True, Path(partes[0]).name + ' sólo dice su versión'
    if partes[:3] == ['swift', 'package', 'clean'] and len(partes) == 3:
        # Borra .build del propio worktree, que se regenera: un agente de backend esperó por esto.
        return True, 'swift package clean sólo borra la compilación del worktree'
    # `python -m pytest` es lo mismo que `pytest`; otros módulos no se conceden.
    # -B/-u delante no cambian nada (el revisor de una app de iPhone esperó por
    # `python3 -B -m unittest discover`).
    if Path(partes[0]).name.startswith('python'):
        while len(partes) > 1 and partes[1] in OPCIONES_INOFENSIVAS_DE_PYTHON:
            partes = partes[:1] + partes[2:]
    if (Path(partes[0]).name.startswith('python') and len(partes) >= 3 and partes[1] == '-m'
            and partes[2] in MODULOS_DE_PRUEBA):
        partes = [MODULOS_DE_PRUEBA[partes[2]]] + partes[3:]
    if Path(partes[0]).name in INTERPRETES and len(partes) >= 2 and partes[1].endswith('.sh') \
            and not partes[1].startswith('-'):
        # `bash scripts/prueba.sh`: un guion del propio worktree, como `python3 x.py`.
        # Sin -c ni guiones de fuera (el revisor de una app de Mac esperó por esto).
        guion = Path(partes[1]) if Path(partes[1]).is_absolute() else Path(espacio) / partes[1]
        raiz = Path(espacio).resolve()
        if raiz in guion.resolve().parents and not any(_fuera_del_espacio(a, espacio) for a in partes[2:]):
            return True, 'ejecuta un guion del propio worktree'
    if Path(partes[0]).name in INTERPRETES:
        return False, 'lanza otro intérprete de órdenes'
    construido = _ejecutable_construido(partes, espacio)
    if construido is not None:
        return construido
    blender = _blender_sin_ventana(partes, espacio)
    if blender is not None:
        return blender
    if partes[0].startswith('/') and not Path(partes[0]).exists():
        # Antes se concedía («fallará sin hacer nada»), pero no existir aquí no
        # es no existir al ejecutarse: `/bin/[s]h -c …` la shell lo expande a
        # /bin/sh, y un trozo anterior de `a && b` puede crear el programa.
        return False, 'el programa ' + partes[0] + ' no existe'
    programa = Path(partes[0]).name
    argumentos = partes[1:]
    if programa == 'time' and partes[0] in ('time', '/usr/bin/time'):
        # `/usr/bin/time -p go test …`: sólo mide; vale lo que valga la orden medida.
        # Un agente esperó permiso por eso al verificar el arreglo de failover.test.
        resto = [a for a in argumentos if a not in ('-p', '-l')]
        if not resto or resto[0].startswith('-'):
            return False, 'time sin orden que medir'
        veredicto, motivo = _analizar(resto, espacio, escritura, merge_base=merge_base, lecturas=lecturas)
        return veredicto, ('mide ' if veredicto else '') + motivo
    if programa == 'caffeinate' and partes[0] in ('caffeinate', '/usr/bin/caffeinate'):
        # `caffeinate -i python3 ci/…`: sólo impide que el Mac se duerma o frene
        # la prueba; vale lo que valga la orden. Un agente se quedó sin su corrida
        # de memoria porque se le negó. Sin orden (-w, -t solos) no se concede.
        resto = list(argumentos)
        while resto and re.fullmatch(r'-[dimsu]+', resto[0]):
            resto.pop(0)
        if not resto or resto[0].startswith('-'):
            return False, 'caffeinate sin orden que envolver'
        veredicto, motivo = _analizar(resto, espacio, escritura, merge_base=merge_base, lecturas=lecturas)
        return veredicto, ('sin dormir el Mac, ' if veredicto else '') + motivo
    diagnostico = _diagnostico(programa, argumentos, espacio)
    if diagnostico is not None:
        return diagnostico
    if programa == 'ps':
        return _ps(argumentos)
    if programa == 'colima' and argumentos[:1] in (['status'], ['list'], ['ls'], ['version']) \
            and all(a in ('--json', '-j', '--extended', '-e') or a.startswith(('--profile=', '-p='))
                    or (i > 0 and argumentos[i - 1] in ('--profile', '-p')) or a in ('--profile', '-p')
                    for i, a in enumerate(argumentos[1:], 1)):
        # Un agente esperó permiso por `colima status --profile vm-de-ensayo --json`
        # en un ensayo en Linux: status/list sólo leen el estado de las VM.
        return True, 'colima sólo consulta el estado de sus máquinas virtuales'
    if programa == 'colima' and argumentos[:1] in (['start'], ['stop']) and _vm_de_ensayo(argumentos[1:]):
        # Arrancar o parar una VM de ensayo ya creada (un agente esperó por `colima
        # start --profile vm-de-ensayo` al medir memoria). Sin opciones no cambia su
        # configuración; crearla o borrarla, o tocar la VM «default» del usuario, sigue preguntando.
        return True, 'arranca o para una máquina virtual de ensayo propia'
    if programa == 'openssl' and argumentos[:1] in (['verify'], ['x509'], ['pkey']):
        # El revisor de una guía esperó por `openssl verify` y por sacar la
        # clave pública de certificados de ensayo para compararlas. Leer y verificar no
        # cambia nada; lo que escriba (-out) tiene que quedar dentro del worktree, y de
        # una clave privada sólo puede salir la pública (-pubout).
        if any(_fuera_del_espacio(a, espacio) for a in argumentos[1:]):
            return False, 'openssl con rutas fuera del worktree'
        if argumentos[0] == 'pkey' and '-pubout' not in argumentos:
            return False, 'openssl pkey que podría copiar una clave privada'
        if argumentos[0] == 'x509' and '-out' in argumentos and '-noout' not in argumentos \
                and '-pubkey' not in argumentos:
            return False, 'openssl x509 que reescribe un certificado'
        if any(a in ('-passin', '-passout', '-engine') for a in argumentos):
            return False, 'openssl con contraseñas o motores'
        return True, 'openssl sólo verifica certificados de ensayo dentro del worktree'
    if programa == 'write_stdin' and len(argumentos) == 3 and argumentos[0] == '--session-id' \
            and argumentos[1].isdigit() and argumentos[2] and set(argumentos[2]) <= {'\x03', '\x04'}:
        # Ctrl-C/Ctrl-D a una orden que el propio agente lanzó: sólo la interrumpe
        # (el revisor de Automejora esperó por esto con Git colgado).
        return True, 'interrumpe una orden que el propio agente lanzó'
    if programa == 'kill':
        return _kill(argumentos, espacio)
    if programa == 'gofmt':
        # Formatear los .go del propio worktree (`gofmt -w internal/x.go`): un
        # agente esperó permiso por eso en un proyecto Go. Un revisor sólo puede mirar (-l, -d).
        rutas = [a for a in argumentos if not a.startswith('-')]
        banderas = {a for a in argumentos if a.startswith('-')}
        if not banderas <= {'-w', '-l', '-d', '-s'} or not rutas:
            return False, 'gofmt con opciones que no reconozco'
        if '-w' in banderas and not escritura:
            return False, 'un revisor no reescribe archivos'
        if any(_fuera_del_espacio(r, espacio) or '..' in Path(r).parts for r in rutas):
            return False, 'gofmt fuera del worktree'
        return True, 'gofmt formatea los archivos del propio worktree'
    if re.fullmatch(r'python3\.\d+', programa) and (
            '/' not in partes[0] or partes[0].startswith(PYTHON_DEL_SISTEMA)):
        # `/opt/homebrew/opt/python@3.11/bin/python3.11 -B deploy/tests/…` es
        # python3: un revisor esperó permiso por eso.
        programa = 'python3'
    if programa in ('git', 'go') and len(argumentos) >= 2 and argumentos[0] == '-C':
        # `git -C ..` desde una subcarpeta del worktree: lo que cuenta es que la
        # carpeta siga dentro (un agente esperó permiso por esto).
        # `go -C macos/helper test` igual: un revisor esperó por eso.
        raiz = Path(espacio).resolve()
        carpeta = Path(argumentos[1])
        carpeta = (raiz / carpeta).resolve() if not carpeta.is_absolute() else carpeta.resolve()
        if argumentos[1].startswith('~') or (carpeta != raiz and raiz not in carpeta.parents):
            return False, programa + ' -C apunta fuera del worktree'
        # Las rutas relativas que siguen son relativas a esa carpeta: `go -C
        # controlplane build -o ../.tmp-alfalfa/x` escribe en el worktree, no fuera
        # (un agente esperó permiso por eso al construir un generador de carga).
        sub = argumentos[1].rstrip('/')
        # Sólo cambian de sentido las que suben (`../`); el resto sigue dentro igual.
        argumentos = [sub + '/' + a if a.startswith('../') else a for a in argumentos[2:]]
        partes = [partes[0]] + argumentos
    prohibidas = BANDERAS_PROHIBIDAS.get(programa, set())
    # La misma letra significa cosas distintas según dónde caiga: `git -c` fija
    # configuración arbitraria, mientras que `git switch -c` crea una rama. Se
    # mira sólo lo que va antes del subcomando, que es donde manda de verdad.
    if programa in SUBCOMANDOS:
        cabecera = argumentos[:next((i for i, a in enumerate(argumentos)
                                     if not a.startswith('-')), len(argumentos))]
    else:
        cabecera = argumentos
    if any(argumento in prohibidas for argumento in cabecera):
        return False, programa + ' con una opción que escribe fuera de su ámbito'
    if programa == 'rg' and any(rg_ejecuta(a) for a in argumentos):
        return False, 'rg con una opción que ejecuta otros programas (--pre, --hostname-bin o -z)'
    if programa == 'git':
        sub = next((a for a in argumentos if not a.startswith('-')), '')
        # `git diff --output=archivo` (y show, log…) escribe donde se le diga.
        # En `git grep`, -o sólo muestra lo que coincide. `git archive` tiene
        # su propio control: sólo hacia una carpeta temporal del worktree.
        if sub != 'archive' and any(a.startswith('--output')
                                    or (a.startswith('-o') and not a.startswith('--') and sub != 'grep')
                                    for a in argumentos):
            return False, 'git con una opción que escribe la salida en un archivo'
    entrada = _entrar_en_el_espacio(programa, argumentos, espacio)
    if entrada is not None:
        return entrada
    borrado = _borrado_de_cache(programa, argumentos, espacio, escritura)
    if borrado is not None:
        return borrado
    if programa == 'node' and ({'--test', '--check'} & set(argumentos)):
        # `node --test` es lo mismo que `npm test`: corre las pruebas del propio
        # proyecto. Sólo esas banderas; cualquier otra (un -e, un --require
        # ajeno) sigue necesitando permiso. Las rutas se miran abajo.
        if any(a.startswith('-') and a not in ('--test', '--check', '--experimental-strip-types')
               and not a.startswith('--test-') for a in argumentos):
            return False, 'node con una opción que no es de pruebas'
        for argumento in argumentos:
            if _fuera_del_espacio(argumento, espacio):
                return False, 'apunta fuera del worktree: ' + argumento
        return True, 'node ejecuta las pruebas del proyecto dentro del worktree'
    if programa in ('python3', 'python') and escritura:
        # -B (sin .pyc) o -u (salida sin búfer) no cambian qué se ejecuta: un
        # agente esperó permiso por `python3 -B …/verificar.py` en una app de iPhone.
        while argumentos and argumentos[0] in OPCIONES_INOFENSIVAS_DE_PYTHON:
            argumentos = argumentos[1:]
    if programa == 'swiftc' and argumentos and ({'-typecheck', '-parse'} & set(argumentos)):
        # Sólo comprobar tipos, sin generar nada: el revisor de una app de Mac
        # esperó permiso por `swiftc -typecheck …`. El SDK está dentro de Xcode y
        # la caché de módulos en el propio worktree.
        if {'-o', '-emit-executable', '-emit-library', '-emit-object', '-c'} & set(argumentos):
            return False, 'swiftc que genera archivos'
        for i, a in enumerate(argumentos):
            previo = argumentos[i - 1] if i else ''
            if previo == '-module-cache-path' and a in _salida_de_compilacion_temporal('swift', ['--scratch-path', a]):
                continue  # caché de módulos en /tmp: regenerable
            if previo in ('-sdk', '-I', '-F', '-module-cache-path', '-target', '-swift-version'):
                if previo == '-sdk' and re.search(r'/Xcode[^/]*\.app/Contents/Developer/Platforms/'
                                                  r'[^/]+/Developer/SDKs/', a):
                    continue
                if previo in ('-target', '-swift-version'):
                    continue
            if _fuera_del_espacio(a, espacio):
                return False, 'swiftc apunta fuera del worktree: ' + a
        return True, 'swiftc sólo comprueba tipos dentro del worktree'
    # Un guion del propio proyecto que sólo envuelve otra orden tras `--`
    # (`python3 ci/supervised_xcodebuild.py --timeout 900 -- xcodebuild … test`,
    # que vigila la memoria mientras compila): vale lo que valga la orden de
    # dentro. Tres revisiones de una app de Mac esperaron por esto.
    if programa in ('python3', 'python') and argumentos and '--' in argumentos \
            and not argumentos[0].startswith('-') and argumentos[0].endswith('.py'):
        corte = argumentos.index('--')
        guion = Path(argumentos[0]) if Path(argumentos[0]).is_absolute() else Path(espacio) / argumentos[0]
        try:
            dentro = Path(espacio).resolve() in guion.resolve().parents
        except OSError:
            dentro = False
        if dentro and corte + 1 < len(argumentos) \
                and not any(_fuera_del_espacio(a, espacio) for a in argumentos[1:corte]):
            veredicto, motivo = _analizar(argumentos[corte + 1:], espacio, escritura,
                                         merge_base=merge_base, lecturas=lecturas)
            if veredicto:
                return True, 'un guion del proyecto que supervisa una orden segura: ' + motivo
    # Un revisor corre las pruebas del proyecto aunque no use pytest: el de
    # seguridad de un proyecto esperó permiso para `python3 deploy/tests/test_deploy.py`.
    prueba = bool(argumentos) and re.fullmatch(r'(?:test_\w+|\w+_test)\.py', Path(argumentos[0]).name) \
        and any(p in ('tests', 'test') for p in Path(argumentos[0]).parts)
    if programa in ('python3', 'python') and (escritura or prueba) and argumentos and not argumentos[0].startswith('-'):
        # Igual que node: el verificador de accesibilidad de una app de iPhone
        # (python3 App/evidencia/accesibilidad/verificar.py) esperó permiso en
        # mitad de una carrera. Sin -c ni -m: eso sigue preguntando.
        guion = argumentos[0]
        destino = Path(guion) if Path(guion).is_absolute() else Path(espacio) / guion
        try:
            dentro = Path(espacio).resolve() in destino.resolve().parents
        except OSError:
            dentro = False
        if dentro and destino.suffix == '.py' and not any(_fuera_del_espacio(a, espacio) for a in argumentos):
            return True, 'python ejecuta un script del propio worktree'
    if programa == 'node' and escritura and argumentos and not argumentos[0].startswith('-'):
        # Un script del propio worktree (p. ej. el que saca las capturas de
        # evidencia) no es más que `npm run` de un guion del proyecto. Sin
        # banderas de node: un -e o un --require ajeno siguen preguntando.
        guion = argumentos[0]
        destino = Path(guion) if Path(guion).is_absolute() else Path(espacio) / guion
        try:
            dentro = Path(espacio).resolve() in destino.resolve().parents
        except OSError:
            dentro = False
        if dentro and destino.suffix in ('.js', '.mjs', '.cjs', '.ts', '.mts') and not any(
                _fuera_del_espacio(a, espacio) for a in argumentos):
            return True, 'node ejecuta un script del propio worktree'
    copia = _copia_dentro(programa, argumentos, espacio, escritura)
    if copia is not None:
        return copia
    robots = _leer_robots(programa, argumentos)
    if robots is not None:
        return robots
    captura = _captura_sin_ventana(programa, argumentos, espacio, escritura)
    if captura is not None:
        return captura
    rutas = [a for a in argumentos if a not in ('-p', '--parents')]
    if programa == 'mkdir' and rutas and all(_es_cache(a, espacio) for a in rutas):
        # Crear .tmp-alfalfa/ para dejar la salida de las pruebas no toca el
        # trabajo: está ignorada y fuera de la huella. Un revisor se
        # quedó esperando a una persona por esto.
        return True, 'crea una carpeta temporal dentro del worktree'
    if programa == 'mkdir' and escritura and rutas and all(
            not r.startswith('-') and not _fuera_del_espacio(r if '/' in r else './' + r, espacio)
            and not r.startswith('~') for r in rutas):
        # Una carpeta vacía en el propio worktree (p. ej. evidence/<ticket>/…) no
        # cambia nada que no pudiera crear igual al escribir un archivo; un agente
        # esperó por `mkdir -p evidence/…`. Un revisor no escribe.
        return True, 'crea carpetas dentro del worktree'
    if programa == 'mkdir' and rutas and all(_carpeta_de_evidencia(r, espacio) for r in rutas):
        # Un revisor no escribe código, pero sí deja su evidencia: uno esperó
        # (y caducó) por `mkdir -p App/evidencia/<ticket>-revision`.
        return True, 'crea una carpeta de evidencia dentro del worktree'
    referencia = _copia_de_referencia(programa, argumentos, espacio, escritura)
    if referencia is not None:
        return referencia
    extraccion = _extraccion_a_cache(programa, argumentos, espacio)
    if extraccion is not None:
        if not extraccion[0]:
            return extraccion
        for argumento in argumentos:
            if _fuera_del_espacio(argumento, espacio):
                return False, 'apunta fuera del worktree: ' + argumento
        return extraccion
    xcode = _xcode(programa, argumentos, escritura, espacio)
    if xcode is not None:
        if not xcode[0]:
            return xcode
        temporales = _salida_de_compilacion_temporal(programa, argumentos, espacio) | _resultados_de_xcode(argumentos)
        for argumento in argumentos:
            if argumento not in temporales and _fuera_del_espacio(argumento, espacio):
                return False, 'apunta fuera del worktree: ' + argumento
        return xcode
    from .. import ajustes
    try:
        propia = ajustes.orden_permitida(programa, list(argumentos), espacio)
    except Exception:
        propia = None
    if isinstance(propia, str) and propia:
        return True, propia
    herramienta = _herramienta_del_proyecto(programa, argumentos)
    if herramienta is not None:
        if not herramienta[0]:
            return herramienta
    elif programa in SUBCOMANDOS:
        sub = next((a for a in argumentos if not a.startswith('-')), '')
        if sub not in SUBCOMANDOS[programa]:
            return False, programa + ' ' + (sub or '(sin subcomando)') + ' no es de sólo lectura'
        if not escritura and sub in GIT_ESCRITURA:
            return False, 'un revisor no escribe en la rama que juzga'
        # `git merge --abort` cierra una fusión que ya empezó. `git merge otra`
        # trae trabajo ajeno a la rama del ticket, y eso lo decide una persona.
        # Con una referencia delante (`git checkout f6a5d66 -- test`) trae esos
        # archivos de otro commit sin cambiar de rama, igual que
        # `git restore --source`: el arreglo de una integración en rojo se paró
        # a esperar a una persona por eso.
        if sub == 'checkout' and ('--' not in argumentos
                                  or not argumentos[argumentos.index('--') + 1:]
                                  or len([a for a in argumentos[:argumentos.index('--')]
                                          if a != '--' and not a.startswith('-') and a != sub]) > 1
                                  or any(a.startswith('-') for a in argumentos[:argumentos.index('--')])):
            return False, 'git checkout sólo para devolver archivos (git checkout -- ruta), no para cambiar de rama'
        # Avanzar sin fusionar hasta la línea común (o la base) sólo pone la
        # rama al día: lo pedía quien arreglaba una integración en rojo.
        avance = (sub == 'merge' and '--ff-only' in argumentos
                  and [a for a in argumentos if not a.startswith('-') and a != 'merge']
                  in [['main'], ['origin/main']] + [[r] for r in _ramas_de_avance()])
        ramas_recuperacion = ({merge_base, 'origin/' + merge_base}
                              if isinstance(merge_base, str) and merge_base
                              and not merge_base.startswith('-') and not any(c.isspace() for c in merge_base)
                              else set())
        merge_recuperacion = escritura and tuple(argumentos) in (
            {('merge', rama) for rama in ramas_recuperacion}
            | {('merge', '--no-edit', rama) for rama in ramas_recuperacion}
        )
        if sub == 'merge' and not avance and not merge_recuperacion \
                and not ({'--abort', '--continue', '--quit'} & set(argumentos)):
            return False, 'traer otra rama a la del ticket lo decide ' + ajustes.nombre('decide') + ', no el agente'
    elif programa == 'sed' and _sed_que_solo_imprime(argumentos):
        pass  # `sed -n '660,810p' archivo` sólo lee: un agente de QA esperó permiso por esto
    elif programa not in LECTURA:
        return False, programa + ' no está entre las órdenes de sólo lectura'
    temporales = _salida_de_compilacion_temporal(programa, argumentos, espacio) | _sdk_de_xcode(programa, argumentos)
    referencia = False
    for argumento in argumentos:
        if argumento not in temporales and _fuera_del_espacio(argumento, espacio) \
                and not (programa in SOLO_METADATOS and _ruta_instalada(argumento)):
            if herramienta is None and programa not in SUBCOMANDOS \
                    and _lectura_de_referencia(programa, argumentos, argumento, espacio, lecturas):
                referencia = True
                continue
            return False, 'apunta fuera del worktree: ' + argumento
    if herramienta is not None:
        return herramienta
    if referencia:
        return True, programa + ' de sólo lectura sobre una carpeta de referencia'
    return True, programa + ' de sólo lectura dentro del worktree'


def _destino_de_renombrado(detalle):
    """Destino de un renombrado (`move_path`/`movePath`), None si no hay, False si no se entiende."""
    if not isinstance(detalle, dict):
        return None
    candidatos = [detalle.get(clave) for clave in ('move_path', 'movePath')]
    if isinstance(detalle.get('kind'), dict):
        candidatos += [detalle['kind'].get(clave) for clave in ('move_path', 'movePath')]
    candidatos = [c for c in candidatos if c is not None]
    if not candidatos:
        return None
    if len(candidatos) > 1 and len(set(map(str, candidatos))) > 1 or not isinstance(candidatos[0], str):
        return False
    return candidatos[0]


def _rutas(params):
    """Rutas que un cambio de archivos dice tocar, o None si no se sabe."""
    for clave in ('changes', 'files', 'fileChanges'):
        valor = params.get(clave)
        if isinstance(valor, dict):
            rutas = list(valor.keys())
            for detalle in valor.values():
                # Un renombrado de Codex lleva el destino dentro (`move_path`):
                # mirar sólo el origen dejaba mover un archivo del worktree a
                # ~/.zshrc sin preguntar.
                destino = _destino_de_renombrado(detalle)
                if destino is False:
                    return None
                if destino:
                    rutas.append(destino)
            return rutas
        if isinstance(valor, list):
            rutas = []
            for fila in valor:
                ruta = fila.get('path') if isinstance(fila, dict) else fila
                if not isinstance(ruta, str):
                    return None
                rutas.append(ruta)
                destino = _destino_de_renombrado(fila)
                if destino is False:
                    return None
                if destino:
                    rutas.append(destino)
            return rutas
    ruta = params.get('path')
    return [ruta] if isinstance(ruta, str) else None


def safe_file_change(params, espacio):
    """Escribir dentro del propio worktree del ticket no necesita permiso."""
    if not espacio:
        return False, 'el ticket no tiene worktree propio'
    rutas = _rutas(params if isinstance(params, dict) else {})
    if not rutas:
        return False, 'no se puede saber qué archivos toca'
    for ruta in rutas:
        if _fuera_del_espacio(ruta, espacio) or ruta.startswith('~') or ruta.startswith('-'):
            return False, 'escribe fuera del worktree: ' + str(ruta)
    return True, 'escribe ' + str(len(rutas)) + ' archivo(s) dentro de su worktree'


# ---------------------------------------------------------------------------
# Lecturas fuera del worktree
#
# Un requerimiento puede citar su fuente de verdad fuera de la carpeta del
# proyecto (el código del juego, un repo hermano). Claude Code en modo
# restringido bloquea esas lecturas y la tarea quedaba parada esperando a
# alguien. Leer lo que el propio usuario citó no cambia nada: se concede como
# carpeta de solo lectura, nunca escritura.

RE_RUTA_ABSOLUTA = re.compile(r"(?:~|/(?:Users|Volumes|private|opt|tmp|srv|home))/[^\s`'\"<>()\[\]{}|,;*]+")
RE_TRAMO_CON_ESPACIO = re.compile(r" [^\s`'\"<>()\[\]{}|,;*]+")
HERRAMIENTAS_DE_LECTURA = ('Read', 'Glob', 'Grep', 'LS', 'NotebookRead')


def ruta_leida(denegacion):
    """La ruta absoluta que intentó leer una herramienta, o None si no es lectura."""
    if not isinstance(denegacion, dict) or denegacion.get('tool_name') not in HERRAMIENTAS_DE_LECTURA:
        return None
    entrada = denegacion.get('tool_input') or {}
    ruta = entrada.get('file_path') or entrada.get('path') or entrada.get('notebook_path')
    if not isinstance(ruta, str) or not ruta.startswith(('/', '~')):
        return None
    return os.path.normpath(os.path.expanduser(ruta))


def rutas_citadas(*textos):
    """Rutas absolutas que aparecen en el requerimiento o sus adjuntos."""
    citadas = []
    for texto in textos:
        for cita in RE_RUTA_ABSOLUTA.finditer(texto or ''):
            bruto = _con_espacios(cita.group(0), texto, cita.end())
            ruta = os.path.normpath(os.path.expanduser(bruto.rstrip('.:')))
            if ruta not in citadas:
                citadas.append(ruta)
    return citadas


def _con_espacios(ruta, texto, fin):
    """Completa una ruta citada que el espacio cortó, si la continuación existe.

    «~/Library/Application Support/MiApp/…» se leía como «~/Library/Application»;
    al no existir, se concedía su carpeta padre, ~/Library, que contiene las
    sesiones web de la app. Sólo se une el texto siguiente cuando la carpeta
    resultante existe en disco: así no se inventan rutas a partir de la prosa.
    """
    while not os.path.exists(os.path.expanduser(ruta.rstrip('.:'))):
        extra, pos = '', fin
        for _ in range(4):  # un nombre con hasta cuatro espacios: «Mobile Documents», «My Great Folder»
            tramo = RE_TRAMO_CON_ESPACIO.match(texto, pos)
            if not tramo:
                return ruta
            extra, pos = extra + tramo.group(0), tramo.end()
            if os.path.exists(os.path.expanduser(ruta + extra.split('/', 1)[0])):
                break
            if '/' in extra:
                return ruta
        else:
            return ruta
        ruta, fin = ruta + extra, pos
    return ruta


def carpeta_de_lectura(ruta, citadas, repositorio=None):
    """Qué carpeta conceder para leer `ruta`, o None si hay que preguntar.

    Se concede la carpeta citada que la contiene (o el repositorio del
    proyecto), no sólo el archivo: así una verificación no pide permiso
    archivo por archivo.
    """
    if not ruta or sensible(ruta):
        return None
    candidatas = [c for c in citadas if c != '/' and len(Path(c).parts) >= 4]
    if repositorio:
        candidatas.append(os.path.normpath(str(repositorio)))
    mejor = None
    for base in candidatas:
        # Una cita puede ser un archivo (exista o no): su carpeta es lo que se lee.
        raiz = base if os.path.isdir(base) else os.path.dirname(base)
        if (ruta == raiz or ruta.startswith(raiz.rstrip('/') + '/')) and not expone_sesiones(raiz):
            if mejor is None or len(raiz) > len(mejor):
                mejor = raiz
    return mejor


# Opciones de python que no cambian qué se ejecuta; -E, -s e -I lo aíslan más.
OPCIONES_INOFENSIVAS_DE_PYTHON = ('-B', '-u', '-E', '-s', '-I')

# `python -c` que sólo lee y calcula. Los agentes de Ollama revisan sus
# entregables con código en línea (contar ramas de un esquema, comparar con los
# ejemplos) porque se les pidió no dejar guiones en .tmp-alfalfa/, y cada uno
# esperaba al usuario: el texto trae saltos de línea y `$ref`, y eso se rechaza
# antes de mirar qué hace. Aquí se lee el código: sólo
# módulos que no tocan el sistema, `open` en modo lectura y nada que escriba,
# borre, mueva, ejecute o lea el entorno (donde vive el token del forge).
MODULOS_DE_LECTURA = {
    'json', 'glob', 're', 'pathlib', 'sys', 'collections', 'itertools', 'math', 'statistics',
    'hashlib', 'textwrap', 'string', 'functools', 'operator', 'csv', 'tomllib', 'ast',
    'difflib', 'fnmatch', 'pprint', 'datetime', 'unicodedata', 'decimal', 'fractions',
    'jsonschema', 'referencing', 'typing', 'dataclasses', 'enum', 'os', 'os.path',
}
LLAMADAS_PROHIBIDAS = {'exec', 'eval', 'compile', '__import__', 'getattr', 'setattr', 'delattr',
                       'globals', 'locals', 'vars', 'breakpoint', 'input', 'exit', 'quit'}
ATRIBUTOS_PROHIBIDOS = {
    'write', 'write_text', 'write_bytes', 'writelines', 'unlink', 'remove', 'rmdir', 'removedirs',
    'rmtree', 'rename', 'renames', 'mkdir', 'makedirs', 'touch', 'chmod', 'chown', 'lchmod',
    'symlink_to', 'hardlink_to', 'link', 'symlink', 'system', 'popen', 'fork', 'kill', 'truncate',
    'environ', 'environb', 'getenv', 'putenv', 'unsetenv', 'chdir', 'startfile', 'utime',
    'execv', 'execve', 'execl', 'execvp', 'spawnv', 'spawnl', 'posix_spawn', 'setrecursionlimit',
    # Salir del worktree por la puerta de atrás: lo que se lee acaba en el modelo.
    'home', 'expanduser', 'expandvars',
}


def _modo_de_lectura(nodo):
    """El modo de un open(): sólo una constante sin w, a, x ni +."""
    modo = nodo.args[1] if len(nodo.args) > 1 else next(
        (k.value for k in nodo.keywords if k.arg == 'mode'), None)
    if modo is None:
        return True
    return isinstance(modo, ast.Constant) and isinstance(modo.value, str) and not set(modo.value) & set('wax+')


def python_que_solo_lee(codigo, espacio):
    """(¿sólo lee?, motivo) para el código de un `python -c`.

    Leer también tiene límite: lo que se imprime llega al modelo, así que una
    ruta escrita en el código tiene que caer dentro del worktree, como con `cat`.
    """
    try:
        arbol = ast.parse(codigo)
    except (SyntaxError, ValueError):
        return False, 'python -c que no se puede analizar'
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Import, ast.ImportFrom)):
            nombres = [a.name for a in nodo.names] if isinstance(nodo, ast.Import) else [nodo.module or '']
            if isinstance(nodo, ast.ImportFrom) and nodo.level:
                return False, 'python -c con una importación relativa'
            fuera = [n for n in nombres
                     if n not in MODULOS_DE_LECTURA and n.split('.')[0] not in MODULOS_DE_LECTURA - {'os'}]
            if fuera:
                return False, 'python -c importa ' + ', '.join(fuera)
            if isinstance(nodo, ast.ImportFrom) and any(a.name in ATRIBUTOS_PROHIBIDOS | LLAMADAS_PROHIBIDAS
                                                        or a.name == '*' for a in nodo.names):
                return False, 'python -c importa algo que escribe o ejecuta'
        elif isinstance(nodo, ast.Attribute):
            # __version__ es sólo un texto: los agentes lo miran antes de validar con jsonschema.
            if (nodo.attr.startswith('__') and nodo.attr != '__version__') or nodo.attr in ATRIBUTOS_PROHIBIDOS:
                return False, 'python -c usa ' + nodo.attr
            # str.replace(viejo, nuevo) es habitual; Path.replace(destino) mueve un archivo.
        elif isinstance(nodo, ast.Name) and nodo.id in LLAMADAS_PROHIBIDAS:
            return False, 'python -c usa ' + nodo.id
        elif (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
              # Un '/' suelto es el separador de un split, no una ruta.
              and nodo.value not in ('/', '~')
              and (nodo.value.startswith(('/', '~')) or '..' in Path(nodo.value).parts)
              and '\n' not in nodo.value and _fuera_del_espacio(nodo.value, espacio)):
            return False, 'python -c lee fuera del worktree: ' + nodo.value[:80]
        if isinstance(nodo, ast.Call):
            funcion = nodo.func
            nombre = funcion.id if isinstance(funcion, ast.Name) else (
                funcion.attr if isinstance(funcion, ast.Attribute) else '')
            if nombre == 'open' and not _modo_de_lectura(nodo):
                return False, 'python -c abre un archivo para escribir'
            if nombre == 'replace' and len(nodo.args) < 2:
                return False, 'python -c mueve un archivo con replace'
            if nombre in ('dump', 'dumps') and any(k.arg == 'cls' for k in nodo.keywords):
                return False, 'python -c serializa con una clase propia'
    return True, 'python -c que sólo lee y calcula'


def python_en_linea_seguro(argv, espacio):
    """(¿aprobable?, motivo) para `[<python de confianza>, '-c', código]`.

    El argv llega sin pasar por una shell (harness nativo), así que el código se
    puede leer tal cual; con una orden en texto no aplica.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        return False, ''
    # Un agente esperó diez minutos por `python3 -I -c`.
    argv = [argv[0]] + list(dropwhile(lambda a: a in OPCIONES_INOFENSIVAS_DE_PYTHON, argv[1:]))
    if len(argv) != 3 or argv[1] != '-c':
        return False, ''
    programa = str(argv[0])
    if not re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(programa).name):
        return False, ''
    if '/' in programa and not (programa.startswith(PYTHON_DEL_SISTEMA)
                                or programa_de_confianza(programa, espacio)):
        return False, 'un python que no es del sistema, del worktree ni del .venv del integrador'
    return python_que_solo_lee(str(argv[2]), espacio)


def _ramas_de_avance():
    from .. import ajustes
    try:
        return [r for r in ajustes.ramas_de_avance() or () if isinstance(r, str) and r and not r.startswith('-')
                and not any(c.isspace() for c in r)]
    except Exception:
        return []


def fuera_del_espacio(ruta, espacio):
    """¿`ruta` sale del worktree `espacio`? Para las órdenes que aprueba un integrador."""
    return _fuera_del_espacio(ruta, espacio)
