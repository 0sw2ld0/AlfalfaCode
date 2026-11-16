"""Imágenes adjuntas a un mensaje del usuario, como en Codex.

Codex (codex-rs/protocol/src/models.rs, `local_image_content_items`) envuelve
cada imagen con `<image name=[Image #N] path="...">` … `</image>` para que el
modelo sepa a cuál se refiere el texto, y la reduce a 2048 px como mucho
(codex-rs/utils/image). Aquí el texto lleva esas mismas etiquetas, la imagen se
copia (reducida) a la carpeta de la sesión y, al llamar a Ollama, el mensaje
lleva sus bytes en base64 en el campo `images` de `/api/chat`.

En macOS mide, reduce y convierte `sips`. En Linux lo hace Pillow, que se instala
con AlfalfaCode sólo allí, en un proceso aparte aislado y sin red
(alfalfa.aisladas): sus decodificadores son código nativo que abre un archivo de
fuera.
"""
import base64
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_DIMENSION = 2048
MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGENES = 8
EXTENSIONES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif', '.tif', '.tiff', '.bmp'}
# Ollama decodifica PNG y JPEG en todos sus modelos de visión; el resto se convierte a PNG.
DIRECTAS = {'.png', '.jpg', '.jpeg'}
ETIQUETA = re.compile(r'<image name=\[Image #(\d+)\] path="([^"\n]+)">')
CIERRE = '</image>'
# Pillow en Linux: lo que se decodifica como mucho (una foto de 48 Mpx cabe; una bomba de descompresión, no),
# lo que puede ocupar la imagen que devuelve y cuánto puede tardar.
MAX_PIXELES = 50_000_000
MAX_SALIDA = 64 * 1024 * 1024
PLAZO = 60
# Corre en el proceso aislado: argv = ruta, formato (PNG o JPEG), lado máximo y píxeles máximos. Escribe por
# stdout la imagen reducida (sin ampliar nunca) y girada según su EXIF, o nada si ya sirve tal cual.
_PILLOW = r'''
import io, sys, warnings
from PIL import Image, ImageOps
ruta, formato, lado = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.MAX_IMAGE_PIXELS = int(sys.argv[4])
warnings.simplefilter('error', Image.DecompressionBombWarning)
imagen = Image.open(ruta)
if max(imagen.size) <= lado and imagen.format == formato:
    sys.exit(0)
imagen.draft(None, (lado, lado))
if imagen.mode in ('1', 'P') and max(imagen.size) > lado:
    imagen = imagen.convert('RGBA')
imagen.thumbnail((lado, lado), Image.Resampling.LANCZOS)
imagen = ImageOps.exif_transpose(imagen)
validos = ('L', 'RGB', 'CMYK') if formato == 'JPEG' else ('1', 'L', 'LA', 'I', 'I;16', 'P', 'RGB', 'RGBA')
if imagen.mode not in validos:
    imagen = imagen.convert('RGBA' if formato == 'PNG' and 'A' in imagen.mode else 'RGB')
salida = io.BytesIO()
opciones = {'quality': 90} if formato == 'JPEG' else {}
imagen.save(salida, formato, icc_profile=imagen.info.get('icc_profile'), **opciones)
sys.stdout.buffer.write(salida.getvalue())
'''


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
    if not shutil.which('sips') and sys.platform.startswith('linux'):
        return _preparar_con_pillow(origen, copia, extension)
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


def _preparar_con_pillow(origen, copia, extension):
    """Como con sips: si no se puede medir, una PNG o JPEG se copia tal cual; otro formato no se adjunta."""
    try:
        datos = _con_pillow(origen, 'JPEG' if extension in ('.jpg', '.jpeg') else 'PNG')
    except ImagenInvalida as exc:
        if extension != origen.suffix.lower():
            raise ImagenInvalida(f'No se pudo convertir {origen.name} a PNG: {exc}') from None
        datos = None
    temporal = copia.with_name(copia.name + '.tmp')
    if datos is None:
        shutil.copyfile(origen, temporal)
    else:
        temporal.write_bytes(datos)
    temporal.chmod(0o600)
    os.replace(temporal, copia)
    return copia


def _hay_pillow():
    return importlib.util.find_spec('PIL') is not None


def _con_pillow(origen, formato):
    """La imagen reducida o convertida a `formato`, o None si ya sirve; ImagenInvalida si no se pudo."""
    from .aisladas import Cortada, correr
    from .seatbelt import SandboxUnavailable
    if not _hay_pillow():
        raise ImagenInvalida('falta Pillow, que AlfalfaCode instala en Linux (pip install pillow)')
    try:
        codigo, salida, error = correr([sys.executable, '-I', '-c', _PILLOW, str(origen), formato,
                                        str(MAX_DIMENSION), str(MAX_PIXELES)], tope=MAX_SALIDA, plazo=PLAZO)
    except SandboxUnavailable as exc:
        raise ImagenInvalida('Pillow sólo corre en el sandbox y no arrancó (' + str(exc)[:300] + ')') from None
    except Cortada:
        raise ImagenInvalida('la imagen reducida pesa demasiado') from None
    except TimeoutError:
        raise ImagenInvalida('Pillow tardó demasiado en leerla') from None
    if codigo != 0:
        lineas = error.strip().splitlines()
        raise ImagenInvalida('Pillow no pudo leerla' + (': ' + lineas[-1][:200] if lineas else ''))
    return salida or None


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
