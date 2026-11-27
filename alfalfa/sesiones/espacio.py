"""Lo que ocupan las sesiones guardadas y cómo liberarlo: `--espacio-sesiones` y `--liberar-sesiones`.

Dónde guarda AlfalfaCode una conversación:

- `$ALFALFA_HOME/sesiones/<id>/`: todo lo suyo. `task.json` (encargo, carpeta, turnos, gasto),
  `<id>.jsonl` (la transcripción que pinta la app), `native/<id>/` (el estado del modelo:
  `session.json`, `messages.jsonl`, `fotos.jsonl` con las fotos del espacio de trabajo, la
  evidencia y `imagenes/` con las imágenes adjuntas o generadas, que es lo que más pesa),
  `native/<id>-aN/` (sus subagentes), `procesos/` y `marcas.json` (título, fijada, archivada).
- `$ALFALFA_HOME/indice/sesiones.sqlite3`: el índice de `buscar_sesiones` (SQLite con FTS5 y
  `auto_vacuum = INCREMENTAL`), con una copia del texto de cada conversación.
- `$ALFALFA_HOME/instantaneas/<huella de la carpeta>/`: el git paralelo de deshacer, uno por
  carpeta de trabajo y compartido por sus sesiones; cada una tiene sus refs
  `refs/alfalfa/<id>/…` y su índice `indices/<id>`.

Liberar una sesión es:

1. Con su cerrojo (`lock`) tomado, moverla entera a la Papelera de macOS (`~/.Trash`), de donde
   se puede recuperar. Si otro proceso tiene el cerrojo (está trabajando, o abierta en la app
   entre turnos), no se toca.
2. Quitarla del índice de búsqueda y devolver al sistema las páginas libres
   (`PRAGMA incremental_vacuum`, FTS5 optimizado y WAL vaciado: `Indice.limpiar_disco`). No hace
   falta un `VACUUM` completo: el índice se creó con `auto_vacuum = INCREMENTAL`, así que el
   archivo encoge igual, sin reescribirlo entero ni bloquear las búsquedas de otras sesiones.
3. Borrar sus refs de instantáneas y su índice en el almacén de su carpeta y pasar `git gc
   --prune=now`, una vez por almacén: los objetos que sólo usaba ella se van. Esto no pasa por
   la Papelera (son copias de los archivos del proyecto para deshacer turnos, no la conversación):
   si se recupera la sesión, se puede leer, pero no deshacer sus turnos.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

IDENT = re.compile(r'[A-Za-z0-9_-]{1,80}')
# Lo que no cuenta como «usarla»: abrir el cerrojo o cambiarle el título desde la barra lateral.
NO_ES_USO = {'lock', 'marcas.json', 'marcas.lock', 'marcas.json.tmp'}
MAX_TITULO = 120
AVISO = ('Las sesiones movidas a la Papelera desaparecen de AlfalfaCode y ya no se podrán retomar ni '
         'bifurcar. Puedes recuperarlas desde la Papelera mientras no la vacíes, pero sin deshacer sus '
         'turnos: sus instantáneas se borran para liberar el espacio.')


def _sesiones(home):
    return Path(home) / 'sesiones'


def _medir(carpeta):
    """(bytes en disco, bytes de imágenes, última modificación) de una carpeta, sin seguir enlaces."""
    total = imagenes = 0
    ultima = 0.0
    for raiz, carpetas, archivos in os.walk(carpeta):
        carpetas[:] = [c for c in carpetas if not os.path.islink(os.path.join(raiz, c))]
        en_imagenes = os.path.basename(raiz) == 'imagenes'
        for nombre in archivos:
            try:
                info = os.lstat(os.path.join(raiz, nombre))
            except OSError:
                continue
            # Lo que ocupa de verdad (bloques), no el tamaño aparente: es lo que se libera.
            peso = getattr(info, 'st_blocks', 0) * 512 or info.st_size
            total += peso
            if en_imagenes:
                imagenes += peso
            if nombre not in NO_ES_USO:
                ultima = max(ultima, info.st_mtime)
    return total, imagenes, ultima


def en_uso(carpeta):
    """¿Tiene alguien el cerrojo de la sesión? El motor lo guarda mientras trabaja y, en la app, entre turnos."""
    try:
        fd = os.open(Path(carpeta) / 'lock', os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # sin poder mirarlo, mejor no tocarla
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _titulo(task, marcas):
    if isinstance(marcas.get('titulo'), str) and marcas['titulo'].strip():
        return marcas['titulo'].strip()[:MAX_TITULO]
    from ..adjuntos import sin_etiquetas
    objetivo = task.get('objective') if isinstance(task, dict) else None
    texto = sin_etiquetas(objetivo) if isinstance(objetivo, str) else ''
    # Sin el «# » de un encabezado de Markdown: el título de la barra lateral tampoco lo lleva.
    lineas = (linea.strip().lstrip('#').strip() for linea in texto.splitlines())
    linea = next((linea for linea in lineas if linea), '')
    return linea[:MAX_TITULO] or None


def _marcas(carpeta):
    try:
        ruta = carpeta / 'marcas.json'
        datos = {} if ruta.is_symlink() else json.loads(ruta.read_text())
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def fila(carpeta, ahora=None):
    """Una sesión guardada: id, título, carpeta, bytes, último uso, si está en uso…"""
    ahora = time.time() if ahora is None else ahora
    total, imagenes, ultima = _medir(carpeta)
    datos = dict(id=carpeta.name, titulo=None, cwd=None, estado='corrupta', bytes=total, imagenes=imagenes,
                 en_uso=en_uso(carpeta), fijada=False, archivada=False)
    try:
        ruta = carpeta / 'task.json'
        if ruta.is_symlink():
            raise ValueError()
        task = json.loads(ruta.read_text())
        if not isinstance(task, dict):
            raise ValueError()
    except (OSError, ValueError):
        task = None
    marcas = _marcas(carpeta)
    if task is not None:
        datos.update(cwd=task.get('cwd') if isinstance(task.get('cwd'), str) else None,
                     estado=task.get('status') if isinstance(task.get('status'), str) else 'corrupta',
                     titulo=_titulo(task, marcas), modelo=task.get('model'),
                     turnos=len(task.get('turnos') or []) if isinstance(task.get('turnos'), list) else 0)
        actualizada = task.get('updated_at')
        if isinstance(actualizada, (int, float)) and not isinstance(actualizada, bool):
            ultima = max(ultima, float(actualizada))
        for clave in ('bifurcada_de', 'continuada_de'):
            if isinstance(task.get(clave), dict) and isinstance(task[clave].get('sesion'), str):
                datos[clave] = task[clave]['sesion']
        if isinstance(task.get('padre'), str):
            datos['padre'] = task['padre']
    datos['fijada'] = marcas.get('fijada') is True
    datos['archivada'] = marcas.get('archivada') is True
    datos['ultimo_uso'] = ultima
    datos['dias_sin_usar'] = max(0, int((ahora - ultima) // 86400)) if ultima else None
    return datos


def listar(home, ordenar='tamano', ahora=None):
    """Todas las sesiones guardadas, de la que más ocupa a la que menos (o de la más vieja, con «uso»)."""
    base = _sesiones(home)
    filas = []
    if base.is_dir() and not base.is_symlink():
        for carpeta in base.iterdir():
            if not carpeta.is_dir() or carpeta.is_symlink() or not IDENT.fullmatch(carpeta.name):
                continue
            try:
                nombres = os.listdir(carpeta)
            except OSError:
                continue
            if nombres in ([], ['lock']):
                continue  # una sesión que arranca ahora mismo o que no llegó a nacer
            filas.append(fila(carpeta, ahora))
    if ordenar == 'uso':
        filas.sort(key=lambda f: (f['ultimo_uso'], f['id']))
    else:
        filas.sort(key=lambda f: (-f['bytes'], f['id']))
    return filas


def _peso(ruta):
    """Bytes en disco de un archivo o de una carpeta entera (sin seguir enlaces)."""
    if os.path.isfile(ruta) and not os.path.islink(ruta):
        info = os.lstat(ruta)
        return getattr(info, 'st_blocks', 0) * 512 or info.st_size
    total = 0
    for raiz, carpetas, archivos in os.walk(ruta):
        carpetas[:] = [c for c in carpetas if not os.path.islink(os.path.join(raiz, c))]
        for nombre in archivos:
            try:
                info = os.lstat(os.path.join(raiz, nombre))
            except OSError:
                continue
            total += getattr(info, 'st_blocks', 0) * 512 or info.st_size
    return total


def totales(home, filas):
    """Lo que ocupan las sesiones, el índice de búsqueda y las instantáneas de deshacer."""
    from ..busqueda_sesiones import ARCHIVO, CARPETA
    indice = Path(home) / CARPETA
    indice_bytes = sum(_peso(p) for p in indice.glob(ARCHIVO + '*')) if indice.is_dir() else 0
    return dict(sesiones=sum(f['bytes'] for f in filas), indice=indice_bytes,
                instantaneas=_peso(Path(home) / 'instantaneas'))


def elegir(filas, ids=None, dias=None, incluir_fijadas=False, ahora=None):
    """Las filas que se liberarían. Por id, las pedidas; con `dias`, las que llevan más de `dias` días
    sin usarse. Las fijadas sólo entran si se nombran o con `incluir_fijadas`; las que están en uso,
    nunca (salen en la respuesta como saltadas)."""
    ahora = time.time() if ahora is None else ahora
    elegidas = []
    pedidas = set(ids or [])
    for f in filas:
        if f['id'] in pedidas:
            elegidas.append(f)
        elif dias is not None and not pedidas and (incluir_fijadas or not f['fijada']):
            if f['ultimo_uso'] and ahora - f['ultimo_uso'] > dias * 86400:
                elegidas.append(f)
    return elegidas


def a_la_papelera(carpeta, ident):
    """Mueve la carpeta de la sesión a `~/.Trash` con su cerrojo tomado. Devuelve el destino.

    Con el cerrojo tomado hasta moverla: una sesión que arrancara justo entonces no acaba en la
    Papelera mientras trabaja (`ValueError` si ya está en ejecución)."""
    cerrojo = os.open(carpeta / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('La sesión está en ejecución; detenla antes de eliminarla.') from None
        papelera = Path.home() / '.Trash'
        papelera.mkdir(exist_ok=True)
        destino = papelera / ('alfalfa-' + ident)
        n = 1
        while destino.exists() or destino.is_symlink():
            n += 1
            destino = papelera / f'alfalfa-{ident}-{n}'
        carpeta.rename(destino)
        return destino
    finally:
        os.close(cerrojo)


def _quitar_del_indice(home, ids):
    """Las quita del índice de búsqueda en una transacción y devuelve (antes, después) en bytes."""
    from .. import busqueda_sesiones as busqueda
    ruta = busqueda.ruta_indice(home)
    if not ruta.exists() or not ids:
        return None
    carpeta = ruta.parent
    antes = sum(_peso(p) for p in carpeta.glob(busqueda.ARCHIVO + '*'))
    with busqueda.Indice(home) as indice:
        for ident in ids:
            indice.quitar(ident)
        indice.limpiar_disco()
    return dict(antes=antes, despues=sum(_peso(p) for p in carpeta.glob(busqueda.ARCHIVO + '*')))


def _olvidar_instantaneas(home, por_carpeta):
    """{cwd: [ids]} → borra sus refs e índices en cada almacén y compacta una vez por almacén."""
    from ..deshacer import instantaneas
    antes = despues = refs = 0
    for cwd, ids in por_carpeta.items():
        almacen = instantaneas.Almacen(home, cwd)
        if not (almacen.dir / 'HEAD').is_file():
            continue
        peso = almacen.tamano()
        try:
            with almacen.cerrojo():
                nombres = []
                for ident in ids:
                    prefijo = 'refs/alfalfa/' + instantaneas._seguro(ident) + '/'
                    salida = almacen.git('for-each-ref', '--format=%(refname)', prefijo)
                    nombres += [n for n in salida.decode().splitlines() if n.startswith(prefijo)]
                    almacen._indice(ident).unlink(missing_ok=True)
                refs += almacen._borrar(nombres)
        except (instantaneas.SinInstantanea, OSError):
            continue  # otra instantánea ocupa el almacén: la limpieza de los 30 días las quitará igual
        antes += peso
        despues += almacen.tamano()
    return dict(antes=antes, despues=despues, refs=refs)


def liberar(home, elegidas):
    """Mueve a la Papelera las sesiones elegidas que no estén en uso y limpia el índice y las instantáneas."""
    base = _sesiones(home)
    movidas, saltadas = [], []
    por_carpeta = {}
    for f in elegidas:
        ident = f['id']
        carpeta = base / ident
        if not IDENT.fullmatch(ident) or not carpeta.is_dir() or carpeta.is_symlink():
            saltadas.append(dict(id=ident, motivo='no_existe'))
            continue
        try:
            destino = a_la_papelera(carpeta, ident)
        except ValueError:
            saltadas.append(dict(id=ident, motivo='en_uso'))
            continue
        except OSError as exc:
            saltadas.append(dict(id=ident, motivo='error', error=str(exc)[:300]))
            continue
        movidas.append(dict(id=ident, titulo=f.get('titulo'), bytes=f['bytes'], papelera=str(destino)))
        if f.get('cwd'):
            por_carpeta.setdefault(f['cwd'], []).append(ident)
    ids = [m['id'] for m in movidas]
    resultado = dict(movidas=movidas, saltadas=saltadas, liberado=sum(m['bytes'] for m in movidas))
    try:
        resultado['indice'] = _quitar_del_indice(home, ids)
    except (OSError, ValueError, sqlite3.Error):
        resultado['indice'] = None  # la próxima actualización del índice las quita igual: ya no están en disco
    resultado['instantaneas'] = _olvidar_instantaneas(home, por_carpeta)
    return resultado


# MARK: - La orden

def tamano_legible(n):
    for unidad, valor in (('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if n >= valor:
            return f'{n / valor:,.1f} {unidad}'.replace(',', ' ').replace('.', ',')
    return f'{n} B'


def _cuando(f):
    dias = f.get('dias_sin_usar')
    if dias is None:
        return '—'
    return 'hoy' if dias == 0 else ('ayer' if dias == 1 else f'hace {dias} días')


def _imprimir(filas, salida):
    for f in filas:
        marca = ' [en uso]' if f['en_uso'] else (' [fijada]' if f['fijada'] else '')
        print(f"{f['id']}  {tamano_legible(f['bytes']):>9}  {_cuando(f):>14}  "
              f"{f.get('titulo') or '(sin título)'}{marca}", file=salida)
        print(f"{'':16}  {f.get('cwd') or '(sin carpeta)'}", file=salida)


def argumentos(parser, grupo):
    grupo.add_argument('--espacio-sesiones', action='store_true',
                       help='Las sesiones guardadas con lo que ocupa cada una, su último uso y su carpeta')
    grupo.add_argument('--liberar-sesiones', nargs='*', metavar='ID',
                       help='Mover a la Papelera esas sesiones (o, con --sin-usar-dias, las que no se usan) y '
                            'quitarlas del índice y de las instantáneas')
    parser.add_argument('--ordenar', choices=('tamano', 'uso'), default='tamano',
                        help='Con --espacio-sesiones: por tamaño (por defecto) o de la menos usada a la más')
    parser.add_argument('--sin-usar-dias', type=_dias, metavar='N',
                        help='Con --liberar-sesiones sin ids: las que llevan más de N días sin usarse')
    parser.add_argument('--incluir-fijadas', action='store_true',
                        help='Con --liberar-sesiones --sin-usar-dias: también las fijadas')
    parser.add_argument('--si', action='store_true',
                        help='Con --liberar-sesiones: no preguntar antes de moverlas a la Papelera')


def _dias(valor):
    import argparse
    try:
        numero = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError('se esperaba un número entero de días') from None
    if numero < 0:
        raise argparse.ArgumentTypeError('no puede ser negativo')
    return numero


def pedida(args):
    return bool(getattr(args, 'espacio_sesiones', False) or getattr(args, 'liberar_sesiones', None) is not None)


def orden(args, home, salida=None, entrada=None):
    """`--espacio-sesiones` y `--liberar-sesiones`; devuelve el código de salida."""
    from ..stdio import Transport
    salida = salida or sys.stdout
    entrada = entrada or sys.stdin
    transporte = Transport(salida) if args.json else None
    filas = listar(home, args.ordenar)
    if args.espacio_sesiones:
        if transporte:
            transporte.emit('espacio_sesiones', sesiones=filas, totales=totales(home, filas), nota=AVISO)
            return 0
        _imprimir(filas, salida)
        t = totales(home, filas)
        n = len(filas)
        print(f"{n} {'sesión' if n == 1 else 'sesiones'}: {tamano_legible(t['sesiones'])}. Índice de búsqueda: "
              f"{tamano_legible(t['indice'])}. Instantáneas de deshacer: {tamano_legible(t['instantaneas'])}.",
              file=salida)
        print('Para liberar: alfalfa --liberar-sesiones ID … o --liberar-sesiones --sin-usar-dias 30', file=salida)
        return 0
    ids = args.liberar_sesiones
    if not ids and args.sin_usar_dias is None:
        return _error(transporte, 'Di qué sesiones (sus ids) o cuántos días sin usarse con --sin-usar-dias N.', salida)
    if ids and args.sin_usar_dias is not None:
        return _error(transporte, 'Usa ids o --sin-usar-dias, no las dos cosas.', salida)
    conocidas = {f['id'] for f in filas}
    desconocidas = [i for i in ids if i not in conocidas]
    elegidas = elegir(filas, ids, args.sin_usar_dias, args.incluir_fijadas)
    en_uso_ya = [f for f in elegidas if f['en_uso']]
    candidatas = [f for f in elegidas if not f['en_uso']]
    saltadas = ([dict(id=i, motivo='no_existe') for i in desconocidas]
                + [dict(id=f['id'], motivo='en_uso') for f in en_uso_ya])
    if not args.si:
        if transporte:
            # La app pregunta ella misma; sin --si sólo enseña lo que se movería.
            transporte.emit('espacio_sesiones', sesiones=candidatas, totales=totales(home, filas), nota=AVISO,
                            vista_previa=True, saltadas=saltadas)
            return 0
        if not candidatas:
            print('No hay ninguna sesión que mover a la Papelera.', file=salida)
            _saltadas(saltadas, salida)
            return 0
        _imprimir(candidatas, salida)
        _saltadas(saltadas, salida)
        n = len(candidatas)
        print(f"\n{n} {'sesión' if n == 1 else 'sesiones'}, "
              f"{tamano_legible(sum(f['bytes'] for f in candidatas))}. {AVISO}", file=salida)
        if not entrada.isatty():
            print('Añade --si para confirmarlo sin preguntar.', file=salida)
            return 1
        print('¿Moverlas a la Papelera? [s/N] ', end='', file=salida, flush=True)
        if entrada.readline().strip().lower() not in ('s', 'si', 'sí', 'y', 'yes'):
            print('No se ha movido nada.', file=salida)
            return 1
    resultado = liberar(home, candidatas)
    resultado['saltadas'] = saltadas + resultado['saltadas']
    if transporte:
        transporte.emit('sesiones_liberadas', nota=AVISO, **resultado)
        return 0
    for m in resultado['movidas']:
        print(f"{m['id']}  a la Papelera ({tamano_legible(m['bytes'])})", file=salida)
    _saltadas(resultado['saltadas'], salida)
    texto = f"Se liberan {tamano_legible(resultado['liberado'])} de sesiones"
    if resultado.get('indice'):
        texto += f", {tamano_legible(max(0, resultado['indice']['antes'] - resultado['indice']['despues']))} del índice"
    inst = resultado['instantaneas']
    if inst['refs']:
        texto += f" y {tamano_legible(max(0, inst['antes'] - inst['despues']))} de instantáneas"
    print(texto + '. El espacio de las sesiones vuelve al vaciar la Papelera.', file=salida)
    return 0


def _saltadas(saltadas, salida):
    for s in saltadas:
        motivos = {'en_uso': 'en uso o abierta: no se toca', 'no_existe': 'no existe'}
        motivo = motivos.get(s['motivo'], s.get('error', ''))
        print(f"{s['id']}  {motivo}", file=salida)


def _error(transporte, texto, salida):
    if transporte:
        transporte.emit('error', texto=texto)
    else:
        print('AlfalfaCode · Error: ' + texto, file=sys.stderr)
    return 1
