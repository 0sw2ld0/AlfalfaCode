"""Nombres que ve el modelo: mcp__<servidor>__<herramienta>, únicos y reversibles.

El identificador de servidor no admite «_» (configuracion.ID), así que el primer
«__» tras el prefijo separa siempre servidor y herramienta: dos servidores no
pueden producir el mismo nombre. Dentro de un servidor, la normalización
(caracteres fuera de [A-Za-z0-9_-] pasan a «_») o el recorte a 64 caracteres
pueden juntar dos herramientas; la segunda, en orden del nombre original,
recibe un sufijo con el SHA-1 de su nombre original. Es la técnica de Codex
(codex-mcp/src/tools.rs, unique_callable_parts) con tres adaptaciones: tope de
64 caracteres en vez de 128 (límite de nombre de función de las API de chat),
sufijo de 8 hexadecimales en vez de 12 para dejar sitio al nombre, y «-» se
conserva (Codex sólo admite [A-Za-z0-9_]). La asociación nombre → original vive en un mapa: el
nombre expuesto nunca se vuelve a descomponer para llamar.
"""
from __future__ import annotations

import hashlib
import re

PREFIJO = 'mcp__'
SEPARADOR = '__'
# Límite común de los proveedores para nombres de función.
MAX_NOMBRE = 64
SUFIJO = 8


def normalizar(texto):
    return re.sub(r'[^A-Za-z0-9_-]', '_', texto)


def _sufijo(original, intento):
    base = original if intento == 0 else original + '\0' + str(intento)
    return '_' + hashlib.sha1(base.encode('utf-8')).hexdigest()[:SUFIJO]


def asignar(servidor, originales):
    """{nombre expuesto: original} determinista para las herramientas de un servidor."""
    cabeza = PREFIJO + servidor + SEPARADOR
    usados, mapa = set(), {}
    for original in sorted(set(originales)):
        nombre = cabeza + (normalizar(original) or '_')
        if len(nombre) > MAX_NOMBRE or nombre in usados:
            completo, intento = nombre, 0
            while True:
                sufijo = _sufijo(original, intento)
                nombre = completo[:MAX_NOMBRE - len(sufijo)] + sufijo
                if nombre not in usados:
                    break
                intento += 1
        usados.add(nombre)
        mapa[nombre] = original
    return mapa
