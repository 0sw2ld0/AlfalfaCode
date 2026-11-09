"""Lectura de los archivos de otras herramientas: JSONC, TOML y Markdown con cabecera.

Nada de aquí ejecuta, descarga ni escribe: sólo lee archivos regulares de hasta 1 MB.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

MAX_ARCHIVO = 1024 * 1024


class Invalido(ValueError):
    """Un archivo o una entrada que no se puede usar; el texto se enseña al usuario."""


def leer_texto(ruta, maximo=MAX_ARCHIVO):
    ruta = Path(ruta)
    try:
        if not ruta.is_file():
            raise Invalido('no es un archivo regular')
        if ruta.stat().st_size > maximo:
            raise Invalido('supera ' + str(maximo // 1024) + ' KB')
        return ruta.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        raise Invalido('no se pudo leer (' + (exc.strerror or type(exc).__name__) + ')') from None


def sin_comentarios(texto):
    """JSONC → JSON: quita `//`, `/* */` y las comas finales, sin tocar el interior de las cadenas."""
    salida, i, n = [], 0, len(texto)
    while i < n:
        c = texto[i]
        if c == '"':
            fin = i + 1
            while fin < n and texto[fin] != '"':
                fin += 2 if texto[fin] == '\\' else 1
            salida.append(texto[i:fin + 1])
            i = fin + 1
        elif texto.startswith('//', i):
            fin = texto.find('\n', i)
            i = n if fin < 0 else fin
        elif texto.startswith('/*', i):
            fin = texto.find('*/', i + 2)
            i = n if fin < 0 else fin + 2
        else:
            salida.append(c)
            i += 1
    limpio = ''.join(salida)
    # Comas antes de } o ]: se quitan fuera de las cadenas (las cadenas ya están completas en `salida`).
    return re.sub(r'("(?:[^"\\]|\\.)*")|,(\s*[}\]])', lambda m: m.group(1) or m.group(2), limpio)


def leer_jsonc(ruta):
    try:
        datos = json.loads(sin_comentarios(leer_texto(ruta)))
    except json.JSONDecodeError as exc:
        raise Invalido('JSON inválido en la línea ' + str(exc.lineno)) from None
    if not isinstance(datos, dict):
        raise Invalido('se esperaba un objeto JSON')
    return datos


def leer_toml(ruta):
    try:
        return tomllib.loads(leer_texto(ruta))
    except tomllib.TOMLDecodeError as exc:
        raise Invalido('TOML inválido: ' + str(exc)[:120]) from None


def _valor(texto):
    texto = texto.strip()
    if len(texto) >= 2 and texto[0] == texto[-1] and texto[0] in '"\'':
        return texto[1:-1]
    if texto in ('true', 'false'):
        return texto == 'true'
    if re.fullmatch(r'-?\d+', texto):
        return int(texto)
    if re.fullmatch(r'-?\d+\.\d+', texto):
        return float(texto)
    if texto.startswith('[') and texto.endswith(']'):
        return [_valor(v) for v in texto[1:-1].split(',') if v.strip()]
    return texto


def cabecera(texto):
    """Cabecera YAML sencilla de un .md (OpenCode, Claude Code) y su cuerpo.

    Admite `clave: valor`, un nivel de mapa anidado (`tools:` y debajo `  write: false`) y
    listas con guiones. Lo que no entiende se ignora: nunca se ejecuta nada de YAML."""
    if not texto.startswith('---'):
        return {}, texto.strip()
    lineas = texto.split('\n')
    datos, fin, actual, bloque = {}, None, None, None
    for numero, linea in enumerate(lineas[1:], 1):
        if linea.strip() == '---':
            fin = numero
            break
        sangrada = bool(linea) and linea[0] in ' \t'
        if bloque is not None and (sangrada or not linea.strip()):
            datos[bloque] += linea.strip() + '\n'
            continue
        bloque = None
        if not linea.strip() or linea.lstrip().startswith('#'):
            continue
        if sangrada and actual is not None:
            contenido = linea.strip()
            if contenido.startswith('- '):
                if not isinstance(datos[actual], list):
                    datos[actual] = []
                datos[actual].append(_valor(contenido[2:]))
            elif ':' in contenido:
                clave, valor = contenido.split(':', 1)
                if not isinstance(datos[actual], dict):
                    datos[actual] = {}
                datos[actual][clave.strip().strip('"\'')] = _valor(valor)
            continue
        if ':' not in linea:
            continue
        clave, valor = linea.split(':', 1)
        clave = clave.strip()
        if valor.strip() in ('|', '>', '|-', '>-'):
            datos[clave], actual, bloque = '', None, clave
        elif not valor.strip():
            datos[clave], actual = {}, clave
        else:
            datos[clave], actual = _valor(valor), None
    if fin is None:
        return {}, texto.strip()
    # `clave:` sin nada debajo queda vacía, no como un mapa.
    datos = {k: ('' if v == {} else v.strip() if isinstance(v, str) else v) for k, v in datos.items()}
    return datos, '\n'.join(lineas[fin + 1:]).strip()
