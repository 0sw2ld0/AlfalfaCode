"""Sandbox Seatbelt de macOS para las órdenes de AlfalfaCode.

Derivado de codex-rs/sandboxing/src/seatbelt.rs (OpenAI Codex, Apache-2.0;
ver NOTICE, LICENSE y PROCEDENCIA.md de esta carpeta). Se conserva la forma de
create_seatbelt_command_args: `/usr/bin/sandbox-exec -p <política> -DCLAVE=ruta
… -- argv`, con las rutas como parámetros -D y nunca dentro del texto de la
política, salvo la expresión regular que protege `.git`, que se escapa.

Adaptaciones respecto a Codex, todas en este módulo (los .sbpl son copia exacta):
- Lectura de todo el disco y escritura sólo en tres raíces fijas: la carpeta de
  trabajo resuelta, $TMPDIR resuelto y /private/tmp.
- `.git` queda de solo lectura en la raíz y en cualquier subcarpeta de la
  carpeta de trabajo (Codex lo protege sólo en la raíz), y además con una
  denegación final para que $TMPDIR o /private/tmp no la reabran cuando la
  carpeta de trabajo vive dentro de ellos.
- La expresión depende de la ruta, así que no se puede renombrar ningún
  directorio que la cambie: los antecesores de cada `.git` anidado (hallados al
  construir la política) y los de la carpeta de trabajo dentro de otra raíz.
  Un directorio ilegible al recorrerlo puede ocultar un `.git`: no se renombra
  ni borra ningún directorio suyo ni de su interior. Si el árbol es demasiado
  grande para recorrerlo, eso vale para toda la carpeta de trabajo.
- Las raíces se resuelven con realpath en lugar de rechazar enlaces: la
  política nombra el destino real, que es lo que Seatbelt compara.
- El hogar del entorno y el de la sesión siguen de solo lectura dentro de una raíz
  escribible. Se anclan sus antecesores y se rechazan los hardlinks preexistentes.
- Sin proxy gestionado ni sockets Unix: la red queda cerrada salvo la
  variante explícita `network=True`, que equivale a la red completa de Codex.
"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import unicodedata
from pathlib import Path

from ..procesos import comunicar

# Como Codex: sólo el de /usr/bin, nunca el que aparezca antes en PATH.
SANDBOX_EXEC = '/usr/bin/sandbox-exec'
PRIVATE_TMP = '/private/tmp'
# Las carpetas de instrucciones las escribe el arnés tras su tarjeta, fuera de las órdenes.
PROTECTED_METADATA = ('.git', '.alfalfa', '.agents', '.claude', '.codex')
PREFLIGHT_SECONDS = 10.0
# Entradas que se recorren buscando `.git` anidados antes de rendirse.
SCAN_LIMIT = 200_000
# Independiente del recorrido del workspace: agotar aquél sella directorios; éste debe comprobarse entero.
HOME_SCAN_LIMIT = 200_000

_HERE = Path(__file__).resolve().parent
BASE_POLICY = (_HERE / 'seatbelt_base_policy.sbpl').read_text()
NETWORK_POLICY = (_HERE / 'seatbelt_network_policy.sbpl').read_text()
PREFERENCES_POLICY = (_HERE / 'seatbelt_preferences_policy.sbpl').read_text()
TLS_TRUST_POLICY = '(allow mach-lookup (global-name "com.apple.TrustEvaluationAgent"))\n'

# Metacaracteres de la sintaxis de expresiones regulares de Seatbelt.
_REGEX_META = set('\\.^$|?*+()[]{}')


class SandboxUnavailable(Exception):
    """No se puede ejecutar dentro del sandbox; la orden no debe lanzarse sola."""


def regex_escape(text):
    # Dentro de #"…" Seatbelt no admite escapes: una comilla cerraría la
    # cadena. `[^/]` la cubre y sólo puede ampliar lo protegido, nunca reducirlo.
    return ''.join('[^/]' if ch == '"' else '\\' + ch if ch in _REGEX_META else ch for ch in text)


def _sin_mayusculas(texto):
    """Cada letra como `[gG]`: en APFS sin distinción de mayúsculas `.GIT` es el mismo `.git`, y así
    la protección no depende de cómo compare Seatbelt. Seatbelt no media el renombrado que sólo
    cambia mayúsculas (`mv .git .GIT` sale bien con cualquier regla), pero con este patrón el
    `.GIT` resultante sigue igual de protegido: ni sus hooks se escriben ni sale de su sitio."""
    partes = []
    for c in texto:
        minuscula, mayuscula = c.lower(), c.upper()
        if minuscula == mayuscula:
            partes.append(regex_escape(c))
        elif len(minuscula) == len(mayuscula) == 1:
            partes.append('[' + minuscula + mayuscula + ']')
        else:
            partes.append('(' + '|'.join(regex_escape(v) for v in dict.fromkeys((c, minuscula, mayuscula))) + ')')
    return ''.join(partes)


def protected_metadata_name_regex(root, name, nested=True):
    """Como seatbelt_protected_metadata_name_regex, también en subcarpetas si `nested`; el nombre
    protegido, sin distinguir mayúsculas."""
    root = str(root)
    while len(root) > 1 and root.endswith('/'):
        root = root[:-1]
    prefix = '' if root == '/' else regex_escape(root)
    depth = '(.*/)?' if nested else ''
    return '^' + prefix + '/' + depth + _sin_mayusculas(name) + '(/.*)?$'


def _policy_regex(regex):
    if '"' in regex:
        raise SandboxUnavailable('Expresión de protección no representable en Seatbelt.')
    return '#"' + regex + '"'


def _checked(path, what):
    text = str(path)
    if '\0' in text or '\n' in text or '\r' in text:
        raise SandboxUnavailable(what + ' contiene caracteres de control.')
    if not os.path.isabs(text):
        raise SandboxUnavailable(what + ' no es una ruta absoluta.')
    real = os.path.realpath(text)
    if not os.path.isdir(real):
        raise SandboxUnavailable(what + ' no es una carpeta existente: ' + real)
    if real == '/':
        raise SandboxUnavailable(what + ' no puede ser la raíz del disco.')
    return real


def _contains(parent, child):
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def hogar_por_defecto():
    """Ruta del hogar del entorno, sin leer su contenido."""
    return os.path.realpath(os.path.expanduser(os.environ.get('ALFALFA_HOME') or '~/.alfalfa'))


def hogares_protegidos(rutas=()):
    return tuple(dict.fromkeys([hogar_por_defecto()] + [
        os.path.realpath(os.path.expanduser(str(p))) for p in rutas if p is not None]))


def ruta_comparable(ruta):
    """APFS puede identificar mayúsculas y formas Unicode distintas como la misma ruta."""
    return unicodedata.normalize('NFD', str(ruta)).casefold()


def regex_hogar(ruta, *, nested=True):
    variantes = dict.fromkeys(unicodedata.normalize(forma, str(ruta)) for forma in ('NFC', 'NFD'))
    final = '(/.*)?$' if nested else '$'
    return '(' + '|'.join('^' + _sin_mayusculas(p.rstrip('/')) + final for p in variantes) + ')'


def _filtro_hogar(ruta, *, nested=True):
    # Cada forma en su literal: juntarlas puede superar el límite del lector SBPL.
    variantes = dict.fromkeys(unicodedata.normalize(forma, str(ruta)) for forma in ('NFC', 'NFD'))
    final = '(/.*)?$' if nested else '$'
    filtros = ['(regex ' + _policy_regex('^' + _sin_mayusculas(p.rstrip('/')) + final) + ')'
               for p in variantes]
    return '(require-any ' + ' '.join(filtros) + ')'


def antecesores_del_hogar(hogar, raiz):
    while ruta_comparable(hogar) != ruta_comparable(raiz) and _contains(
            ruta_comparable(raiz), ruta_comparable(hogar)):
        yield hogar
        hogar = os.path.dirname(hogar)


def comprobar_enlaces_del_hogar(hogares):
    """Seatbelt compara rutas: un hardlink anterior al sandbox podría tener otro nombre.

    Sólo se examinan metadatos, nunca contenidos. Sin un recorrido completo no se asegura
    el aislamiento; tampoco se permite un hogar que ya comparta archivos con otras rutas.
    """
    def error(exc):
        raise exc

    for hogar in hogares:
        if not os.path.lexists(hogar):
            continue
        vistos = 0
        try:
            for carpeta, dirs, archivos in os.walk(hogar, followlinks=False, onerror=error):
                vistos += len(dirs) + len(archivos)
                if vistos > HOME_SCAN_LIMIT:
                    raise SandboxUnavailable('El hogar de AlfalfaCode es demasiado grande para comprobar sus enlaces.')
                for nombre in archivos:
                    datos = os.stat(os.path.join(carpeta, nombre), follow_symlinks=False)
                    if stat.S_ISREG(datos.st_mode) and datos.st_nlink > 1:
                        raise SandboxUnavailable('El hogar de AlfalfaCode contiene archivos con enlaces duros; '
                                                 'no se puede aislar por rutas.')
        except OSError as exc:
            raise SandboxUnavailable('No se pudieron comprobar los enlaces del hogar de AlfalfaCode.') from exc


def _ancestors(path, stop):
    """`path` y sus padres estrictamente por debajo de `stop`."""
    found = []
    while path != stop and _contains(stop, path):
        found.append(path)
        path = os.path.dirname(path)
    return found


def nested_metadata_ancestors(workspace, limit=None):
    """(directorios, subárboles) de `workspace` cuyo renombrado sacaría un `.git`
    anidado de la protección, o None si el árbol supera `limit` entradas.

    Los directorios se protegen uno a uno; los subárboles, enteros, porque son
    directorios ilegibles que pueden ocultar un `.git` a cualquier profundidad:
    dentro del sandbox basta un chmod para volver a verlos. No sigue enlaces
    simbólicos: Seatbelt compara la ruta real, y el destino de un enlace dentro
    de la carpeta ya se recorre por su propia ruta.
    """
    limit = SCAN_LIMIT if limit is None else limit
    protected, sealed, pending, seen = set(), set(), [workspace], 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        return None
                    if entry.name.casefold() in {n.casefold() for n in PROTECTED_METADATA}:
                        protected.update(_ancestors(current, workspace))
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
        except OSError:
            protected.update(_ancestors(current, workspace))
            sealed.add(current)
    # Lo que ya cubre un subárbol sellado no necesita su propia regla.
    sealed = {p for p in sealed if not any(o != p and _contains(o, p) for o in sealed)}
    protected = {p for p in protected if not any(_contains(o, p) for o in sealed)}
    return sorted(protected), sorted(sealed)


class SeatbeltSandbox:
    """Construye y comprueba la orden sandbox-exec para una carpeta de trabajo.

    La red queda bloqueada por defecto. `network=True` es la variante con red
    que sólo selecciona la política on-request (permisos/bajo_demanda.py) tras
    un permiso «red» aprobado, y sólo para ese intento.
    """

    def __init__(self, root, *, network=False, tmpdir=None, home=None, executable=SANDBOX_EXEC, protegidas=()):
        from ..complementos.rutas_habilidades import carpetas_protegidas
        self.root = root
        self.protegidas = hogares_protegidos((*protegidas, *(p for p in carpetas_protegidas(root) if p.exists())))
        self.network = network is True
        self.tmpdir = tmpdir
        self.home = home
        self.executable = executable

    def writable_roots(self):
        root = _checked(self.root, 'La carpeta de trabajo')
        # La expresión que protege `.git` se ancla en la carpeta de trabajo: si ésta ya está dentro
        # de un `.git` (p. ej. `.git/hooks`), no lo cubriría y un hook escrito aquí correría fuera
        # del sandbox en el siguiente commit. Esa carpeta no se abre para escribir.
        # En APFS sin distinción de mayúsculas `.GIT` es el mismo `.git`.
        protegidos = {nombre.casefold() for nombre in PROTECTED_METADATA}
        if any(parte.casefold() in protegidos for parte in Path(root).parts + Path(str(self.root)).parts):
            raise SandboxUnavailable('La carpeta de trabajo está dentro de un .git; no se abre para escribir: '
                                     + root)
        roots = [root]
        home = os.path.realpath(str(self.home or Path.home()))
        tmpdir = self.tmpdir if self.tmpdir is not None else os.environ.get('TMPDIR')
        if tmpdir:
            try:
                tmp = _checked(tmpdir, '$TMPDIR')
            except SandboxUnavailable:
                tmp = None
            # Un $TMPDIR que abarque el hogar ampliaría la escritura: se ignora.
            if tmp and not _contains(tmp, home) and tmp not in roots:
                roots.append(tmp)
        private_tmp = os.path.realpath(PRIVATE_TMP)
        if private_tmp not in roots:
            roots.append(private_tmp)
        return roots

    def policy(self):
        """(texto de la política, [(clave, ruta)]) en el orden de Codex."""
        roots = self.writable_roots()
        comprobar_enlaces_del_hogar(self.protegidas)
        from ..complementos.rutas_habilidades import carpetas_descubiertas
        try:
            habilidades = carpetas_descubiertas(roots[0], HOME_SCAN_LIMIT)
        except (OSError, ValueError, RuntimeError) as exc:
            raise SandboxUnavailable('No se pudieron comprobar las carpetas de habilidades del proyecto.') from exc
        protegidas = hogares_protegidos((*self.protegidas, *habilidades))
        comprobar_enlaces_del_hogar(protegidas)
        workspace = roots[0]
        protected = [_policy_regex(protected_metadata_name_regex(workspace, name)) for name in PROTECTED_METADATA]
        protected.extend(_policy_regex(protected_metadata_name_regex(root, name))
                         for root in roots[1:] for name in PROTECTED_METADATA if name != '.git')
        params, components, anchors = [], [], []
        for index, root in enumerate(roots):
            key = 'WRITABLE_ROOT_' + str(index)
            params.append((key, root))
            anchors.append('(deny file-write-unlink (require-all (literal (param "' + key
                           + '")) (vnode-type DIRECTORY)))')
            subpath = '(subpath (param "' + key + '"))'
            if index == 0:
                parts = [subpath] + ['(require-not (regex ' + rx + '))' for rx in protected]
                components.append('(require-all ' + ' '.join(parts) + ' )')
            else:
                components.append(subpath)
        # Renombrar un antecesor movería un .git fuera de la expresión anclada a
        # la carpeta: hacia /private/tmp, por ejemplo, donde ya sería escribible.
        scan = nested_metadata_ancestors(workspace)
        if scan is None:
            nested, sealed = [], ['WRITABLE_ROOT_0']
        else:
            nested, sealed = scan[0], []
            for index, path in enumerate(scan[1]):
                key = 'SEALED_SUBTREE_' + str(index)
                params.append((key, path))
                sealed.append(key)
        for key in sealed:
            anchors.append('(deny file-write-unlink (require-all (subpath (param "' + key + '"))'
                           ' (vnode-type DIRECTORY)))')
        outer = [p for root in roots[1:] for p in _ancestors(os.path.dirname(workspace), root)]
        # También si la sesión se abre dentro del propio hogar: esa raíz queda de solo lectura.
        hogares = [h for h in protegidas if any(
            _contains(ruta_comparable(root), ruta_comparable(h))
            or _contains(ruta_comparable(h), ruta_comparable(root)) for root in roots)]
        # Ni el hogar ni sus antecesores se pueden mover fuera de la protección.
        for path in sorted({p for h in hogares for root in roots for p in antecesores_del_hogar(h, root)}):
            anchors.append('(deny file-write-unlink (require-all '
                           + _filtro_hogar(path, nested=False) + ' (vnode-type DIRECTORY)))')
        for index, path in enumerate(sorted(set(nested + outer))):
            key = 'PROTECTED_ANCESTOR_' + str(index)
            params.append((key, path))
            anchors.append('(deny file-write-unlink (require-all (literal (param "' + key
                           + '")) (vnode-type DIRECTORY)))')
        sections = [
            BASE_POLICY,
            '; allow read-only file operations\n(allow file-read*)',
            '(allow file-write*\n' + ' '.join(components) + '\n)\n' + '\n'.join(anchors),
        ]
        if self.network:
            sections.append('(allow network-outbound)\n(allow network-inbound)\n'
                            + NETWORK_POLICY + TLS_TRUST_POLICY)
        else:
            sections.append('')
        sections.append(PREFERENCES_POLICY)
        sections.append('(deny mach-lookup (xpc-service-name-prefix ""))')
        # Las reglas posteriores prevalecen: ninguna raíz que contenga la carpeta
        # de trabajo (p. ej. $TMPDIR) puede reabrir su .git.
        sections.extend('(deny file-write* (regex ' + rx + '))' for rx in protected)
        for index, hogar in enumerate(hogares):
            key = 'ALFALFA_HOME_' + str(index)
            params.append((key, hogar))
            sections.append('(deny file-write* (subpath (param "' + key + '")) '
                            + _filtro_hogar(hogar) + ')')
        sections.append('(deny system-fcntl (fcntl-command 80 110))')
        return '\n'.join(sections), params

    def command_args(self, argv):
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise SandboxUnavailable('La orden debe ser una lista de argumentos no vacía.')
        text, params = self.policy()
        return ([self.executable, '-p', text] + ['-D' + key + '=' + value for key, value in params]
                + ['--'] + list(argv))

    async def prepare(self, argv):
        """argv envuelto, tras comprobar que el sandbox arranca; si no, SandboxUnavailable."""
        if sys.platform != 'darwin':
            raise SandboxUnavailable('Seatbelt sólo existe en macOS.')
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise SandboxUnavailable('No existe ' + self.executable + '.')
        # Recorre la carpeta buscando .git anidados: fuera del bucle de eventos.
        command = await asyncio.to_thread(self.command_args, argv)
        # Misma política y parámetros con /usr/bin/true: separa un fallo del
        # sandbox de un fallo de la propia orden.
        probe = command[:command.index('--') + 1] + ['/usr/bin/true']
        try:
            proc = await asyncio.create_subprocess_exec(
                *probe, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, start_new_session=True, env={'PATH': '/usr/bin:/bin'})
        except (OSError, ValueError) as exc:
            raise SandboxUnavailable('No se pudo iniciar sandbox-exec: ' + (str(exc) or type(exc).__name__)) from exc
        try:
            _, err = await comunicar(proc, PREFLIGHT_SECONDS)
        except asyncio.TimeoutError:
            raise SandboxUnavailable('sandbox-exec no respondió en '
                                     + str(int(PREFLIGHT_SECONDS)) + ' segundos.') from None
        if proc.returncode != 0:
            detail = (err or b'').decode(errors='replace').strip()[:500]
            raise SandboxUnavailable('El sandbox no arrancó (código ' + str(proc.returncode) + ')'
                                     + (': ' + detail if detail else '.'))
        return command
