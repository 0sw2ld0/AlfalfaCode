"""Estado e instalación explícita de Chromium, fuera de las herramientas del agente."""
import json
import subprocess
import sys
from pathlib import Path

from .aprendizaje.rutas import escribir_privado
from .cuybrowser_proceso import _private, clave_proyecto, ejecutable_cuybrowser, entorno_cuybrowser

PREFERENCIA = 'navegador-cli.json'
SIN_CHROMIUM = 'No se encontró ningún navegador compatible'


def _resultado(estado, *, origen=None, navegador=None, mensaje=None):
    return dict(tipo='cuybrowser_preparacion', estado=estado, origen=origen, navegador=navegador, mensaje=mensaje)


def _consultar(home, origen):
    try:
        root = Path.cwd().resolve()
        binario, fuente = ejecutable_cuybrowser(root, origen=origen)
        if binario is None:
            mensaje = 'CuyBrowser no está disponible en esta instalación.'
            if origen == 'incluido':
                mensaje = 'Esta app no trae CuyBrowser. Puedes elegir PATH en Ajustes.'
            return None, _resultado('sin_cuybrowser', mensaje=mensaje)
        perfil = home / 'cuybrowser' / clave_proyecto(root)
        env = entorno_cuybrowser(home, perfil, root=root)
        salida = subprocess.run([binario, 'browsers'], cwd='/', env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace', timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        mensaje = str(error) if isinstance(error, ValueError) else 'No se pudo comprobar CuyBrowser.'
        return None, _resultado('error', mensaje=mensaje)
    if salida.returncode != 0:
        if SIN_CHROMIUM in salida.stderr or 'Chromium no está instalado' in salida.stderr:
            return binario, _resultado('sin_chromium', origen=fuente)
        return None, _resultado('error', origen=fuente, mensaje='CuyBrowser no pudo detectar un navegador compatible.')
    for linea in salida.stdout.splitlines():
        if linea.startswith('* '):
            elegido = linea[2:].partition(': ')[0]
            nombre = Path(elegido).name if elegido.startswith('/') else elegido
            return binario, _resultado('disponible', origen=fuente, navegador=nombre[:120])
    return None, _resultado('error', origen=fuente, mensaje='CuyBrowser no informó qué navegador eligió.')


def _confirmar(pregunta):
    if not sys.stdin.isatty():
        return False
    try:
        return input(pregunta + ' [s/N] ').strip().casefold() in ('s', 'sí', 'si')
    except EOFError:
        return False


def _descargar(home, origen, *, confirmada=False):
    binario, estado = _consultar(home, origen)
    if estado['estado'] != 'sin_chromium':
        return estado
    if not confirmada and not _confirmar('¿Descargar Chromium de Playwright (unos 190 MB)?'):
        return _resultado('sin_chromium', origen=estado['origen'], mensaje='Descarga cancelada.')
    try:
        env = entorno_cuybrowser(home, root=Path.cwd().resolve())
        if env['PLAYWRIGHT_BROWSERS_PATH'] == str(home / 'chromium-cuybrowser'):
            _private(_private(home) / 'chromium-cuybrowser')
        descarga = subprocess.run([binario, 'install-browser', '--yes'], cwd='/', env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        descarga = None
    if descarga is None or descarga.returncode != 0:
        return _resultado('error', origen=estado['origen'], mensaje='No se completó la descarga de Chromium.')
    return _consultar(home, origen)[1]


def _preferencia(home):
    try:
        ruta = home / PREFERENCIA
        if not ruta.is_symlink() and ruta.is_file() and ruta.stat().st_size <= 256:
            valor = json.loads(ruta.read_text(encoding='utf-8'))
            return valor if isinstance(valor, bool) else None
    except (OSError, ValueError):
        pass
    return None


def _guardar(home, activa):
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    escribir_privado(home, home / PREFERENCIA, json.dumps(activa) + '\n')


def preparar_terminal(args, home):
    """La selección explícita inicia la configuración; una preferencia guardada sólo rige la CLI."""
    preferida = _preferencia(home)
    if args.motor_navegador is None and preferida is True:
        args.motor_navegador = 'cuybrowser'
        return
    if args.motor_navegador != 'cuybrowser' or preferida is not None or not sys.stdin.isatty():
        return
    estado = _descargar(home, args.origen_cuybrowser)
    if estado['estado'] == 'disponible':
        pregunta = f"¿Activar CuyBrowser con {estado['navegador']} y un perfil propio en los próximos encargos?"
        _guardar(home, _confirmar(pregunta))
    elif estado['mensaje']:
        print(estado['mensaje'], file=sys.stderr)


def orden(args, home):
    if args.cuybrowser_cli:
        activa = args.cuybrowser_cli == 'activar'
        _guardar(home, activa)
        print('CuyBrowser activado para la terminal.' if activa else 'CuyBrowser desactivado para la terminal.')
        return 0
    if args.descargar_chromium:
        estado = _descargar(home, args.origen_cuybrowser, confirmada=args.confirmar_descarga_chromium)
    else:
        estado = _consultar(home, args.origen_cuybrowser)[1]
    if args.json:
        print(json.dumps(estado, ensure_ascii=False))
    elif estado['navegador']:
        print(f"CuyBrowser puede usar {estado['navegador']} con un perfil propio.")
    else:
        print(estado['mensaje'] or 'Falta Chromium. Usa alfalfa --descargar-chromium para instalarlo.')
    return int(estado['estado'] != 'disponible')
