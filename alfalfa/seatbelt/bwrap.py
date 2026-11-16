"""Sandbox bubblewrap (`bwrap`) de Linux para las órdenes de AlfalfaCode.

La política de Seatbelt (seatbelt/__init__.py) expresada con montajes en lugar de reglas sobre rutas.
El diseño sigue a codex-rs/linux-sandbox/src/bwrap.rs (OpenAI Codex, Apache-2.0; ver NOTICE, LICENSE y
PROCEDENCIA.md de esta carpeta): reescrito en Python, sin código copiado.

- Todo el disco se lee (`--ro-bind / /`) y sólo se escribe en las raíces de Seatbelt: la carpeta de
  trabajo resuelta, $TMPDIR resuelto y /tmp, que hace de /private/tmp.
- Cada `.git` que halla el recorrido, en la raíz o en subcarpetas: el propio `.git` queda como punto de
  montaje, que no se renombra ni se borra, y sus `hooks` y `config*` (también los de `modules/`) de
  solo lectura. El resto (refs, objetos, índice) se escribe: ramas, commits y stash no piden permiso.
  Si falta `hooks` o `config`, se crean vacíos antes, como `git init`, para que la orden no los pueda
  crear (un montaje sólo protege lo que existe). Un
  `.git` que es un archivo (worktree o submódulo) queda de solo lectura, y también los hooks y config
  del repositorio al que apunta si caen en una raíz escribible.
- Las carpetas de instrucciones (`.alfalfa`, `.agents`, `.claude`, `.codex`) halladas en la carpeta de
  trabajo y las de primer nivel de los temporales, de solo lectura.
- El hogar de AlfalfaCode y las carpetas protegidas de la sesión, de solo lectura aunque caigan en una
  raíz escribible (o la contengan: entonces esa raíz queda de solo lectura), con los mismos enlaces
  duros rechazados que en Seatbelt.
- Los antecesores de cada `.git` o carpeta de instrucciones anidados, del hogar y de la carpeta de
  trabajo dentro de otra raíz quedan fijados como puntos de montaje: no se pueden mover. Un directorio
  ilegible queda entero de solo lectura, porque puede ocultar un `.git`.
- Red: `--unshare-net` y además un filtro seccomp que niega `socket()`, también el de los sockets
  Unix, que en /tmp o /run llegan a tmux, a Docker o al bus de la sesión, fuera del sandbox, e
  io_uring, que abriría sockets sin pasar por `socket()`. Falla con EPERM, «Operation not permitted»,
  como en Seatbelt, y la política on-request lo reconoce como denegación. `network=True` quita las
  dos cosas: es la red completa.
- Sin capacidades (`--cap-drop ALL`, también si el arnés corre como root), en su propia sesión, con
  sus propios espacios de nombres de usuario, procesos e IPC, y sin poder crear más espacios de usuario
  cuando bwrap lo admite (`--disable-userns`, desde 0.8). Muere con el arnés (`--die-with-parent`),
  igual que las órdenes en segundo plano no le sobreviven en macOS.
- Antes de cada orden se lanza lo mismo con `true` para separar un fallo del sandbox de un fallo de la
  orden: si bwrap falta o no arranca, SandboxUnavailable y la orden pide el permiso «SIN SANDBOX».

Diferencias con Seatbelt, porque un montaje sólo protege lo que ya existe al lanzar la orden:
- No impide crear un `.git` o una carpeta de instrucciones nuevos (un `git init` en una subcarpeta);
  Seatbelt los niega por nombre a cualquier profundidad.
- En los temporales sólo se protegen las carpetas de instrucciones de primer nivel.
- Si el árbol supera el tope del recorrido, los `.git` que no llegó a ver no se protegen.
- Un `.git`, una carpeta de instrucciones o un antecesor fijado que sea un enlace simbólico no se puede
  fijar: el sandbox no arranca. Un `hooks` o un `config*` enlazado deja el `.git` entero de solo lectura.
- Fuera de x86_64 y aarch64 no hay filtro de red, y un programa de 32 bits muere en el sandbox sin red.
"""
from __future__ import annotations

import asyncio
import os
import platform
import struct
import subprocess
import sys
import threading

from ..procesos import comunicar
from . import (
    PREFLIGHT_SECONDS,
    PROTECTED_METADATA,
    SandboxUnavailable,
    SeatbeltSandbox,
    _ancestors,
    _contains,
    comprobar_enlaces_del_hogar,
    hogares_protegidos,
    recorrer_carpeta_de_trabajo,
)

