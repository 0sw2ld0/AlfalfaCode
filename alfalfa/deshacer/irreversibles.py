"""Lo que las instantáneas no deshacen: lo que un turno hizo fuera del contenido de la carpeta.

Las instantáneas guardan los archivos de la carpeta de trabajo, no su `.git`, ni lo
instalado, ni lo que salió por la red. Este módulo lee las llamadas del agente de los
turnos que se deshacen y avisa, con nombre, de lo que se queda como está. Usa los
analizadores de los permisos: `peligrosos` para sacar las órdenes de un `bash -c`,
`ordenes.safe_command` para descartar lo que se queda dentro de la carpeta y
`ordenes._fuera_del_espacio` para las rutas de fuera.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath

from ..permisos import ordenes, peligrosos

PUBLICA = 'publicacion'
GIT = 'git'
INSTALA = 'instalacion'
FUERA = 'fuera_de_la_carpeta'
RED = 'red'
SISTEMA = 'sistema'
EXTERNA = 'herramienta_externa'
NO_CUBIERTO = 'no_cubierto'
SUBAGENTE = 'subagente'

TITULOS = {
    PUBLICA: 'Publicó fuera de tu Mac',
    GIT: 'Cambió el repositorio git (commits, ramas o remotos)',
    INSTALA: 'Instaló o actualizó paquetes',
    FUERA: 'Tocó archivos fuera de la carpeta',
    RED: 'Hizo llamadas de red',
    SISTEMA: 'Cambió el sistema',
    EXTERNA: 'Usó herramientas que actúan fuera (MCP o navegador)',
    SUBAGENTE: 'Un subagente ejecutó órdenes que no se pudieron leer',
    NO_CUBIERTO: 'Cambió archivos que las instantáneas no guardan (ignorados, grandes o en repositorios anidados)',
}

# git: lo que sólo lee no importa; lo que publica va aparte.
GIT_PUBLICA = {'push', 'send-email', 'request-pull'}
GIT_LEE = ordenes.SUBCOMANDOS['git'] - ordenes.GIT_ESCRITURA | {'help', 'version', 'remote', 'config'}
GIT_RED = {'fetch', 'pull', 'clone', 'ls-remote', 'submodule'}
PUBLICADORES = {('npm', 'publish'), ('pnpm', 'publish'), ('yarn', 'publish'), ('cargo', 'publish'),
                ('twine', 'upload'), ('docker', 'push'), ('gem', 'push'), ('pod', 'trunk'),
                ('gh', 'pr'), ('gh', 'release'), ('gh', 'issue'), ('gh', 'repo'), ('gh', 'api'),
                ('fly', 'deploy'), ('vercel', None), ('netlify', 'deploy'), ('heroku', None)}
INSTALADORES = {'brew': {'install', 'upgrade', 'uninstall', 'reinstall', 'tap', 'link', 'unlink'},
                'npm': {'install', 'i', 'ci', 'add', 'uninstall', 'update', 'link'},
                'pnpm': {'install', 'i', 'add', 'remove', 'update'},
                'yarn': {'install', 'add', 'remove', 'upgrade', None},
                'bun': {'install', 'add', 'remove'},
                'pip': {'install', 'uninstall'}, 'pip3': {'install', 'uninstall'},
                'uv': {'pip', 'add', 'remove', 'sync', 'tool'}, 'pipx': {'install', 'uninstall'},
                'gem': {'install', 'uninstall'}, 'cargo': {'install', 'add'}, 'go': {'install', 'get'},
                'apt': {'install', 'remove'}, 'apt-get': {'install', 'remove'}, 'port': {'install'},
                'poetry': {'add', 'install', 'remove'}, 'composer': {'install', 'require', 'update'},
                'pod': {'install', 'update'}, 'swift': {'package'}, 'npx': None, 'bunx': None}
DE_RED = {'curl', 'wget', 'ssh', 'scp', 'sftp', 'ftp', 'nc', 'ncat', 'telnet', 'http', 'https', 'rsync',
          'aws', 'gcloud', 'az', 'kubectl', 'ollama'}
BORRAN = {'rm', 'rmdir', 'unlink', 'mv', 'cp', 'ln', 'chmod', 'chown', 'touch', 'mkdir', 'tee', 'trash',
          'install', 'rsync', 'dd', 'truncate'}
DE_SISTEMA = {'sudo', 'launchctl', 'defaults', 'systemsetup', 'networksetup', 'pmset', 'crontab', 'osascript',
              'security', 'killall', 'shutdown', 'reboot', 'diskutil', 'tccutil', 'csrutil'}


def _ordenes(argv):
    """Las órdenes literales de `argv` (desenvuelve `bash -c`); [argv] si no es una shell."""
    guion = peligrosos.extract_shell_script(argv)
    if guion is None or isinstance(guion, peligrosos._Dynamic):
        return [argv]
    try:
        literales = peligrosos.parse_literal_commands(guion)
    except peligrosos._Unparseable:
        return [argv]
    salida = []
    for orden in literales:
        if orden:
            salida.extend(_ordenes([str(p) for p in orden]))
    return salida or [argv]


def _sin_entorno(partes):
    """Quita `VAR=valor`, `env …` y `command`/`exec`/`time` del principio."""
    i = 0
    while i < len(partes):
        palabra = partes[i]
        if '=' in palabra and not palabra.startswith('-') and palabra.split('=', 1)[0].isidentifier():
            i += 1
        elif PurePosixPath(palabra).name in ('env', 'command', 'exec', 'time', 'nohup', 'xargs'):
            i += 1
            while i < len(partes) and partes[i].startswith('-'):
                i += 1
        else:
            break
    return partes[i:]


def clasificar(argv, cwd):
    """[(categoría, detalle)] de una orden: lo que no se deshace con las instantáneas."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        return []
    # Lo que los permisos aprobarían sin preguntar lee o escribe sólo dentro de la carpeta:
    # no sale por la red ni toca fuera. Sí puede instalar, publicar o tocar el .git.
    try:
        dentro = ordenes.safe_command(list(argv), str(cwd))[0]
    except Exception:  # noqa: BLE001 — un analizador que falla no quita el aviso
        dentro = False
    hallado = []
    for orden in _ordenes(list(argv)):
        partes = _sin_entorno([str(p) for p in orden])
        if not partes:
            continue
        programa = PurePosixPath(partes[0]).name
        resto = partes[1:]
        sub = next((a for a in resto if not a.startswith('-')), None)
        texto = ' '.join(partes)[:200]
        if programa in DE_SISTEMA:
            hallado.append((SISTEMA, texto))
            if programa == 'sudo' and resto:
                hallado.extend(par for par in clasificar(resto, cwd) if par[0] != SISTEMA)
            continue
        if programa == 'git':
            sub = _subcomando_de_git(resto)
            if sub in GIT_PUBLICA:
                hallado.append((PUBLICA, texto))
            elif sub in GIT_RED:
                hallado.append((RED, texto))
                hallado.append((GIT, texto))
            elif sub and sub not in GIT_LEE:
                hallado.append((GIT, texto))
            elif sub in ('remote', 'config') and len([a for a in resto if not a.startswith('-')]) > 2:
                hallado.append((GIT, texto))
            continue
        if (programa, sub) in PUBLICADORES or (programa, None) in PUBLICADORES:
            hallado.append((PUBLICA, texto))
            continue
        if programa in INSTALADORES:
            subs = INSTALADORES[programa]
            if subs is None or sub in subs or (None in subs and sub is None):
                hallado.append((INSTALA, texto))
                continue
        if dentro:
            continue
        if programa in DE_RED:
            hallado.append((RED, texto))
        if programa in BORRAN or programa in DE_RED:
            if any(ordenes._fuera_del_espacio(a, str(cwd)) for a in resto if not _es_remoto(a)):
                hallado.append((FUERA, texto))
    return hallado


