"""Generación de imágenes con aprobación individual y sin reintentos."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import tempfile
from decimal import Decimal
from pathlib import Path

import httpx

from ... import ajustes, contenido_externo
from ...permisos import modos
from .. import configuracion
from . import imagenes_codex

MODELO = 'gpt-image-1.5'
URL = 'https://api.openai.com/v1/images/generations'
TARIFAS = 'https://platform.openai.com/docs/pricing'
MOTIVO = 'generar_imagen'
MAX_PROMPT = 4000
MAX_RESPUESTA = 32 * 1024 * 1024
CALIDADES = ('low', 'medium', 'high')
PRECIOS = {
    '1024x1024': ('0.009', '0.034', '0.133'),
    '1024x1536': ('0.013', '0.050', '0.200'),
    '1536x1024': ('0.013', '0.050', '0.200'),
}


PROVEEDORES = ('codex', 'openai', 'none')
PREFERENCIA = 'proveedor_imagen'


def configurar(preferencia=None):
    datos = configuracion.leer(para_cambiar=preferencia is not None)
    if preferencia is not None:
        if preferencia not in PROVEEDORES:
            raise ValueError('Proveedor de imágenes desconocido.')
        datos[PREFERENCIA] = preferencia
        configuracion.escribir(datos)
    elegido = datos.get(PREFERENCIA)
    if elegido not in PROVEEDORES:
        elegido = None
    proveedor = 'none'
    if elegido in (None, 'codex') and imagenes_codex.conectado():
        proveedor = 'codex'
    elif elegido == 'openai' and ajustes.openai_clave():
        proveedor = 'openai'
    return {'preferencia': elegido, 'proveedor': proveedor}


def restringida(runtime):
    return (runtime.task.get('review') or runtime.task.get('subagente_de')
            or modos.solo_lee(modos.modo_de(runtime.task)) or getattr(runtime.manager, 'read_only', False)
            # El costo de imágenes aún no está integrado en el presupuesto de equipo.
            or bool(getattr(runtime.manager, 'equipo', None)))


def destino(tools, path):
    if Path(path).is_absolute() or Path(path).suffix.lower() != '.png':
        raise ValueError('Indica una ruta relativa al proyecto con extensión .png.')
    archivo = tools.path(path, escritura=True)
    if archivo.exists() or not archivo.parent.is_dir():
        raise ValueError('El destino debe ser nuevo y su carpeta debe existir.')
    return archivo


def guardar_png(archivo, datos):
    # El enlace publica el PNG completo y falla si alguien creó el destino mientras tanto.
    with tempfile.NamedTemporaryFile(prefix='.alfalfa-', dir=archivo.parent) as temporal:
        temporal.write(datos)
        temporal.flush()
        os.link(temporal.name, archivo)


async def solicitar(clave, payload):
    headers = {'Authorization': ' '.join(('Bearer', clave))}
    timeout = httpx.Timeout(230, connect=10)
    try:
        async with asyncio.timeout(240), httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            async with client.stream('POST', URL, json=payload, headers=headers) as r:
                if r.status_code != 200:
                    mensaje = {
                        400: 'solicitud no admitida o moderada', 401: 'clave inválida',
                        403: 'revisa permisos o verificación de la organización',
                        429: 'límite de uso o cuota',
                    }.get(r.status_code, 'solicitud no completada; el cargo no se pudo confirmar')
                    raise ValueError(f'OpenAI HTTP {r.status_code}: {mensaje}. No se reintentó.')
                cuerpo = bytearray()
                async for bloque in r.aiter_bytes(chunk_size=65536):
                    if len(cuerpo) + len(bloque) > MAX_RESPUESTA:
                        raise ValueError('La respuesta supera el tamaño permitido; OpenAI pudo cobrar la generación.')
                    cuerpo.extend(bloque)
                return json.loads(cuerpo)
    except (TimeoutError, httpx.HTTPError) as exc:
        raise ValueError('No se pudo confirmar la generación. OpenAI pudo cobrarla; no se reintentó.') from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError('Respuesta de OpenAI inválida. OpenAI pudo cobrar la generación.') from exc


def datos_png(respuesta):
    try:
        datos = base64.b64decode(respuesta['data'][0]['b64_json'], validate=True)
        return datos, dimensiones_png(datos)
    except (KeyError, IndexError, TypeError, ValueError, struct.error) as exc:
        raise ValueError('OpenAI no devolvió un PNG válido; pudo cobrar la generación.') from exc


def dimensiones_png(datos):
    if len(datos) < 33 or not datos.startswith(b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR'):
        raise ValueError('El servicio no devolvió un PNG válido.')
    width, height = struct.unpack('>II', datos[16:24])
    if not width or not height:
        raise ValueError('El PNG devuelto tiene dimensiones inválidas.')
    return width, height


def consumo(respuesta, salida):
    uso = respuesta.get('usage')
    if not isinstance(uso, dict):
        uso = {}
    detalles = uso.get('input_tokens_details')
    texto = detalles.get('text_tokens') if isinstance(detalles, dict) else None
    if texto is None:
        texto = uso.get('input_tokens')  # Sólo se admite texto de entrada.
    totales = {k: uso[k] for k in ('input_tokens', 'output_tokens', 'total_tokens')
               if type(uso.get(k)) is int and uso[k] >= 0}
    entrada = Decimal(texto) * Decimal('0.000005') if type(texto) is int and texto >= 0 else None
    return totales, {'currency': 'USD', 'estimated_output': str(salida),
                     'estimated_text_input': str(entrada) if entrada is not None else None,
                     'estimated_total': str(salida + entrada) if entrada is not None else None,
                     'pricing_url': TARIFAS}


async def generar(tools, args):
    runtime = tools.runtime
    if restringida(runtime):
        raise ValueError('Generación de imágenes deshabilitada en esta sesión.')
    prompt = args['prompt'].strip()
    if not 1 <= len(prompt) <= MAX_PROMPT:
        raise ValueError(f'El prompt debe tener entre 1 y {MAX_PROMPT} caracteres.')
    archivo = destino(tools, args['path'])
    ruta = archivo.relative_to(tools.root).as_posix()
    size, quality = args.get('size', '1024x1024'), args.get('quality', 'medium')
    proveedor = runtime.image_provider
    if proveedor not in ('codex', 'openai'):
        raise ValueError('No tienes una herramienta para generar imágenes desde un prompt.')
    con_codex = proveedor == 'codex'
    valores = set(contenido_externo.secretos(runtime))
    if con_codex:
        codex = imagenes_codex.ejecutable()
        if codex is None:
            raise ValueError('Instala Codex CLI o la app de ChatGPT e inicia sesión en Codex con ChatGPT.')
        modelo = 'codex'
        proposito = 'Enviar este prompt a Codex y generar un PNG usando tu plan de ChatGPT.'
    else:
        modelo = MODELO
        costo = Decimal(PRECIOS[size][CALIDADES.index(quality)])
        clave = ajustes.openai_clave()
        if not clave:
            raise ValueError('Configura la clave de OpenAI en Ajustes o mediante OPENAI_API_KEY; no la pegues '
                             'en el chat.')
        valores.add(clave)
        proposito = (f'Enviar este prompt a OpenAI y generar un PNG. Salida: USD {costo} estimados '
                     '+ tokens de texto de entrada (USD 5 por millón). No es el cargo final.')
    if prompt != contenido_externo.limpiar(prompt, valores) or ruta != contenido_externo.limpiar(ruta, valores):
        raise ValueError('El prompt o la ruta contienen datos sensibles. No se envió el encargo.')
    permiso = {'argv': ['generate_image', ruta, modelo, size, quality, prompt],
               'purpose': proposito, 'motivo': MOTIVO, 'motivos': [MOTIVO]}
    if contenido_externo.contaminada(runtime):
        permiso['justificacion'] = ('El agente leyó contenido externo. Aprueba sólo si el prompt corresponde '
                                    'a lo que pediste.')
    try:
        respuesta = await asyncio.wait_for(runtime.approve(permiso), 600)
    except TimeoutError as exc:
        raise ValueError('El permiso venció. No se envió ninguna solicitud.') from exc
    if respuesta is False:
        raise ValueError('Generación rechazada. No se envió ninguna solicitud.')
    destino(tools, ruta)
    if con_codex:
        datos = await imagenes_codex.generar(codex, prompt, size, quality)
        width, height = dimensiones_png(datos)
        detalle = {'provider': 'codex', 'requested_size': size, 'requested_quality': quality}
    else:
        respuesta = await solicitar(clave, {'model': modelo, 'prompt': prompt, 'size': size, 'quality': quality,
                                           'n': 1, 'output_format': 'png'})
        datos, (width, height) = datos_png(respuesta)
        uso, estimacion = consumo(respuesta, costo)
        detalle = {'quality': quality, 'usage': uso, 'estimated_cost': estimacion}
    try:
        guardar_png(destino(tools, ruta), datos)
    except (OSError, ValueError) as exc:
        raise ValueError('El servicio generó la imagen, pero no se pudo guardar sin sobrescribir el destino. '
                         'La generación pudo tener costo; no se reintentó.') from exc
    from .herramientas import digest
    sha = digest(datos)
    runtime.state.setdefault('changes', []).append({'path': ruta, 'before': 'NEW', 'after': sha, 'via': MOTIVO})
    runtime.save()
    return {'path': ruta, 'model': modelo, 'format': 'png', 'width': width, 'height': height,
            'bytes': len(datos), 'sha256': sha, **detalle}
