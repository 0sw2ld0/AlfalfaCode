"""Lo que una aplicación que integra AlfalfaCode puede cambiar sin que el arnés la conozca.

Sin integrador cada ajuste tiene su valor propio. Una aplicación que integra el
arnés los sustituye al cargarse, y así el arnés no importa nada suyo: sus
instrucciones y su tono, dónde están los binarios de los motores, cómo se llega
al buscador web y quién mira las imágenes. Los ajustes se leen en cada uso, nunca al importar, para que
cambiarlos o parchearlos en una prueba llegue a todos.
"""
from __future__ import annotations

import os
import shutil

# Devuelve (instrucciones, tono) para un NativeRuntime creado sin ellas, o None
# para usar las de AlfalfaCode en la terminal.
instrucciones_por_defecto = None


def ejecutable(nombre):
    """Ruta del programa `nombre` (por ejemplo `codex`), o None si no está."""
    return shutil.which(nombre)


def ollama_url():
    """URL base de Ollama: `ALFALFA_OLLAMA_URL`, o la local de siempre."""
    return os.getenv('ALFALFA_OLLAMA_URL') or 'http://127.0.0.1:11434'


def ollama_clave():
    """Clave de Ollama Cloud: la estándar `OLLAMA_API_KEY`."""
    return os.getenv('OLLAMA_API_KEY') or None


def cuysearch_url():
    return os.getenv('ALFALFA_CUYSEARCH_URL', '')


def cuysearch_clave():
    return os.getenv('ALFALFA_CUYSEARCH_KEY')


def repo_del_integrador():
    """Repositorio cuyo `.venv` y referencias se dan por propios, o None.

    Un integrador puede devolver el suyo si sus agentes ejecutan su suite con
    `<repo>/.venv/bin/python`.
    """
    return None


def vision_delegada():
    """Quién mira las imágenes de `inspect_image`, o None para el modelo de la sesión.

    Como `view_image` de Codex, por defecto mira el propio modelo con el que
    trabaja la sesión, si Ollama le declara visión (`vision` en `/api/show`).
    Una aplicación puede delegarlo en una sesión de Codex de solo lectura con
    un diccionario: `modelo` (obligatorio), `esfuerzo` (por defecto 'max') y
    `nombre`, que aparece en la actividad y en los errores. Por ejemplo:
    {'modelo': 'gpt-5', 'esfuerzo': 'high', 'nombre': 'GPT'}.
    """
    return None


async def antes_de_cuysearch(base):
    """Se llama antes de cada petición al buscador: un integrador puede encenderlo si está apagado."""
    return None