def _subcomando_de_git(resto):
    i = 0
    while i < len(resto):
        if resto[i] in ('-C', '-c', '--git-dir', '--work-tree', '--namespace'):
            i += 2
        elif resto[i].startswith('-'):
            i += 1
        else:
            return resto[i]
    return None


def _es_remoto(argumento):
    """`servidor:ruta` (scp, rsync) no es una ruta local."""
    cabeza = argumento.split('/', 1)[0]
    return ':' in cabeza and not argumento.startswith(('/', '.', '~'))


def _argumentos(funcion):
    valor = funcion.get('arguments')
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return {}
    return valor if isinstance(valor, dict) else {}


def _clasificar_llamada(nombre, argumentos, cwd):
    from ..navegador import ESCRITURA as NAVEGADOR_ESCRIBE
    if nombre in ('run_command', 'run_checks'):
        return clasificar(argumentos.get('argv'), cwd)
    if nombre.startswith('mcp__') or nombre in NAVEGADOR_ESCRIBE:
        return [(EXTERNA, nombre)]
    return []


def _agrupar(encontrados):
    por_tipo = {}
    for tipo, texto in encontrados:
        lista = por_tipo.setdefault(tipo, [])
        if texto not in lista:
            lista.append(texto)
    return [{'tipo': tipo, 'titulo': TITULOS[tipo], 'ordenes': textos[:8]}
            for tipo, textos in sorted(por_tipo.items(), key=lambda par: list(TITULOS).index(par[0]))]


