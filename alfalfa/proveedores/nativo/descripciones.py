"""Qué hace cada llamada a una herramienta, dicho para la persona.

La app junta las llamadas seguidas en un grupo («Ejecutó 3 comandos, leyó 2
archivos») y enseña una fila por llamada con su descripción. En las órdenes la
escribe el modelo en `description`, como en la herramienta Bash de Claude Code;
en lo demás se saca de los argumentos («Leer runtime.py»). Sin descripción del
modelo, una orden usa su `purpose` y, si tampoco hay, el propio comando.
"""

import re
import shlex
from pathlib import PurePosixPath

MAX_DESCRIPCION = 200
MAX_ORDEN = 2000

ORDENES = ('run_command', 'run_checks')


def _texto(valor, limite=MAX_DESCRIPCION):
    if not isinstance(valor, str):
        return ''
    valor = ' '.join(valor.split())
    return valor if len(valor) <= limite else valor[:limite - 1].rstrip() + '…'


def _nombre(ruta):
    """El último trozo de la ruta: «runtime.py» en vez de la ruta entera."""
    ruta = _texto(ruta, 500).rstrip('/')
    if not ruta or ruta == '.':
        return ''
    return PurePosixPath(ruta).name or ruta


def orden_legible(argv):
    """`["git", "status"]` → `git status`, con comillas sólo donde hacen falta."""
    if not isinstance(argv, list) or not argv:
        return ''
    texto = ' '.join(shlex.quote(str(a)) for a in argv)
    return texto if len(texto) <= MAX_ORDEN else texto[:MAX_ORDEN - 1] + '…'


def _archivos_del_parche(texto):
    if not isinstance(texto, str):
        return []
    return re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', texto, re.MULTILINE)


def descripcion(nombre, args):
    """Frase corta en español que dice qué hace la llamada."""
    args = args if isinstance(args, dict) else {}
    if nombre in ORDENES:
        propia = _texto(args.get('description')) or _texto(args.get('purpose'))
        if propia:
            return propia
        orden = orden_legible(args.get('argv'))
        return ('Comprobar con ' if nombre == 'run_checks' else 'Ejecutar ') + (orden or 'una orden')
    ruta = _nombre(args.get('path'))
    if nombre == 'read_file':
        return 'Leer ' + (ruta or 'un archivo')
    if nombre == 'list_files':
        return 'Ver los archivos de ' + ruta if ruta else 'Ver los archivos del proyecto'
    if nombre == 'search':
        consulta = _texto(args.get('query'), 80)
        return 'Buscar «' + consulta + '»' + (' en ' + ruta if ruta else '')
    if nombre == 'write_file':
        nuevo = args.get('expected_sha256') == 'NEW'
        return ('Crear ' if nuevo else 'Escribir ') + (ruta or 'un archivo')
    if nombre == 'edit_file':
        return 'Editar ' + (ruta or 'un archivo')
    if nombre == 'apply_patch':
        archivos = _archivos_del_parche(args.get('input'))
        if len(archivos) == 1:
            return 'Editar ' + _nombre(archivos[0])
        if archivos:
            return 'Editar ' + str(len(archivos)) + ' archivos'
        return 'Aplicar un parche'
    if nombre == 'inspect_image':
        return 'Mirar la imagen ' + ruta if ruta else 'Mirar una imagen'
    if nombre == 'update_plan':
        return 'Actualizar el plan'
    if nombre == 'web_search':
        return 'Buscar en la web «' + _texto(args.get('query'), 80) + '»'
    if nombre == 'read_web':
        return 'Leer la página ' + _texto(args.get('url'), 120)
    if nombre == 'command_status':
        proceso = _texto(args.get('process'), 40)
        if args.get('stop') is True:
            return 'Detener el proceso ' + proceso
        return 'Ver cómo va el proceso ' + proceso
    if nombre == 'app_list':
        return 'Ver las apps abiertas'
    if nombre == 'app_state':
        return 'Mirar la ventana de ' + _texto(args.get('app'), 80)
    if nombre == 'app_action':
        accion = _texto(args.get('action'), 30)
        objetivo = _texto(args.get('target'), 60)
        detalle = (' (' + accion + (' en «' + objetivo + '»' if objetivo else '') + ')') if accion else ''
        return 'Usar ' + _texto(args.get('app'), 80) + detalle
    if nombre == 'spawn_agent':
        return 'Encargar a un subagente: ' + (_texto(args.get('description'), 80) or 'una tarea')
    if nombre == 'wait_agent':
        return 'Esperar a los subagentes'
    if nombre == 'send_input':
        return 'Escribir al subagente ' + _texto(args.get('agent'), 20)
    if nombre == 'close_agent':
        return 'Cerrar el subagente ' + _texto(args.get('agent'), 20)
    if nombre == 'list_agents':
        return 'Ver los subagentes'
    if nombre == 'wrap_up':
        return 'Cerrar el turno'
    if nombre == 'propose_session':
        return 'Proponer una sesión aparte'
    if nombre == 'buscar_herramientas':
        return 'Cargar herramientas'
    if isinstance(nombre, str) and nombre.startswith('browser_'):
        return _del_navegador(nombre, args)
    if isinstance(nombre, str) and nombre.startswith('mcp__'):
        partes = nombre.split('__')
        if len(partes) >= 3:
            return 'Usar ' + partes[1] + ' · ' + '__'.join(partes[2:])
    return 'Usar ' + str(nombre)


