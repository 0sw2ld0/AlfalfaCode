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


# Cómo se nombran el arnés y el integrador en los textos que leen los agentes y
# en los informes. Un integrador devuelve sólo las claves que cambia.
NOMBRES = {
    'runtime': 'AlfalfaCode',      # «runtime nativo de …» en el manual y el contrato
    'concede': 'el arnés solo',    # «Algunas órdenes las concede …»
    'decide': 'el usuario',        # «traer otra rama … lo decide …»
    'integrador': 'integrador',    # `kind` del modelo sin cuota en la energía
}


def nombres():
    """Las claves de `NOMBRES` que el integrador cambia (por defecto, ninguna)."""
    return {}


def nombre(clave):
    valor = (nombres() or {}).get(clave)
    return valor if isinstance(valor, str) and valor else NOMBRES[clave]


def firma_de_commits():
    """La línea `Co-Authored-By` que se añade a los `git commit` del agente, o None para no firmar.

    Por defecto es la de AlfalfaCode (`alfalfa.firma.FIRMA`). Un integrador la cambia por la suya o la
    quita. El usuario la apaga con `--sin-firma`, `ALFALFA_FIRMA=0` o Ajustes ▸ General.
    """
    from .firma import FIRMA
    return FIRMA


def ramas_de_avance():
    """Ramas, además de main y origin/main, a las que un agente puede avanzar con `git merge --ff-only`."""
    return ()


def orden_permitida(programa, argumentos, espacio):
    """El motivo si el integrador da por segura esta orden simple (`programa` y sus argumentos), o None.

    Para órdenes propias de su proyecto que el arnés no conoce. Sólo puede
    añadir permisos a los runtimes sin política de aprobación, nunca quitarlos;
    `alfalfa.permisos.ordenes.fuera_del_espacio(ruta, espacio)` ayuda a
    comprobar que una salida queda dentro del worktree.
    """
    return None


def ejecutable(nombre):
    """Ruta del programa `nombre` (por ejemplo `codex`), o None si no está."""
    return shutil.which(nombre)


def ollama_url():
    """URL base de Ollama: `ALFALFA_OLLAMA_URL`, la de `proveedores.json` o la local de siempre."""
    from .proveedores import configuracion
    return (os.getenv('ALFALFA_OLLAMA_URL') or configuracion.de('ollama').get('url')
            or 'http://127.0.0.1:11434')


def ollama_clave():
    """Clave de Ollama Cloud: la estándar `OLLAMA_API_KEY` o la guardada en el Llavero."""
    from .proveedores import llavero
    return os.getenv('OLLAMA_API_KEY') or llavero.leer('ollama')


def openai_clave():
    """Clave de la API de OpenAI: la estándar `OPENAI_API_KEY` o la guardada en el Llavero."""
    from .proveedores import llavero
    return os.getenv('OPENAI_API_KEY') or llavero.leer('openai')


def anthropic_clave():
    """Clave de la API de Anthropic (Claude): la estándar `ANTHROPIC_API_KEY` o la guardada en el Llavero."""
    from .proveedores import llavero
    return os.getenv('ANTHROPIC_API_KEY') or llavero.leer('anthropic')


def clave_compatible(proveedor):
    """Clave de un proveedor compatible con OpenAI del catálogo (OpenRouter, OpenCode Zen…).

    La guardada en el Llavero para ese proveedor. OpenRouter y OpenCode Zen leen
    antes su variable estándar (`OPENROUTER_API_KEY`, `OPENCODE_API_KEY`); los demás
    no leen variables, porque el nombre viene de un catálogo externo.
    """
    from .proveedores import configuracion, llavero
    from .proveedores.compatible import CONOCIDOS
    variable = CONOCIDOS.get(proveedor, {}).get('env')
    if variable and os.getenv(variable):
        return os.getenv(variable)
    if proveedor not in CONOCIDOS and configuracion.de(proveedor).get('guardada') is not True:
        return None
    return llavero.leer(proveedor)


def alfalfa_codigo():
    """Código de cortesía del servicio Alfalfa: `ALFALFA_CODIGO` o el guardado al activarlo."""
    from .proveedores import llavero
    return os.getenv('ALFALFA_CODIGO') or llavero.leer('alfalfa')


def alfalfa_servicio_url():
    """URL base del servicio Alfalfa: `ALFALFA_SERVICIO_URL` o la de `proveedores.json`.

    El repositorio es público y no trae ninguna: sin URL el proveedor no aparece.
    """
    from .proveedores import configuracion
    return os.getenv('ALFALFA_SERVICIO_URL') or configuracion.de('alfalfa').get('url') or None


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
