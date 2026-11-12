"""Escrituras de memoria y habilidades fuera del sandbox de órdenes.

Estas escrituras las hace el propio proceso del arnés, no una orden dentro de
Seatbelt, así que cada ruta se valida aquí: relativa, sin `..` ni componentes
ocultos, sin enlaces simbólicos por el camino, dentro de su raíz y nunca dentro de
un `.git` (sin distinguir mayúsculas: APFS no las distingue).

Escribir, leer y borrar abren la ruta componente a componente desde la raíz con
`O_NOFOLLOW | O_DIRECTORY` y `dir_fd`: un enlace que aparezca entre la comprobación y
la escritura no se sigue. Se escribe en un temporal de la misma carpeta y se renombra
con `os.replace(..., src_dir_fd, dst_dir_fd)`.
"""
from __future__ import annotations

import errno
import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath

MAX_ARCHIVO = 256 * 1024
NOMBRE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
# Archivos de apoyo de una habilidad: sólo texto en references/ (como los «knowledge
# skills» de Hermes), con nombres ASCII que no se confunden con SKILL.md.
APOYO = re.compile(r'^references/[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(?:md|txt)$')
_CARPETA = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class RutaInvalida(ValueError):
    pass


def nombre_valido(nombre):
    if not isinstance(nombre, str) or not NOMBRE.fullmatch(nombre):
        raise RutaInvalida('El nombre de una habilidad es de 1 a 64 letras minúsculas, cifras o guiones '
                           '(«probar-swift»), y empieza por letra o cifra.')
    return nombre


def clave_de_ruta(relativa):
    """Cómo la ve APFS: sin distinguir mayúsculas y en NFC («SKILL.md» y «skill.md» son el mismo)."""
    return unicodedata.normalize('NFC', str(relativa)).casefold()


def relativa_valida(relativa):
    """Una ruta de apoyo limpia (`references/api.md`), o RutaInvalida."""
    if not isinstance(relativa, str) or not relativa or len(relativa) > 200:
        raise RutaInvalida('Ruta de archivo inválida.')
    if any(unicodedata.category(c) in ('Cc', 'Cf', 'Co', 'Cs', 'Zl', 'Zp') for c in relativa):
        raise RutaInvalida('La ruta tiene caracteres de control o de dirección del texto.')
    pura = PurePosixPath(relativa)
    if pura.is_absolute() or relativa.startswith('~') or '\\' in relativa:
        raise RutaInvalida('La ruta tiene que ser relativa a la carpeta de la habilidad.')
    if any(p in ('', '.', '..') or p.startswith('.') for p in pura.parts):
        raise RutaInvalida('La ruta no puede tener «..», «.» ni carpetas o archivos ocultos (como .git).')
    if not APOYO.fullmatch(relativa):
        raise RutaInvalida('Los archivos de apoyo van en references/ con nombre ASCII y extensión .md o .txt '
                           '(«references/api.md»).')
    return pura


def _partes(raiz, destino):
    """Los componentes de `destino` bajo `raiz`, validados (sin «..» ni .git)."""
    try:
        relativa = Path(destino).relative_to(Path(raiz))
    except ValueError:
        raise RutaInvalida('La ruta sale de su carpeta.') from None
    partes = relativa.parts
    if any(p in ('', '.', '..') for p in partes):
        raise RutaInvalida('La ruta sale de su carpeta.')
    if any(clave_de_ruta(p) == '.git' for p in partes):
        raise RutaInvalida('Nunca se escribe dentro de .git.')
    return partes


def sin_enlaces(raiz, destino):
    """Comprobación previa (para planear): ni la raíz ni nada entre ella y `destino` es un
    enlace, ni sale de ella. Las escrituras vuelven a comprobarlo al abrir cada componente."""
    raiz, destino = Path(raiz), Path(destino)
    _partes(raiz, destino)
    if any(clave_de_ruta(p) == '.git' for p in destino.parts):
        raise RutaInvalida('Nunca se escribe dentro de .git.')
    actual = destino
    while True:
        if actual.is_symlink():
            raise RutaInvalida('La ruta pasa por un enlace simbólico: ' + str(actual))
        if actual == raiz or actual == actual.parent:
            break
        actual = actual.parent
    real_raiz = raiz.resolve(strict=False)
    real = destino.resolve(strict=False)
    if real != real_raiz and not real.is_relative_to(real_raiz):
        raise RutaInvalida('La ruta sale de su carpeta.')
    return destino


