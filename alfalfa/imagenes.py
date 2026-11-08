"""Imágenes adjuntas a un mensaje del usuario, como en Codex.

Codex (codex-rs/protocol/src/models.rs, `local_image_content_items`) envuelve
cada imagen con `<image name=[Image #N] path="...">` … `</image>` para que el
modelo sepa a cuál se refiere el texto, y la reduce a 2048 px como mucho
(codex-rs/utils/image). Aquí el texto lleva esas mismas etiquetas, la imagen se
copia (reducida) a la carpeta de la sesión y, al llamar a Ollama, el mensaje
lleva sus bytes en base64 en el campo `images` de `/api/chat`.
"""
import base64
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

MAX_DIMENSION = 2048
MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGENES = 8
EXTENSIONES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif', '.tif', '.tiff', '.bmp'}
# Ollama decodifica PNG y JPEG en todos sus modelos de visión; el resto se convierte a PNG.
DIRECTAS = {'.png', '.jpg', '.jpeg'}
ETIQUETA = re.compile(r'<image name=\[Image #(\d+)\] path="([^"\n]+)">')
CIERRE = '</image>'


class ImagenInvalida(ValueError):
    """La imagen no existe, no es de un tipo admitido o es demasiado grande."""


def validar(ruta):
    """Ruta absoluta de una imagen legible; ImagenInvalida si no sirve."""
    path = Path(ruta).expanduser()
    if not path.is_absolute():
        raise ImagenInvalida(f'La ruta de la imagen tiene que ser absoluta: {ruta}')
    if path.suffix.lower() not in EXTENSIONES:
        raise ImagenInvalida(f'{path.name} no es una imagen admitida (PNG, JPEG, GIF, WebP, HEIC, TIFF o BMP).')
    if not path.is_file():
        raise ImagenInvalida(f'No existe la imagen {path}.')
    if path.stat().st_size > MAX_BYTES:
        raise ImagenInvalida(f'{path.name} pesa más de 20 MB.')
    return path.resolve()


def con_etiquetas(texto, rutas):
    """El texto del mensaje con una etiqueta por imagen delante, numeradas desde 1."""
    if len(rutas) > MAX_IMAGENES:
        raise ImagenInvalida(f'Como mucho {MAX_IMAGENES} imágenes por mensaje.')
    partes = [f'<image name=[Image #{n}] path="{validar(r)}">{CIERRE}' for n, r in enumerate(rutas, 1)]
    return '\n'.join(partes + [texto]) if partes else texto


def rutas_etiquetadas(texto):
    return [m.group(2) for m in ETIQUETA.finditer(texto or '')]


def preparar(ruta, destino):
    """Copia la imagen a `destino`, reducida a 2048 px y en PNG o JPEG. Devuelve la copia."""
    origen = validar(ruta)
    destino = Path(destino)
    destino.mkdir(mode=0o700, parents=True, exist_ok=True)
    resumen = hashlib.sha256(origen.read_bytes()).hexdigest()[:16]
    extension = origen.suffix.lower() if origen.suffix.lower() in DIRECTAS else '.png'
    copia = destino / (resumen + extension)
    if copia.exists():
        return copia
    orden = ['sips']
    if max(_dimensiones(origen)) > MAX_DIMENSION:
        orden += ['-Z', str(MAX_DIMENSION)]  # sólo reduce: -Z también ampliaría las pequeñas
    if extension == '.png' and origen.suffix.lower() != '.png':
        orden += ['-s', 'format', 'png']
    if orden == ['sips']:
        shutil.copyfile(origen, copia)
        copia.chmod(0o600)
        return copia
    try:
        r = subprocess.run(orden + [str(origen), '--out', str(copia)], capture_output=True, timeout=60)
        ok = r.returncode == 0 and copia.is_file()
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        if extension != origen.suffix.lower():
            raise ImagenInvalida(f'No se pudo convertir {origen.name} a PNG.')
        shutil.copyfile(origen, copia)
    copia.chmod(0o600)
    return copia


def _dimensiones(ruta):
    """(ancho, alto) según sips; (0, 0) si no se puede leer."""
    try:
        salida = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(ruta)],
                                capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return (0, 0)
    valores = re.findall(r'pixel(?:Width|Height):\s*(\d+)', salida)
    return tuple(int(v) for v in valores[:2]) if len(valores) >= 2 else (0, 0)


def base64_de(ruta):
    return base64.b64encode(Path(ruta).read_bytes()).decode('ascii')