# Como sandbox-exec en macOS: el del sistema, nunca el primero del PATH.
BWRAP = '/usr/bin/bwrap'
TEMPORAL = '/tmp'
SH = '/bin/sh'
TRUE = '/usr/bin/true'
# El filtro seccomp llega a bwrap por este descriptor: el lanzador lo abre y bwrap lo lee y lo cierra.
FD_FILTRO = '9'
LANZADOR = 'exec ' + FD_FILTRO + '<"$1" || exit 125; shift; exec "$@"'
NOMBRE_LANZADOR = 'alfalfa-bwrap'
# Lo que la shell del lanzador añade al entorno cuando /bin/sh es bash (SHLVL y _). Ningún llamador del
# arnés pasa estas variables a una orden aislada: bwrap las quita. PWD no: bwrap la pone siempre, con la
# carpeta de la orden, después de entrar en ella.
DE_LA_SHELL = ('OLDPWD', 'SHLVL', '_')
INSTALAR = 'sudo apt install bubblewrap'
APPARMOR = '/proc/sys/kernel/apparmor_restrict_unprivileged_userns'

# ------------------------------------------------------------------ seccomp

# BPF clásico sobre struct seccomp_data {int nr; u32 arch; u64 instruction_pointer; u64 args[6]}.
_LD_ABS, _JEQ, _JGE, _RET = 0x20, 0x15, 0x35, 0x06
_PERMITIR, _ERRNO, _MATAR = 0x7FFF0000, 0x00050000, 0x80000000
_EPERM = 1
_X32 = 0x40000000
# platform.machine(): (AUDIT_ARCH_*, __NR_socket, __NR_io_uring_setup, ¿llamadas x32?)
_ARQUITECTURAS = {
    'x86_64': (0xC000003E, 41, 425, True),
    'aarch64': (0xC00000B7, 198, 425, False),
}


def _ensamblar(programa):
    """Instrucciones (código, salto_si, salto_no, k) con etiquetas de texto como destino de los saltos."""
    etiquetas, instrucciones = {}, []
    for paso in programa:
        if isinstance(paso, str):
            etiquetas[paso] = len(instrucciones)
        else:
            instrucciones.append(paso)
    datos = bytearray()
    for i, (codigo, si, no, k) in enumerate(instrucciones):
        def salto(destino):
            return etiquetas[destino] - i - 1 if isinstance(destino, str) else destino
        datos += struct.pack('=HBBI', codigo, salto(si), salto(no), k)
    return bytes(datos)


def filtro_sin_red(maquina=None):
    """El programa seccomp de la variante sin red: `socket()` e `io_uring_setup()` fallan con EPERM y
    cualquier llamada de otra arquitectura (32 bits, x32) mata el proceso, porque podría abrir sockets
    por otro número de llamada."""
    maquina = maquina or platform.machine()
    try:
        arquitectura, socket_nr, io_uring_nr, x32 = _ARQUITECTURAS[maquina]
    except KeyError:
        raise SandboxUnavailable('No hay filtro de red para la arquitectura ' + str(maquina)
                                 + ' (sólo x86_64 y aarch64).') from None
    programa = [(_LD_ABS, 0, 0, 4), (_JEQ, 0, 'matar', arquitectura), (_LD_ABS, 0, 0, 0)]
    if x32:
        programa.append((_JGE, 'matar', 0, _X32))
    programa += [(_JEQ, 'negar', 0, socket_nr), (_JEQ, 'negar', 0, io_uring_nr), (_RET, 0, 0, _PERMITIR),
                 'negar', (_RET, 0, 0, _ERRNO | _EPERM),
                 'matar', (_RET, 0, 0, _MATAR)]
    return _ensamblar(programa)


_filtros = {}
_cerrojo = threading.Lock()


