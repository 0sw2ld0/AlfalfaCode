"""Permisos de terminal: auto sólo concede una lista cerrada de lecturas.

No reutiliza safe_command: éste presupone un worktree desechable.
"""
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

from ..seatbelt import nombre_del_sandbox, temporal_del_sistema

# Lecturas que Manual y «Aceptar ediciones» dejan pasar sin preguntar. Es una lista de
# permitidos por programa y opción, no de prohibidos: lo que no se reconoce entero pregunta.
# La orden corre igualmente en el sandbox (sin red, sin escribir fuera de la carpeta); esta
# lista sólo decide si además hace falta un sí por ser un cambio. Antes sólo pasaban pwd,
# git status (tres formas), cat/head/wc/ls con muy pocas opciones y rg --files: git log -1,
# git diff, ls -lh, cat .gitignore o rg -n hola preguntaban.
GIT_LECTURA = {'status', 'log', 'show', 'diff', 'blame', 'shortlog', 'describe', 'rev-parse', 'ls-files',
               'ls-tree', 'merge-base', 'rev-list', 'cat-file', 'grep', 'check-ignore', 'branch', 'tag',
               'reflog', 'whatchanged'}
# Opciones de git que escriben, ejecutan programas configurados o leen fuera del repositorio.
GIT_PROHIBIDAS = ('--output', '--ext-diff', '--textconv', '--no-index', '--open-files-in-pager', '-O',
                  '--exec', '--upload-pack', '--receive-pack', '--edit-description', '--set-upstream-to',
                  '--unset-upstream', '--delete', '--move', '--copy', '--force', '--track', '--create-reflog',
                  '--sign', '--annotate', '--message', '--file')
# Lecturas de git que no aceptan nombres sueltos (un nombre suelto crearía la rama o la etiqueta).
GIT_SOLO_LISTAR = {'branch': {'-a', '-r', '-v', '-vv', '-l', '--list', '--all', '--remotes', '--show-current',
                              '--merged', '--no-merged', '--contains', '--sort', '--format', '--color',
                              '--no-color', '--column', '--no-column', '-q'},
                   'tag': {'-l', '--list', '-n', '--sort', '--contains', '--merged', '--no-merged', '--format',
                           '--points-at', '--column', '--no-column'},
                   'reflog': {'show', '-n', '--date', '--format', '--oneline'}}
# Opciones de las órdenes de archivos: (sueltas, con valor). Ningún valor es una ruta.
OPCIONES = {
    'cat': ({'-n', '-b', '-s', '-v', '-e', '-t', '-u', '-l'}, {}),
    'head': ({'-q', '-v'}, {'-n': False, '-c': False}),
    'tail': ({'-q', '-v', '-r'}, {'-n': False, '-c': False, '-b': False}),
    'wc': ({'-l', '-c', '-w', '-m'}, {}),
    'nl': ({'-b', '-ba', '-bt', '-bn'}, {'-w': False, '-s': False, '-v': False, '-i': False}),
    'file': ({'-b', '-i', '-L', '-h', '-k', '-z', '--mime', '--mime-type', '--brief'}, {}),
    'stat': ({'-L', '-l', '-n', '-q', '-r', '-s', '-x'}, {'-f': False, '-t': False}),
    'du': ({'-s', '-h', '-k', '-m', '-c', '-a', '-x', '-sh', '-hs', '-ch', '-sk', '-H', '-L', '-P'},
           {'-d': False}),
}
# grep y rg: opciones con valor (el valor nunca es una ruta: las de archivo, como -f, preguntan).
GREP_CON_VALOR = {'-e', '-m', '-A', '-B', '-C', '--include', '--exclude', '--exclude-dir', '--color',
                  '--colour', '--max-count', '--context', '--after-context', '--before-context', '--regexp'}
RG_CON_VALOR = {'-e', '-m', '-A', '-B', '-C', '-g', '-t', '-T', '--glob', '--iglob', '--type', '--type-not',
                '--max-count', '--context', '--after-context', '--before-context', '--regexp', '--color',
                '--max-depth', '-d', '--sort', '--sortr', '-M', '--max-columns', '--max-filesize', '-j',
                '--threads'}