def de_mensajes(mensajes, cwd):
    """Avisos de los mensajes del agente: [{tipo, titulo, ordenes: [texto]}], sin repetir."""
    encontrados = []
    for mensaje in mensajes or []:
        if not isinstance(mensaje, dict) or mensaje.get('role') != 'assistant':
            continue
        for llamada in mensaje.get('tool_calls') or []:
            funcion = llamada.get('function') if isinstance(llamada, dict) else None
            if isinstance(funcion, dict) and isinstance(funcion.get('name'), str):
                encontrados += _clasificar_llamada(funcion['name'], _argumentos(funcion), cwd)
    return _agrupar(encontrados)


def de_subagentes(carpeta, desde, cwd):
    """Avisos de las órdenes de los subagentes desde `desde` (sus registros `subagentes/*.jsonl`).

    Cada paso empieza con «Inicio NOMBRE · ARGUMENTOS»; si los argumentos no se pueden leer
    (vienen recortados), una orden queda como orden sin clasificar."""
    encontrados = []
    carpeta = Path(carpeta)
    if not carpeta.is_dir() or carpeta.is_symlink():
        return []
    for registro in sorted(carpeta.glob('*.jsonl')):
        try:
            lineas = registro.read_text(errors='replace').splitlines()
        except OSError:
            continue
        for linea in lineas:
            try:
                fila = json.loads(linea)
            except ValueError:
                continue
            texto = fila.get('text') if isinstance(fila, dict) else None
            if (fila.get('role') != 'tool' or not isinstance(texto, str) or not texto.startswith('Inicio ')
                    or (fila.get('time') or 0) < desde):
                continue
            nombre, _, resto = texto[len('Inicio '):].partition(' · ')
            try:
                argumentos = ast.literal_eval(resto)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                argumentos = None
            if not isinstance(argumentos, dict):
                if nombre in ('run_command', 'run_checks'):
                    encontrados.append((SUBAGENTE, resto[:150]))
                continue
            encontrados += _clasificar_llamada(nombre, argumentos, cwd)
    return _agrupar(encontrados)


def no_cubiertos(filas):
    """El aviso de los archivos que cambiaron y no guardan las instantáneas."""
    return {'tipo': NO_CUBIERTO, 'titulo': TITULOS[NO_CUBIERTO], 'ordenes': [f['ruta'] for f in filas[:8]]}


def juntar(*listas):
    """Une avisos de varias fuentes sin repetir tipos ni órdenes."""
    encontrados = [(aviso['tipo'], texto) for lista in listas for aviso in lista for texto in aviso['ordenes']]
    return _agrupar(encontrados)
