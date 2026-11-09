"""Rutas que un agente no puede leer, tocar o recibir, en un solo sitio.

Antes había tres listas que divergían: `native_tools.PRIVATE` (herramientas de
los agentes de Ollama), `command_safety.SENSIBLES`/`expone_sesiones` (qué
lecturas fuera del worktree se conceden) y lo que vetaba
`agents.vetos_de_escritura` (las carpetas de sólo lectura de Claude). Ahora
viven aquí, junto con el entorno sin secretos que hereda cada agente.

Sólo depende de la biblioteca estándar: lo importan `agents`, `native_tools` y
`arnes.permisos.ordenes` sin ciclos.
"""
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Nombres que nunca se tocan
#
# Las dos listas comparten un núcleo pero no se pueden fundir sin cambiar lo
# que se permite:
# - `SENSIBLES` decide qué carpetas de fuera del worktree se conceden para leer.
#   Sumarle `.git` dejaría de conceder lecturas dentro del .git de un repo
#   citado, cosa que hoy se concede.
# - `PRIVADOS_DEL_ESPACIO` es lo que las herramientas nativas no abren ni listan
#   dentro del worktree. Sumarle `secrets`, `credentials`, `Cookies` o `.npmrc`
#   impediría editar carpetas y archivos normales de un proyecto
#   (src/credentials/, el .npmrc del repo), y además dejarían de listarse.
# Por eso son dos listas, las dos definidas aquí y con el núcleo en común.
NUCLEO_SECRETO = frozenset({'.ssh', '.aws', '.gnupg', '.env'})
# WebKit, Cookies y HTTPStorages guardan sesiones web: la de ollama.com que la app de
# Mac abre en su vista interna vive en ~/Library/WebKit/<app>/WebsiteDataStore/.
SENSIBLES = ('.ssh', '.aws', '.gnupg', '.kube', '.docker', 'Keychains', '.netrc', '.npmrc',
             '.pypirc', 'id_rsa', 'id_ed25519', '.env', 'credentials', 'secrets', '.git-credentials',
             'WebKit', 'Cookies', 'HTTPStorages')

# El núcleo está en las dos (lo comprueba tests/test_permisos_rutas.py).
PRIVADOS_DEL_ESPACIO = NUCLEO_SECRETO | {'.git', '.DS_Store'}


def privado_del_espacio(parte):
    """¿Es este tramo de ruta algo que las herramientas nativas no abren?"""
    return parte in PRIVADOS_DEL_ESPACIO or parte.startswith('.env.') or parte.endswith(('.pem', '.key'))


def sensible(ruta):
    partes = Path(ruta).parts
    return any(p in SENSIBLES or p.startswith('.env') for p in partes)


# Dentro de una Library, las carpetas que agrupan datos de muchas apps: concederlas
# (o una de sus apps) alcanzaría su WebKit, sus cookies o su caché de red.
CONTENEDORES_DE_APPS = ('Containers', 'Group Containers', 'Caches')
ALMACENES_WEB = ('WebKit', 'Cookies', 'HTTPStorages')
# En macOS /Users, /home… son firmlinks del volumen de datos: la misma carpeta se
# alcanza también como /System/Volumes/Data/Users, y realpath no lo deshace.
VOLUMEN_DE_DATOS = '/System/Volumes/Data'


def expone_sesiones(carpeta):
    """True si conceder `carpeta` daría lectura a un almacén de sesiones web.

    `sensible` mira los nombres de la ruta, no lo que hay debajo: conceder
    ~/Library pasaba el filtro y alcanzaba ~/Library/WebKit, donde vive la
    sesión de ollama.com. Se rechaza la carpeta de usuario o cualquiera que la
    contenga, una Library entera y los contenedores de apps de una Library
    (Containers/<app>/Data/Library/WebKit, Caches/<app>/WebKit).

    Una misma carpeta tiene muchos nombres: un enlace simbólico, el firmlink
    /System/Volumes/Data/Users o /users en minúsculas (APFS no distingue). Se
    juzgan la ruta tal cual y la canónica, sin mayúsculas, y si existe se
    compara por inodo con la casa real y sus almacenes.
    """
    if not carpeta:
        return True
    ruta = os.path.normpath(os.path.expanduser(str(carpeta)))
    canonica = os.path.realpath(ruta)
    if any(_expone_por_nombre(_sin_volumen_de_datos(r)) for r in (ruta, canonica)):
        return True
    return _alcanza_por_inodo(canonica)