# --pre ejecuta un programa por archivo; -z/--search-zip lanza descompresores; --hostname-bin ejecuta.
RG_PROHIBIDAS = {'--pre', '--pre-glob', '-z', '--search-zip', '--hostname-bin', '--ignore-file', '--path-separator'}
FIND_OPCIONES = {'-H', '-L', '-P', '-E', '-X', '-s', '-x', '-d'}
IMPRIME = re.compile(r'(?:\d+|\$)(?:,(?:\d+|\$))?p')
FIND_PROHIBIDAS = {'-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprint0', '-fprintf', '-fls'}


def _ruta_leible(value, root, workspace):
    """¿`value` es una ruta dentro de la carpeta, sin enlaces ni tramos privados o con secretos?"""
    from .rutas import privado_del_espacio, sensible
    if not value or '\0' in value:
        return False
    path = root / value
    resuelta = path.resolve()
    if '..' in Path(value).parts or not resuelta.is_relative_to(workspace):
        return False
    relativa = resuelta.relative_to(workspace)
    if any(privado_del_espacio(p) for p in relativa.parts) or sensible(relativa):
        return False
    for parent in (path, *path.parents):
        if parent == root or parent == workspace:
            break
        if parent.is_symlink():
            return False
    return True


def _archivos(programa, args, root, workspace):
    """cat, head, tail, wc, nl, file, stat, du: opciones conocidas y rutas leíbles."""
    sueltas, con_valor = OPCIONES[programa]
    i = 0
    while i < len(args):
        a = args[i]
        if a in sueltas:
            i += 1
            continue
        if a in con_valor:
            if i + 1 >= len(args):
                return False
            i += 2
            continue
        # -n20, -c5, -20 (head y tail)
        if programa in ('head', 'tail') and (a[:2] in ('-n', '-c') and a[2:].lstrip('+').isdigit()
                                             or a[1:].isdigit() and a.startswith('-')):
            i += 1
            continue
        if a.startswith('-'):
            return False  # tail -f no termina; lo demás no se conoce
        if not _ruta_leible(a, root, workspace):
            return False
        i += 1
    return True


def _ls(args, root, workspace):
    for a in args:
        if a.startswith('-'):
            # ls sólo lista: cualquier combinación de letras vale.
            if a.startswith('--') and a not in ('--color', '--color=auto', '--color=never'):
                return False
            continue
        if not _ruta_leible(a, root, workspace):
            return False
    return True


def _busqueda(args, root, workspace, con_valor, prohibidas):
    """grep y rg: opciones conocidas, un patrón y rutas leíbles."""
    patron, i = False, 0
    while i < len(args):
        a = args[i]
        nombre = a.split('=', 1)[0]
        if nombre in prohibidas or a in prohibidas:
            return False
        if a == '--':
            rutas = args[i + 1:]
            if not patron and rutas:
                rutas = rutas[1:]
            return all(_ruta_leible(r, root, workspace) for r in rutas)
        if a.startswith('-'):
            if nombre in con_valor:
                if nombre in ('-e', '--regexp'):
                    patron = True
                i += 1 if '=' in a or (len(a) > 2 and not a.startswith('--')) else 2
                continue
            if a.startswith('--'):
                if '=' in a or not a[2:].replace('-', '').isalpha():
                    return False
            elif not a[1:].isalnum():
                return False
            # Banderas cortas agrupadas (-rn, -inw): ninguna lleva valor salvo la última.
            if not a.startswith('--') and any('-' + c in prohibidas or '-' + c in ('-f',) for c in a[1:]):
                return False
            i += 1
            continue
        if not patron:
            patron = True
        elif not _ruta_leible(a, root, workspace):
            return False
        i += 1
    return patron or '--files' in args


def _find(args, root, workspace):
    i = 0
    while i < len(args) and args[i] in FIND_OPCIONES:
        i += 1
    while i < len(args) and not args[i].startswith(('-', '(', '!')):
        if not _ruta_leible(args[i], root, workspace):
            return False
        i += 1
    return not any(a in FIND_PROHIBIDAS for a in args[i:])


def _git(args):
    if not args or args[0] not in GIT_LECTURA:
        return False  # git -C, git -c… delante del subcomando tampoco
    sub, resto = args[0], args[1:]
    for a in resto:
        nombre = a.split('=', 1)[0]
        if nombre in GIT_PROHIBIDAS or (nombre.startswith('-') and not nombre.startswith('--')
                                         and 'O' in nombre[1:] and sub == 'grep'):
            return False
    if sub in GIT_SOLO_LISTAR:
        permitidas = GIT_SOLO_LISTAR[sub]
        # `git tag -l 'v1.*'`: tras -l, un nombre suelto es un patrón, no una etiqueta nueva.
        patrones = sub == 'tag' and bool(resto) and resto[0] in ('-l', '--list')
        return all(a.split('=', 1)[0] in permitidas or (sub == 'tag' and a[:2] == '-n' and a[2:].isdigit())
                   or (patrones and not a.startswith('-')) for a in resto)
    return True