def _abrir_raiz(raiz, crear, modo):
    raiz = Path(raiz)
    if raiz.is_symlink():
        raise RutaInvalida('La carpeta raíz no puede ser un enlace simbólico: ' + str(raiz))
    if crear:
        raiz.mkdir(mode=modo, parents=True, exist_ok=True)
    try:
        return os.open(raiz, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise RutaInvalida('No se puede abrir ' + str(raiz) + ': ' + exc.strerror) from None


def abrir_carpeta(raiz, partes, crear=False, modo=0o700):
    """Descriptor de la carpeta `raiz/partes…`, abierta sin seguir enlaces. El llamador lo cierra."""
    fd = _abrir_raiz(raiz, crear, modo)
    try:
        for parte in partes:
            if crear:
                try:
                    os.mkdir(parte, modo, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                siguiente = os.open(parte, _CARPETA, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise RutaInvalida('La ruta pasa por un enlace simbólico o no es una carpeta: ' + parte) from None
                raise
            os.close(fd)
            fd = siguiente
        return fd
    except BaseException:
        os.close(fd)
        raise


def preparar_carpeta(raiz, carpeta, modo=0o700):
    """Crea `carpeta` (dentro de `raiz`) sin seguir enlaces; devuelve la ruta."""
    os.close(abrir_carpeta(raiz, _partes(raiz, carpeta), crear=True, modo=modo))
    return Path(carpeta)


def escribir(raiz, destino, texto, modo=0o644):
    """Escribe `texto` en `destino` (dentro de `raiz`) de forma atómica y sin seguir enlaces."""
    datos = texto.encode('utf-8')
    if len(datos) > MAX_ARCHIVO:
        raise RutaInvalida('El archivo pasa de 256 KB.')
    partes = _partes(raiz, destino)
    if not partes:
        raise RutaInvalida('Falta el nombre del archivo.')
    # Las del hogar, sólo para el usuario; las del proyecto, como el resto del repositorio.
    fd = abrir_carpeta(raiz, partes[:-1], crear=True, modo=0o700 if modo == 0o600 else 0o755)
    nombre = partes[-1]
    temporal = '.' + nombre + '.' + os.urandom(4).hex() + '.tmp'
    try:
        try:
            actual = os.stat(nombre, dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISREG(actual.st_mode) or stat.S_ISLNK(actual.st_mode)):
                raise RutaInvalida('Ya existe y no es un archivo: ' + str(destino))
        except FileNotFoundError:
            pass
        salida = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, modo, dir_fd=fd)
        try:
            with os.fdopen(salida, 'wb') as archivo:
                archivo.write(datos)
            # Reemplaza también un enlace con ese nombre: el enlace se sustituye, no se sigue.
            os.replace(temporal, nombre, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(temporal, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)
    return Path(destino)


def leer(raiz, ruta, defecto=None):
    """Texto de `ruta` si es un archivo normal dentro de `raiz`; `defecto` si no existe."""
    ruta = Path(ruta)
    partes = _partes(raiz, ruta)
    if not Path(raiz).exists():
        return defecto
    try:
        fd = abrir_carpeta(raiz, partes[:-1])
    except FileNotFoundError:
        return defecto
    try:
        try:
            entrada = os.open(partes[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return defecto
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise RutaInvalida('La ruta es un enlace simbólico: ' + str(ruta)) from None
            raise
        with os.fdopen(entrada, 'rb') as archivo:
            datos = os.fstat(archivo.fileno())
            if not stat.S_ISREG(datos.st_mode) or datos.st_size > MAX_ARCHIVO:
                raise RutaInvalida('No es un archivo normal o es demasiado grande: ' + str(ruta))
            return archivo.read().decode('utf-8')
    finally:
        os.close(fd)


def borrar_archivo(raiz, ruta):
    """Borra el archivo (o el enlace) `ruta` dentro de `raiz` sin seguir enlaces de carpeta."""
    partes = _partes(raiz, ruta)
    fd = abrir_carpeta(raiz, partes[:-1])
    try:
        os.unlink(partes[-1], dir_fd=fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(fd)


def mover_carpeta(raiz, carpeta, destino_raiz, destino):
    """Mueve `carpeta` (dentro de `raiz`) a `destino` (dentro de `destino_raiz`) sin seguir enlaces."""
    origen, llegada = _partes(raiz, carpeta), _partes(destino_raiz, destino)
    fd_origen = abrir_carpeta(raiz, origen[:-1])
    try:
        fd_destino = abrir_carpeta(destino_raiz, llegada[:-1], crear=True)
        try:
            # La carpeta en sí tampoco puede ser un enlace.
            os.close(os.open(origen[-1], _CARPETA, dir_fd=fd_origen))
            os.rename(origen[-1], llegada[-1], src_dir_fd=fd_origen, dst_dir_fd=fd_destino)
        finally:
            os.close(fd_destino)
    finally:
        os.close(fd_origen)


def escribir_privado(hogar, ruta, texto):
    """Un archivo del hogar de AlfalfaCode (sólo para el usuario), con la ruta absoluta dentro de él."""
    return escribir(hogar, ruta, texto, modo=0o600)