def _del_navegador(nombre, args):
    """Las herramientas browser_* del navegador propio de la app (alfalfa/navegador.py)."""
    ref = _texto(args.get('ref'), 20)
    objetivo = _texto(args.get('target'), 60)
    sobre = '«' + objetivo + '»' if objetivo else ref
    if nombre == 'browser_navigate':
        accion = args.get('action') or 'open'
        if accion == 'back':
            return 'Volver a la página anterior'
        if accion == 'forward':
            return 'Ir a la página siguiente'
        if accion == 'reload':
            return 'Recargar la página'
        return 'Abrir ' + (_texto(args.get('url'), 120) or 'una página')
    if nombre == 'browser_read':
        return 'Leer el texto de la página' if args.get('mode') == 'text' else 'Leer la página'
    if nombre == 'browser_click':
        return 'Pulsar ' + (sobre or 'un elemento de la página')
    if nombre == 'browser_type':
        return 'Escribir en ' + (sobre or 'un campo de la página')
    if nombre == 'browser_scroll':
        return 'Desplazar la página hasta ' + ref if ref else 'Desplazar la página'
    if nombre == 'browser_screenshot':
        return 'Captura del navegador'
    if nombre == 'browser_eval':
        return 'Ejecutar JavaScript en la página'
    if nombre == 'browser_download':
        return 'Descargar ' + (_nombre(args.get('path')) or _texto(args.get('url'), 120) or 'un archivo')
    return 'Usar el navegador'


def datos_de_inicio(nombre, args):
    """Campo `herramienta` del evento que abre la llamada."""
    datos = {'nombre': str(nombre)[:200], 'fase': 'inicio', 'descripcion': descripcion(nombre, args)}
    # El archivo o la página: la app no cuenta dos veces el mismo al resumir el grupo.
    objetivo = args.get('path') or args.get('url') if isinstance(args, dict) else None
    if isinstance(objetivo, str) and objetivo.strip():
        datos['objetivo'] = objetivo[:500]
    if nombre == 'apply_patch' and isinstance(args, dict):
        # Un parche toca varios archivos: el resumen cuenta archivos, no parches.
        archivos = list(dict.fromkeys(a.strip()[:500] for a in _archivos_del_parche(args.get('input'))))[:100]
        if archivos:
            datos['objetivos'] = archivos
    if nombre in ORDENES and isinstance(args, dict):
        orden = orden_legible(args.get('argv'))
        if orden:
            datos['orden'] = orden
        if isinstance(args.get('cwd'), str) and args['cwd'].strip():
            datos['cwd'] = args['cwd'][:500]
    return datos


def datos_de_fin(nombre, resultado):
    """Campo `herramienta` del evento con el resultado de la llamada."""
    datos = {'nombre': str(nombre)[:200], 'fase': 'fin'}
    if isinstance(resultado, dict):
        if isinstance(resultado.get('exit_code'), int) and not isinstance(resultado.get('exit_code'), bool):
            datos['codigo'] = resultado['exit_code']
        if 'error' in resultado:
            datos['error'] = True
        # Cómo clasificó el motor el resultado: `rg` sin coincidencias buscadas sale con
        # código 1 y no es un fallo. La app pinta en rojo sólo lo que es `failure`.
        clasificacion = resultado.get('classification')
        if isinstance(clasificacion, dict):
            valores = {k: clasificacion[k][:100] for k in ('disposition', 'status')
                       if isinstance(clasificacion.get(k), str)}
            if valores:
                datos['clasificacion'] = valores
        # Los errores y avisos nuevos que vio el servidor de lenguaje tras una edición (alfalfa/diagnosticos):
        # la app los cuenta en la fila; no son una comprobación.
        informe = resultado.get('diagnosticos')
        if isinstance(informe, dict):
            resumen = {k: informe[k] for k in ('errores', 'avisos')
                       if isinstance(informe.get(k), int) and not isinstance(informe.get(k), bool)}
            if informe.get('sin_respuesta'):
                resumen['incompleto'] = True
            datos['diagnosticos'] = resumen
    return datos