def _sin_volumen_de_datos(ruta):
    plegada = ruta.casefold()
    prefijo = VOLUMEN_DE_DATOS.casefold()
    while plegada == prefijo or plegada.startswith(prefijo + '/'):
        ruta, plegada = ruta[len(prefijo):] or '/', plegada[len(prefijo):] or '/'
    return ruta


def _expone_por_nombre(ruta):
    ruta = Path(ruta.casefold())
    partes = ruta.parts
    if sensible(str(ruta)) or any(p in {s.casefold() for s in SENSIBLES} for p in partes):
        return True
    casa = Path(str(Path.home()).casefold())
    if ruta == casa or ruta in casa.parents or (len(partes) <= 3 and partes[1:2] in (('users',), ('home',))):
        return True  # «/», «/Users», «/Users/<alguien>»: contienen su Library
    if ruta in Path(VOLUMEN_DE_DATOS.casefold()).parents:
        return True  # «/System», «/System/Volumes»: contienen el volumen con las casas
    if 'library' in partes:
        debajo = partes[len(partes) - partes[::-1].index('library'):]
        if not debajo or (debajo[0] in {c.casefold() for c in CONTENEDORES_DE_APPS} and len(debajo) <= 3):
            return True
    return False


def _alcanza_por_inodo(ruta):
    """Último recurso para nombres que no se pueden deshacer (/.vol, montajes):
    la carpeta es la casa, una que la contiene, su Library o un contenedor de
    apps, o está dentro de uno de sus almacenes web."""
    if not os.path.exists(ruta):
        return False
    casa = Path.home()
    biblioteca = casa / 'Library'
    contenedoras = [casa, *casa.parents, biblioteca, *(biblioteca / c for c in CONTENEDORES_DE_APPS)]
    almacenes = [biblioteca / a for a in ALMACENES_WEB]
    try:
        if any(os.path.exists(c) and os.path.samefile(ruta, c) for c in contenedoras):
            return True
        existentes = [a for a in almacenes if os.path.exists(a)]
        return any(os.path.samefile(dentro, a) for dentro in (Path(ruta), *Path(ruta).parents) for a in existentes)
    except OSError:
        return True  # si no se puede comprobar, no se concede


def lecturas_concedibles(carpetas):
    """Las carpetas de sólo lectura que sí pueden llegar a Claude.

    Una concesión guardada antes de vetar las carpetas con sesiones web
    (p. ej. ~/Library) no vuelve a llegar a Claude.
    """
    return [d for d in carpetas or [] if isinstance(d, str) and d.startswith('/') and not expone_sesiones(d)]


# ---------------------------------------------------------------------------
# Entorno de los agentes