def ruta_del_filtro(datos):
    """Ruta que abre el lanzador para pasarle el filtro a bwrap. Es un memfd sellado del arnés: ninguna
    orden del sandbox puede cambiarlo, al contrario que un archivo en $TMPDIR o /tmp."""
    import fcntl
    with _cerrojo:
        fd = _filtros.get(datos)
        if fd is None:
            try:
                fd = os.memfd_create('alfalfa-seccomp', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            except (AttributeError, OSError) as exc:
                raise SandboxUnavailable('No se pudo preparar el filtro de red: ' + str(exc)) from exc
            try:
                vista = memoryview(datos)
                while vista:
                    vista = vista[os.write(fd, vista):]
                fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                            | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            except (AttributeError, OSError) as exc:
                os.close(fd)
                raise SandboxUnavailable('No se pudo sellar el filtro de red: ' + str(exc)) from exc
            _filtros[datos] = fd
    return '/proc/' + str(os.getpid()) + '/fd/' + str(fd)


# ------------------------------------------------------------------ bwrap

_opciones_vistas = {}


def opciones_admitidas(ejecutable):
    """Las opciones opcionales que este bwrap admite (`--disable-userns` llegó en 0.8)."""
    try:
        clave = (ejecutable, os.stat(ejecutable).st_mtime_ns)
    except OSError:
        return frozenset()
    if clave not in _opciones_vistas:
        try:
            ayuda = subprocess.run([ejecutable, '--help'], capture_output=True, text=True, timeout=10,
                                   env={'PATH': '/usr/bin:/bin'})
            texto = ayuda.stdout + ayuda.stderr
        except (OSError, subprocess.SubprocessError):
            texto = ''
        _opciones_vistas[clave] = frozenset(o for o in ('--disable-userns',) if o in texto)
    return _opciones_vistas[clave]


def _profundidad(ruta):
    return ruta.rstrip('/').count('/')


def _leer(ruta, tope=65536):
    try:
        with open(ruta, 'rb') as archivo:
            return archivo.read(tope).decode('utf-8', errors='replace')
    except OSError:
        return ''


def pista(detalle):
    """Cómo arreglar un bwrap que no arranca por los espacios de nombres de usuario."""
    if _leer(APPARMOR).strip() == '1' and any(t in detalle for t in (
            'setting up uid map', 'RTM_NEWADDR', 'Operation not permitted', 'Permission denied')):
        return (' Ubuntu limita con AppArmor los espacios de nombres de usuario sin privilegios: bwrap necesita '
                'un perfil que se los permita (docs/USO.md, «Linux: el sandbox»).')
    if 'No permissions to create new namespace' in detalle:
        return (' El sistema no deja crear espacios de nombres de usuario sin privilegios (docs/USO.md, '
                '«Linux: el sandbox»).')
    return ''


class BwrapSandbox:
    """Construye y comprueba la orden bwrap para una carpeta de trabajo, con la interfaz de SeatbeltSandbox.

    La red queda cortada por defecto; `network=True` es la variante con red que sólo selecciona la
    política on-request (permisos/bajo_demanda.py) tras un permiso «red» aprobado.
    """

    etiqueta = 'bwrap'
    temporal = TEMPORAL
    # Las mismas raíces escribibles que Seatbelt, con /tmp en lugar de /private/tmp.
    writable_roots = SeatbeltSandbox.writable_roots

    def __init__(self, root, *, network=False, tmpdir=None, home=None, executable=BWRAP, protegidas=()):
        from ..complementos.rutas_habilidades import carpetas_protegidas
        self.root = root
        self.protegidas = hogares_protegidos((*protegidas, *(p for p in carpetas_protegidas(root) if p.exists())))
        self.network = network is True
        self.tmpdir = tmpdir
        self.home = home
        self.executable = executable

    def montajes(self):
        """Las opciones de bwrap, en orden: lo que va después prevalece."""
        roots = self.writable_roots()
        workspace = roots[0]
        hallados = {}
        anclados, sellados, instrucciones, _ = recorrer_carpeta_de_trabajo(workspace, hallados=hallados)
        protegidas = hogares_protegidos((*self.protegidas, *instrucciones))
        comprobar_enlaces_del_hogar(protegidas)

        def escribible(ruta):
            return any(_contains(root, ruta) for root in roots)

        args = ['--new-session', '--die-with-parent', '--unshare-user', '--unshare-pid', '--unshare-ipc']
        if not self.network:
            args.append('--unshare-net')
        args += sorted(opciones_admitidas(self.executable))
        args += ['--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc']
        for root in sorted(roots, key=_profundidad):
            args += ['--bind', root, root]

        hogares = [h for h in protegidas if escribible(h) or any(_contains(h, root) for root in roots)]
        fijos = set(anclados)
        fijos.update(p for root in roots[1:] for p in _ancestors(os.path.dirname(workspace), root))
        fijos.update(p for h in hogares for root in roots if _contains(root, h)
                     for p in _ancestors(os.path.dirname(h), root))
        for ruta in sorted(fijos, key=_profundidad):
            if os.path.islink(ruta) or not os.path.isdir(ruta):
                raise SandboxUnavailable('No se puede fijar ' + ruta + ' en el sandbox: no es una carpeta.')
            args += ['--bind', ruta, ruta]

        vistos = set()
        for git in sorted(hallados['git'], key=_profundidad):
            self._proteger_git(git, escribible, args, vistos)
        for carpeta in sorted(instrucciones, key=_profundidad):
            if os.path.islink(carpeta):
                raise SandboxUnavailable('La carpeta de instrucciones ' + carpeta
                                         + ' es un enlace simbólico; bwrap no la puede proteger.')
            args += ['--ro-bind', carpeta, carpeta]
        for root in roots[1:]:
            for nombre in PROTECTED_METADATA[1:]:
                carpeta = os.path.join(root, nombre)
                if os.path.islink(carpeta):
                    raise SandboxUnavailable('La carpeta de instrucciones ' + carpeta
                                             + ' es un enlace simbólico; bwrap no la puede proteger.')
                if os.path.isdir(carpeta):
                    args += ['--ro-bind', carpeta, carpeta]
        for carpeta in sorted(s for s in sellados if s in hallados['ilegibles']):
            # Dentro del sandbox bastaría un chmod para ver lo que esconde.
            args += ['--ro-bind', carpeta, carpeta]
        for hogar in sorted(hogares, key=_profundidad):
            if os.path.lexists(hogar):
                args += ['--ro-bind', hogar, hogar]
        args += ['--cap-drop', 'ALL']
        return args

    def _proteger_git(self, git, escribible, args, vistos):
        if os.path.islink(git):
            raise SandboxUnavailable('El .git ' + git + ' es un enlace simbólico; bwrap no lo puede proteger.')
        if os.path.isdir(git):
            args += ['--bind', git, git]
            _proteger_gitdir(git, escribible, args, vistos)
            return
        if not os.path.isfile(git):
            return
        args += ['--ro-bind', git, git]
        destino = _gitdir_del_puntero(git)
        if destino is None:
            return
        comun = _directorio_comun(destino)
        if comun is not None and comun != destino:
            # Un worktree: su gitdir sólo aporta config.worktree; los hooks son los del común.
            _proteger_gitdir(comun, escribible, args, vistos)
            _proteger_config_worktree(destino, escribible, args, _worktree_config(comun))
        else:
            _proteger_gitdir(destino, escribible, args, vistos)

    def command_args(self, argv):
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise SandboxUnavailable('La orden debe ser una lista de argumentos no vacía.')
        montajes = self.montajes()
        if self.network:
            return [self.executable, *montajes, '--', *argv]
        sin_shell = [a for nombre in DE_LA_SHELL for a in ('--unsetenv', nombre)]
        return [SH, '-c', LANZADOR, NOMBRE_LANZADOR, ruta_del_filtro(filtro_sin_red()), self.executable,
                *montajes, *sin_shell, '--seccomp', FD_FILTRO, '--', *argv]

    async def prepare(self, argv):
        """argv envuelto, tras comprobar que el sandbox arranca; si no, SandboxUnavailable."""
        if not sys.platform.startswith('linux'):
            raise SandboxUnavailable('bubblewrap sólo existe en Linux.')
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise SandboxUnavailable('No existe ' + self.executable + '; instala bubblewrap: ' + INSTALAR + '.')
        # Recorre la carpeta buscando .git anidados: fuera del bucle de eventos.
        command = await asyncio.to_thread(self.command_args, argv)
        # Lo mismo con true: separa un fallo del sandbox de un fallo de la propia orden.
        probe = command[:len(command) - len(argv)] + [TRUE]
        try:
            proc = await asyncio.create_subprocess_exec(
                *probe, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, start_new_session=True, env={'PATH': '/usr/bin:/bin'})
        except (OSError, ValueError) as exc:
            raise SandboxUnavailable('No se pudo iniciar bwrap: ' + (str(exc) or type(exc).__name__)) from exc
        try:
            _, err = await comunicar(proc, PREFLIGHT_SECONDS)
        except asyncio.TimeoutError:
            raise SandboxUnavailable('bwrap no respondió en ' + str(int(PREFLIGHT_SECONDS)) + ' segundos.') from None
        if proc.returncode != 0:
            detail = (err or b'').decode(errors='replace').strip()[:500]
            raise SandboxUnavailable('El sandbox no arrancó (código ' + str(proc.returncode) + ')'
                                     + (': ' + detail if detail else '.') + pista(detail))
        return command


# ------------------------------------------------------------------ .git

def _proteger_gitdir(gitdir, escribible, args, vistos, nivel=0):
    """hooks y config* de solo lectura en un directorio de git (y en los de sus submódulos)."""
    real = os.path.realpath(gitdir)
    if real in vistos or not escribible(real):
        return  # fuera de las raíces escribibles ya es de solo lectura
    vistos.add(real)
    hooks = os.path.join(gitdir, 'hooks')
    configs, entero = [], os.path.islink(hooks)
    if not entero and not os.path.lexists(hooks):
        try:
            os.mkdir(hooks, 0o755)
        except OSError:
            entero = True
    if not entero and not os.path.isdir(hooks):
        entero = True
    config = os.path.join(gitdir, 'config')
    if not entero and not os.path.lexists(config):
        try:
            os.close(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except OSError:
            entero = True
    try:
        with os.scandir(gitdir) as entradas:
            for entrada in entradas:
                if entrada.name.casefold().startswith('config'):
                    if entrada.is_symlink() or not entrada.is_file(follow_symlinks=False):
                        entero = True
                    configs.append(entrada.path)
    except OSError:
        entero = True
    if entero:
        args += ['--ro-bind', gitdir, gitdir]
        return
    args += ['--ro-bind', hooks, hooks]
    for config in sorted(configs):
        args += ['--ro-bind', config, config]
    con_worktree = _worktree_config(gitdir)
    if con_worktree and not os.path.lexists(os.path.join(gitdir, 'config.worktree')):
        _proteger_config_worktree(gitdir, escribible, args, True)
    worktrees = os.path.join(gitdir, 'worktrees')
    if os.path.isdir(worktrees) and not os.path.islink(worktrees):
        try:
            nombres = sorted(os.listdir(worktrees))
        except OSError:
            nombres = []
        for nombre in nombres:
            carpeta = os.path.join(worktrees, nombre)
            if os.path.isdir(carpeta) and not os.path.islink(carpeta):
                _proteger_config_worktree(carpeta, escribible, args, con_worktree)
    if nivel < 8:
        for submodulo in _gitdirs_de_modulos(os.path.join(gitdir, 'modules')):
            args += ['--bind', submodulo, submodulo]
            _proteger_gitdir(submodulo, escribible, args, vistos, nivel + 1)


def _proteger_config_worktree(gitdir, escribible, args, crear):
    """config.worktree de solo lectura; si `extensions.worktreeConfig` está activo y falta, se crea vacío
    antes, para que la orden no pueda crearlo con un core.hooksPath."""
    ruta = os.path.join(gitdir, 'config.worktree')
    if not escribible(os.path.realpath(gitdir)):
        return
    if os.path.islink(ruta):
        args += ['--ro-bind', gitdir, gitdir]
        return
    if not os.path.lexists(ruta) and crear:
        try:
            os.close(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except OSError:
            args += ['--ro-bind', gitdir, gitdir]
            return
    if os.path.isfile(ruta):
        args += ['--ro-bind', ruta, ruta]


def _worktree_config(gitdir):
    return 'worktreeconfig' in _leer(os.path.join(gitdir, 'config')).casefold()


def _gitdirs_de_modulos(modulos, tope=1000):
    """Los directorios de git de los submódulos: los que tienen HEAD bajo `modules/`, a cualquier
    profundidad (un submódulo en a/b vive en modules/a/b)."""
    if not os.path.isdir(modulos) or os.path.islink(modulos):
        return []
    hallados, pendientes = [], [modulos]
    while pendientes and len(hallados) < tope:
        actual = pendientes.pop()
        try:
            with os.scandir(actual) as entradas:
                hijas = [e.path for e in entradas if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for hija in sorted(hijas):
            if os.path.isfile(os.path.join(hija, 'HEAD')):
                hallados.append(hija)
            else:
                pendientes.append(hija)
    return hallados


def _gitdir_del_puntero(archivo):
    """El directorio al que apunta un `.git` de archivo («gitdir: …»), o None."""
    texto = _leer(archivo, 4096).strip()
    if not texto.startswith('gitdir:'):
        return None
    destino = texto[len('gitdir:'):].strip()
    if not destino:
        return None
    destino = os.path.realpath(os.path.join(os.path.dirname(archivo), destino))
    return destino if os.path.isdir(destino) else None


def _directorio_comun(gitdir):
    """El directorio común de un worktree (archivo `commondir`), o el propio gitdir."""
    comun = _leer(os.path.join(gitdir, 'commondir'), 4096).strip()
    if not comun:
        return gitdir
    comun = os.path.realpath(os.path.join(gitdir, comun))
    return comun if os.path.isdir(comun) else gitdir
