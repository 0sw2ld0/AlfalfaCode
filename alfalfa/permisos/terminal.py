"""Permisos de terminal: auto sólo concede una lista cerrada de lecturas.

No reutiliza safe_command: éste presupone un worktree desechable.
"""
import json
import shlex
import shutil
import sys
from pathlib import Path


def solo_lectura(argv, root, folder=None):
    """folder: carpeta absoluta de la orden (cwd); sus rutas relativas se miden desde ahí."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return False
    workspace = Path(root).resolve()
    executable = shutil.which(argv[0])
    if not executable or Path(executable).resolve().is_relative_to(workspace):
        return False
    root = Path(folder).resolve() if folder else workspace
    if root != workspace and not root.is_relative_to(workspace):
        return False
    if argv == ['pwd']:
        return True
    if argv in (['git', 'status'], ['git', 'status', '--short'], ['git', 'status', '--porcelain']):
        return True
    if argv[:4] == ['git', 'diff', '--no-ext-diff', '--no-textconv']:
        return all(a in ('--stat', '--check', '--name-only', '--cached') for a in argv[4:])
    if argv == ['rg', '--files']:
        return True
    # No options which can execute programs, follow links, or write output.
    if argv[0] not in ('cat', 'head', 'wc', 'ls'):
        return False
    options = {'cat': {'-n'}, 'head': set(), 'wc': {'-l', '-c', '-w'}, 'ls': {'-l', '-a', '-la'}}
    for value in argv[1:]:
        if value in options[argv[0]]:
            continue
        if value.startswith('-') or '\0' in value:
            return False
        path = root / value
        if '..' in path.parts or not path.resolve().is_relative_to(root):
            return False
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink() or parent.name.startswith('.'):
                return False
    return True


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
        accepted = session = False
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
                        'Sandbox Seatbelt con red: escribe sólo en la carpeta, $TMPDIR y /private/tmp. ¿Ejecutar? '
                        '[s/N] '
                        if args.get('red') else
                        'Sandbox Seatbelt: escribe sólo en la carpeta, $TMPDIR y /private/tmp; sin red. ¿Ejecutar? '
                        '[s/N] ')
            if args.get('para_sesion'):
                question = question.replace('[s/N] ', '[s/t/N] (t: también el resto de la sesión) ')
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
                accepted = session or respuesta in ('s', 'si', 'sí')
                if args.get('sitio'):
                    site_scope = {'u': 'una_vez', 't': 'sesion', 'p': 'proyecto'}.get(respuesta)
                    accepted = site_scope is not None
            finally:
                loop.remove_reader(fd)
        if not accepted:
            self.denied += 1
            raise ValueError('Comando rechazado; no se ejecutó. No lo repitas ni reformules para eludir el rechazo.')
        return site_scope or ('sesion' if session else None)
