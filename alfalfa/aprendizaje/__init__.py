"""Aprender de cada sesión: memoria curada y habilidades aprendidas.

Idea tomada de Hermes Agent (Nous Research, MIT): el arnés, no el modelo, guarda lo
que vale la pena recordar. Aquí:

- `memoria`: tres almacenes con tope duro de caracteres (usuario, entorno del Mac y
  proyecto), todos dentro de $ALFALFA_HOME: un repositorio no puede plantar la suya.
  Entran en el prompt de sistema congelados al empezar la sesión.
- `habilidades`: habilidades SKILL.md propias en $ALFALFA_HOME/skills/aprendidas
  (del usuario) o .alfalfa/skills (del proyecto), con su registro de origen.
- `revision`: una pasada breve al cerrar el turno que propone memoria o habilidades.
- `pendientes`: lo que la revisión propone y espera al usuario.
- `orden`: `alfalfa --memoria` y `alfalfa --aprendido`.

Ajustes en $ALFALFA_HOME/aprendizaje.json (Ajustes ▸ Memoria de la app los escribe):

    {"memoria": true, "habilidades": true, "revision": true,
     "modelo_revision": null, "apagado_en": ["<clave de proyecto>"]}

No hay ajuste para guardar sin preguntar: cada escritura de memoria o de una habilidad
pide su tarjeta con el diff, en todos los modos (también «Omitir permisos»).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ARCHIVO_AJUSTES = 'aprendizaje.json'
POR_DEFECTO = {
    # La herramienta memoria y su instantánea en el prompt.
    'memoria': True,
    # La herramienta habilidad.
    'habilidades': True,
    # La pasada al cerrar el turno (también ALFALFA_APRENDER_REVISION=0 la apaga).
    'revision': True,
    # Modelo de la revisión (con su prefijo de proveedor); None elige el más barato de la familia.
    'modelo_revision': None,
    # Proyectos (su clave) donde no se aprende nada.
    'apagado_en': [],
}
BOOLEANOS = ('memoria', 'habilidades', 'revision')


def hogar(home=None):
    from ..terminal import home_dir
    return home_dir(home)


def raiz_de_proyecto(cwd):
    """La raíz git de la carpeta de trabajo, o la propia carpeta."""
    actual = Path(cwd).resolve()
    return next((c for c in (actual, *actual.parents) if (c / '.git').exists()), actual)


def clave_de_proyecto(cwd):
    """Identificador estable del proyecto: no depende de nada que el repositorio pueda escribir."""
    return hashlib.sha256(str(raiz_de_proyecto(cwd)).encode('utf-8')).hexdigest()[:16]


def leer_ajustes(home=None):
    """Los ajustes con sus valores por defecto; un archivo roto vale lo de siempre."""
    datos = dict(POR_DEFECTO, apagado_en=[])
    ruta = hogar(home) / ARCHIVO_AJUSTES
    try:
        if ruta.is_file() and not ruta.is_symlink():
            crudo = json.loads(ruta.read_text(encoding='utf-8'))
            if isinstance(crudo, dict):
                for clave in BOOLEANOS:
                    if isinstance(crudo.get(clave), bool):
                        datos[clave] = crudo[clave]
                modelo = crudo.get('modelo_revision')
                if isinstance(modelo, str) and 0 < len(modelo) <= 200:
                    datos['modelo_revision'] = modelo
                apagados = crudo.get('apagado_en')
                if isinstance(apagados, list):
                    datos['apagado_en'] = [c for c in apagados if isinstance(c, str) and len(c) <= 64][:500]
    except (OSError, ValueError):
        pass
    if os.environ.get('ALFALFA_APRENDER_REVISION') == '0':
        datos['revision'] = False
    if os.environ.get('ALFALFA_APRENDER') == '0':
        datos['memoria'] = datos['habilidades'] = datos['revision'] = False
    return datos


def guardar_ajustes(cambios, home=None):
    """Cambia los ajustes (sólo las claves conocidas) y devuelve los nuevos."""
    from .rutas import escribir_privado
    base = hogar(home)
    # Se parte del archivo, no de leer_ajustes: lo que fija el entorno no se guarda como elegido.
    ruta = base / ARCHIVO_AJUSTES
    try:
        crudo = json.loads(ruta.read_text(encoding='utf-8')) if ruta.is_file() and not ruta.is_symlink() else {}
    except (OSError, ValueError):
        crudo = {}
    crudo = crudo if isinstance(crudo, dict) else {}
    for clave, valor in cambios.items():
        if clave in BOOLEANOS:
            if not isinstance(valor, bool):
                raise ValueError(clave + ' es sí o no.')
            crudo[clave] = valor
        elif clave == 'modelo_revision':
            if valor is not None and (not isinstance(valor, str) or not 0 < len(valor) <= 200):
                raise ValueError('modelo_revision es el nombre de un modelo, o nada.')
            crudo[clave] = valor or None
        elif clave == 'apagado_en':
            crudo[clave] = list(valor)
        else:
            raise ValueError('Ajuste desconocido: ' + str(clave))
    escribir_privado(base, base / ARCHIVO_AJUSTES, json.dumps(crudo, ensure_ascii=False, indent=2) + '\n')
    return leer_ajustes(base)


def apagado_en_proyecto(cwd, home=None, ajustes=None):
    ajustes = ajustes or leer_ajustes(home)
    return clave_de_proyecto(cwd) in ajustes['apagado_en']


def cambiar_proyecto(cwd, encendido, home=None):
    """Aprender en esta carpeta, sí o no («apagable por proyecto»)."""
    clave = clave_de_proyecto(cwd)
    apagados = [c for c in leer_ajustes(home)['apagado_en'] if c != clave]
    if not encendido:
        apagados.append(clave)
    return guardar_ajustes({'apagado_en': apagados}, home)