def solo_lectura(argv, root, folder=None):
    """folder: carpeta absoluta de la orden (cwd); sus rutas relativas se miden desde ahí."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return False
    if any('\0' in a for a in argv):
        return False
    workspace = Path(root).resolve()
    executable = shutil.which(argv[0])
    if not executable or Path(executable).resolve().is_relative_to(workspace):
        return False
    root = Path(folder).resolve() if folder else workspace
    if root != workspace and not root.is_relative_to(workspace):
        return False
    if '/' in argv[0]:
        return False  # una ruta al programa: puede ser otro con el mismo nombre
    programa, args = argv[0], argv[1:]
    if programa in ('pwd', 'true') and not args:
        return True
    if programa in ('basename', 'dirname', 'which', 'realpath', 'date', 'echo'):
        return programa != 'date' or all(a.startswith('+') or a in ('-u', '-R') for a in args)
    if programa == 'git':
        return _git(args)
    if programa in OPCIONES:
        return _archivos(programa, args, root, workspace)
    if programa == 'ls':
        return _ls(args, root, workspace)
    if programa == 'grep':
        return _busqueda(args, root, workspace, GREP_CON_VALOR, {'-f', '--file'})
    if programa == 'rg':
        return _busqueda(args, root, workspace, RG_CON_VALOR, RG_PROHIBIDAS)
    if programa == 'find':
        return _find(args, root, workspace)
    if programa == 'sed':
        # sed -n '1,20p' archivo: sólo imprime líneas.
        from .ordenes import _sed_que_solo_imprime
        return _sed_que_solo_imprime(args) and all(
            _ruta_leible(a, root, workspace) for a in args if not a.startswith('-') and not IMPRIME.fullmatch(a))
    return False


MOTIVO_TERMINAL = {
    'peligroso': 'orden peligrosa',
    'red': 'necesita red',
    'salir_sandbox': 'salir del sandbox',
    'reintento': 'reintento sin sandbox',
    'regla': 'regla del usuario',
    'modo': 'el modo pide permiso para cada cambio',
    'edicion': 'edición de archivos (modo Manual)',
    'aprendizaje': 'guardar lo aprendido (memoria o habilidad)',
    'contenido_externo': 'acción tras leer contenido externo',
}


def describe(args, command):
    """Texto de la solicitud: motivo, orden, propósito y, si los hay, justificación y detalle."""
    lines = []
    motivos = [m for m in args.get('motivos') or [args.get('motivo')] if m in MOTIVO_TERMINAL]
    if motivos:
        lines.append('AlfalfaCode · Permiso: ' + ' + '.join(MOTIVO_TERMINAL[m] for m in motivos))
    mcp = args.get('mcp') if isinstance(args.get('mcp'), dict) else None
    if mcp:
        # No es un motivo on-request: llamada a una herramienta MCP (mcp_cliente). Servidor,
        # herramienta y argumentos estructurados, ya redactados por el gestor.
        lines.append('AlfalfaCode · Permiso: herramienta MCP')
        lines.append('Servidor: ' + str(mcp.get('servidor')) + ' · Herramienta: ' + str(mcp.get('herramienta'))
                     + ' · Llamada: ' + str(mcp.get('llamada')))
        lines.append('Argumentos: ' + json.dumps(mcp.get('argumentos'), ensure_ascii=False, sort_keys=True)[:2000])
        return '\n'.join(lines)
    lines.append(('Orden: ' if motivos else 'AlfalfaCode · Orden: ') + command)
    if args.get('cwd') not in (None, '', '.'):
        lines.append('Carpeta: ' + str(args['cwd']))
    lines.append('Propósito: ' + str(args['purpose']))
    if args.get('regla'):
        lines.append('Regla: ' + str(args['regla']))
    if args.get('justificacion'):
        lines.append('Justificación: ' + str(args['justificacion']))
    if args.get('diff'):
        # Entero: lo que se aprueba es exactamente esto (el arnés rechaza antes lo que no cabe).
        lines.append(str(args['diff']).rstrip('\n'))
    if args.get('reintento_de'):
        lines.append('Reintento del intento ' + str(args['reintento_de']) + ': ' + str(args.get('sin_sandbox', '')))
    elif args.get('fallo_sandbox'):
        lines.append('Sandbox no disponible: ' + str(args['fallo_sandbox']))
    return '\n'.join(lines)


class TerminalApproval:
    def __init__(self, root, auto=False, *, stdin=None, stdout=None):
        self.root, self.auto = Path(root), auto
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.denied = 0

    async def __call__(self, args):
        from ..auditoria_equipo import marcar_respuesta

        command = shlex.join(args['argv'])
        print(describe(args, command), file=self.stdout, flush=True)
        # Ejecutar sin sandbox, y cualquier excepción on-request, nunca se concede en automático.
        unsandboxed = bool(args.get('sin_sandbox'))
        folder = self.root / args['cwd'] if isinstance(args.get('cwd'), str) and args['cwd'] not in ('', '.') else None
        if self.auto and not unsandboxed and not args.get('motivo') and solo_lectura(args['argv'], self.root, folder):
            marcar_respuesta('solo_lectura')
            print('AlfalfaCode · Lectura aprobada automáticamente.', file=self.stdout, flush=True)
            return
        # Never read redirected stdin; EOF is a denial, never implicit approval.
        accepted = session = always = False
        marcar_respuesta('sin_respuesta')
        site_scope = None
        if self.stdin.isatty() and self.stdout.isatty():
            question = ('¿Autorizar esta llamada tras leer contenido externo? [s/N] '
                        if args.get('motivo') == 'contenido_externo' else
                        'Llamada MCP: el servidor la ejecuta con sus propios permisos. ¿Enviarla? [s/N] '
                        if args.get('mcp') else
                        '¿Guardarlo? [s/N] ' if args.get('motivo') == 'aprendizaje' else
                        'SIN SANDBOX: el proceso tendrá todos los permisos del usuario. ¿Ejecutar sin sandbox? [s/N] '
                        if unsandboxed else
                        'Sandbox ' + nombre_del_sandbox() + ' con red: escribe sólo en la carpeta, $TMPDIR y '
                        + temporal_del_sistema() + '. ¿Ejecutar? [s/N] '
                        if args.get('red') else
                        'Sandbox ' + nombre_del_sandbox() + ': escribe sólo en la carpeta, $TMPDIR y '
                        + temporal_del_sistema() + '; sin red. ¿Ejecutar? [s/N] ')
            if args.get('para_sesion'):
                question = question.replace('[s/N] ', '[s/t/N] (t: también el resto de la sesión) ')
            if args.get('prefijo'):
                question = question.replace('/N] ', '/a/N] (a: no volver a preguntar por «'
                                            + shlex.join(args['prefijo']) + ' …») ', 1)
            if args.get('sitio'):
                question = ('Sitio: [u] una visita, [t] este origen durante esta sesión, '
                            '[p] siempre en este proyecto, [N] rechazar: ')
            print(question, end='', file=self.stdout, flush=True)
            # A bounded line, no executor thread left blocked after cancellation.
            # The CLI installs signal handlers so Ctrl-C still cancels this wait.
            import asyncio
            loop = asyncio.get_running_loop()
            ready = loop.create_future()
            fd = self.stdin.fileno()
            loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
            try:
                await ready
                linea = self.stdin.readline(100)
                respuesta = linea.strip().casefold()
                marcar_respuesta('terminal' if linea else 'sin_respuesta')
                session = bool(args.get('para_sesion')) and respuesta in ('t', 'todo', 'sesion', 'sesión')
                always = bool(args.get('prefijo')) and respuesta in ('a', 'siempre')
                accepted = session or always or respuesta in ('s', 'si', 'sí')
                if args.get('sitio'):
                    site_scope = {'u': 'una_vez', 't': 'sesion', 'p': 'proyecto'}.get(respuesta)
                    accepted = site_scope is not None
            finally:
                loop.remove_reader(fd)
        if not accepted:
            self.denied += 1
            raise ValueError('Comando rechazado; no se ejecutó. No lo repitas ni reformules para eludir el rechazo.')
        return site_scope or ('siempre' if always else 'sesion' if session else None)