# Variables que parecen secretos: el agente no las necesita y heredarlas le
# daba los tokens del usuario: su forja git, Ollama Cloud y el buscador web.
RE_SECRETO = re.compile(r'TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL', re.I)
# Lo único de ese tipo que cada agente sí usa: su propia autenticación, si el
# usuario la tiene en el entorno (si no, la toma de su llavero o su auth.json).
SECRETOS_PROPIOS = {
    'claude': {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'},
    'codex': {'OPENAI_API_KEY', 'CODEX_API_KEY'},
    'opencode': {'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENCODE_API_KEY'},
}


def entorno_de_agente(provider, env=None):
    """El entorno de un agente sin los secretos del usuario."""
    base = os.environ if env is None else env
    propios = SECRETOS_PROPIOS.get(provider, set())
    return {k: v for k, v in base.items() if k in propios or not RE_SECRETO.search(k)}


# ---------------------------------------------------------------------------
# Vetos de escritura de Claude

# Cuántos vetos admite un nivel antes de agruparlos. Cada veto viaja dos veces
# (Edit y Write) en la orden de Claude: con miles de carpetas hermanas la orden
# pasaría de ARG_MAX, como ya pasó con los archivos.
TOPE_DE_VETOS_POR_NIVEL = 64
# Claude sólo entiende `*` y `**` en sus reglas: los corchetes los toma como
# letras (probado con `[!m]*` y `[^m]*`: no vetaban «abc» y sí la carpeta propia).
# Un nombre con cualquiera de estos caracteres no se agrupa: se veta tal cual.
CARACTERES_DE_PATRON = frozenset('[]*?\\')


def _comun(a, b):
    """Longitud del prefijo común de dos nombres."""
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    return k


def _agrupar(nivel, parte, hijos):
    """Vetos de las hermanas `hijos` (pares nombre, es_carpeta) agrupados por prefijo.

    Para cada hermana se toma lo que comparte con el nombre propio más la
    primera letra en que se aparta: «abc» → `a*`, «mz» → `mz*`, «medico-10» →
    `medico-10*` (propio «medico-1»). El propio no empieza por ninguno de esos
    prefijos, así que sigue escribible; y las miles de hermanas de un nivel
    caben en unas pocas decenas de prefijos. Una hermana que es prefijo del
    propio («me», «medico-») no se puede agrupar sin cubrirlo: se veta exacta.
    """
    vetos = []
    for nombre, es_carpeta in hijos:
        k = _comun(nombre, parte)
        if k == len(nombre) or CARACTERES_DE_PATRON & set(nombre[:k + 1]):
            vetos.append(f'{nivel}/{nombre}/**' if es_carpeta else f'{nivel}/{nombre}')
            continue
        prefijo = f'{nivel}/{nombre[:k + 1]}*'
        vetos += [prefijo, f'{prefijo}/**']
    return vetos


def vetos_de_escritura(carpetas, propia=None):
    """Rutas (patrón de Claude) donde no se puede escribir.

    Un agente trabaja en data/agents/studio-workspaces/medico-* y lee
    data/agents: vetar `data/agents/**` le vetaba su propio worktree y cada
    arreglo acababa en «Claude necesita aprobación». Si la carpeta propia cae
    dentro de una de sólo lectura, se veta cada hermana del camino hacia ella
    (y los archivos sueltos de cada nivel), nunca el camino mismo. Si un nivel
    pasa de TOPE_DE_VETOS_POR_NIVEL, sus hermanas se agrupan por prefijo.
    """
    vetos = []
    destino = Path(propia).resolve() if propia else None
    for d in carpetas:
        base = Path(d.rstrip('/') or '/')
        try:
            relativo = destino.relative_to(base.resolve()) if destino else None
        except ValueError:
            relativo = None
        if not relativo or not relativo.parts:
            if relativo is None:
                vetos.append(f'{base}/**')
            continue  # la propia carpeta es la de lectura: no hay qué vetar sin vetarla
        nivel = base
        for parte in relativo.parts:
            del_nivel, hermanas = [], []
            # Archivos que aparezcan después en este nivel (tasks.json, <tarea>.jsonl…).
            vetado_con_punto = '.' not in parte
            if vetado_con_punto:
                del_nivel.append(f'{nivel}/*.*')
            try:
                hijos = sorted(nivel.iterdir())
            except OSError:
                hijos = []
            for hijo in hijos:
                if hijo.name == parte:
                    continue
                if hijo.is_dir():
                    hermanas.append((hijo.name, True))
                elif not (vetado_con_punto and '.' in hijo.name):
                    # Los que tienen punto ya los cubre `*.*`: data/agents guarda
                    # casi 7.000 .jsonl y uno por uno el agente no arrancaba
                    # («Argument list too long»).
                    hermanas.append((hijo.name, False))
            if len(del_nivel) + len(hermanas) > TOPE_DE_VETOS_POR_NIVEL and not CARACTERES_DE_PATRON & set(parte):
                del_nivel += _agrupar(nivel, parte, hermanas)
            else:
                del_nivel += [f'{nivel}/{n}/**' if carpeta else f'{nivel}/{n}' for n, carpeta in hermanas]
            vetos.extend(del_nivel)
            nivel = nivel / parte
    return list(dict.fromkeys(vetos))
