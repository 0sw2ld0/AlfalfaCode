"""Configuración explícita de complementos; no escanea instalaciones ajenas."""
from __future__ import annotations

import json
from pathlib import Path

ARCHIVO = 'complementos.json'
CAMPOS_RAIZ = {'raiz', 'activo'}
# Política exclusivamente del usuario; nunca se mezcla con el manifiesto.
POLITICA_MCP = {'habilitadas', 'deshabilitadas', 'directas', 'confianza', 'max_bytes_esquema',
                'tiempo_inicio', 'tiempo_herramienta', 'tiempo_permiso', 'red', 'activo'}


def leer(home):
    """(raíces autorizadas, políticas por complemento, diagnósticos (código, texto))."""
    ruta = Path(home) / ARCHIVO
    if not ruta.exists() and not ruta.is_symlink():
        return [], {}, []
    try:
        if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > 1024 * 1024:
            raise ValueError('debe ser un archivo regular de hasta 1 MB, sin enlaces')
        datos = json.loads(ruta.read_text(encoding='utf-8'))
        if not isinstance(datos, dict) or set(datos) - {'raices', 'politicas', 'habilidades_sueltas'}:
            raise ValueError('sólo admite las claves raices, politicas y habilidades_sueltas')
        entradas = datos.get('raices', [])
        politicas = datos.get('politicas', {})
        if not isinstance(entradas, list) or not isinstance(politicas, dict):
            raise ValueError('raices debe ser una lista y politicas un objeto')
    except (OSError, ValueError) as exc:
        motivo = str(exc)[:200] if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) \
            else type(exc).__name__
        return [], {}, [('COMP-CONFIG', ARCHIVO + ' inválido: ' + motivo + '.')]
    raices, diagnosticos = [], []
    for entrada in entradas:
        try:
            if not isinstance(entrada, dict) or set(entrada) - CAMPOS_RAIZ:
                raise ValueError('cada raíz debe ser {"raiz": ruta, "activo": true}')
            if entrada.get('activo', True) is not True:
                continue
            valor = entrada.get('raiz')
            if not isinstance(valor, str) or not valor or '\0' in valor or len(valor) > 4096:
                raise ValueError('raiz debe ser una ruta de texto no vacía')
            # Una ruta relativa pertenece a la configuración, no al cwd del proyecto.
            raiz = Path(valor).expanduser()
            if not raiz.is_absolute():
                raiz = Path(home) / raiz
            raices.append(raiz.absolute())
        except ValueError as exc:
            diagnosticos.append(('COMP-CONFIG-RAIZ', str(exc) + '; se ignora.'))
    limpias = {}
    for identidad, politica in politicas.items():
        if not isinstance(identidad, str) or not isinstance(politica, dict):
            diagnosticos.append(('COMP-CONFIG-POLITICA', 'Cada política necesita identidad y objeto; se ignora.'))
            continue
        desconocidas = set(politica) - {'mcp', 'hooks'}
        hooks = politica.get('hooks', {})
        if (desconocidas or not isinstance(politica.get('mcp', {}), dict) or not isinstance(hooks, dict)
                or set(hooks) - {'activo', 'entorno'} or not isinstance(hooks.get('activo', True), bool)):
            diagnosticos.append(('COMP-CONFIG-POLITICA', 'Política ignorada: sólo admite mcp por servidor y '
                                 'hooks con activo y entorno.'))
            continue
        limpias[identidad] = politica
    return raices, limpias, diagnosticos


def habilidades_sueltas(home):
    """¿Se leen las habilidades sueltas (~/.claude/skills…)? Sí, salvo `"habilidades_sueltas": false`."""
    ruta = Path(home) / ARCHIVO
    try:
        if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > 1024 * 1024:
            return True
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return True
    return not (isinstance(datos, dict) and datos.get('habilidades_sueltas') is False)


def servidor_usuario(politicas, identidad, servidor):
    """Sólo devuelve campos reconocidos de política; sin rutas ni credenciales del plugin."""
    politica = politicas.get(identidad, {}).get('mcp', {}).get(servidor, {})
    if not isinstance(politica, dict):
        return {}, [('COMP-POLITICA-MCP', 'La política del servidor debe ser un objeto; se ignora.')]
    desconocidas = sorted(set(politica) - POLITICA_MCP)
    avisos = [('COMP-POLITICA-MCP', 'Campos de política desconocidos ignorados: ' + ', '.join(desconocidas) + '.')] \
        if desconocidas else []
    return {k: v for k, v in politica.items() if k in POLITICA_MCP}, avisos
