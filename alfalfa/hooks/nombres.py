"""Equivalencias entre las herramientas de Claude Code y las de AlfalfaCode.

Idea tomada de codex-rs/core/src/tools/hook_names.rs (OpenAI Codex, Apache-2.0;
ver NOTICE y PROCEDENCIA.md de esta carpeta): el despacho conserva siempre el
nombre nativo, y los filtros de los hooks se comparan también con un alias.
Diferencia con Codex: cuando la equivalencia es exacta (Bash, Read, Edit,
Write), el hook recibe el nombre y la forma de entrada de Claude, porque los
hooks de ~/.claude/plugins filtran y leen esos campos. El nombre y los
argumentos nativos viajan además en `alfalfa_tool_name` y `alfalfa_tool_input`.

La vuelta (un `updatedInput` del hook) sólo acepta lo que tiene una traducción
exacta: una orden de Bash con tuberías, redirecciones, variables o comodines no
es un argv literal y se rechaza sin ejecutar ninguna interpretación aproximada.
"""
from __future__ import annotations

import math
import shlex
from pathlib import Path

# Herramienta nativa → nombre de Claude Code. run_checks también lanza procesos:
# un hook que vigila Bash no debe poder esquivarse cambiando de herramienta.
EQUIVALENCIAS = {
    'run_command': 'Bash',
    'run_checks': 'Bash',
    'read_file': 'Read',
    'edit_file': 'Edit',
    'write_file': 'Write',
}

# Inventario del resto: conservan su nombre nativo y su forma de entrada, porque
# la herramienta de Claude más parecida no significa lo mismo.
SIN_EQUIVALENTE = {
    'app_list': 'Claude Code no maneja otras apps del Mac por sí mismo; Codex lo hace con Computer Use.',
    'app_state': 'Claude Code no maneja otras apps del Mac por sí mismo; Codex lo hace con Computer Use.',
    'app_action': 'Claude Code no maneja otras apps del Mac por sí mismo; Codex lo hace con Computer Use.',
    'list_files': 'Glob recibe un patrón; list_files lista una carpeta.',
    'search': 'Grep recibe una expresión regular; search busca texto literal.',
    'update_plan': 'TodoWrite recibe todos con content y activeForm; update_plan, plan con step y status (los mismos '
                   'estados).',
    'inspect_image': 'Read de Claude también abre imágenes, pero aquí analiza otro modelo.',
    'web_search': 'WebSearch de Claude recibe query y allowed_domains; aquí query y domains.',
    'read_web': 'WebFetch de Claude recibe url y prompt y resume la página; read_web devuelve su texto.',
    'buscar_herramientas': 'Buscar o cargar un esquema MCP no ejecuta ninguna herramienta MCP.',
    'abrir_habilidad': 'Skill de Claude carga una habilidad por nombre; aquí se abre por referencia autorizada.',
    'invocar_comando': 'Los comandos de Claude son prompts del usuario; aquí sólo se compone su plantilla.',
    'apply_patch': 'Edita varios archivos con un parche; como en Codex, los filtros Edit y Write también lo eligen.',
    'wrap_up': 'El cierre de turno de Claude Code es de la app, no una herramienta que vean los hooks.',
    'propose_session': 'Las tarjetas «Nueva sesión» de Claude Code son de la app, no una herramienta de los hooks.',
    'command_status': 'BashOutput y KillShell de Claude Code son dos herramientas; aquí es una.',
    'spawn_agent': 'Agent (antes Task) de Claude Code recibe prompt y subagent_type; aquí message y agent_type.',
    'wait_agent': 'Claude Code avisa al terminar un agente en segundo plano; aquí se espera como en Codex.',
    'send_input': 'SendMessage de Claude Code; aquí con el nombre de Codex.',
    'close_agent': 'Claude Code no cierra agentes; Codex sí, con close_agent.',
    'list_agents': 'Claude Code no lista sus agentes; Codex sí, con list_agents.',
    'memoria': 'Claude Code guarda la memoria en CLAUDE.md con sus herramientas de archivos; aquí es un almacén '
               'propio del arnés.',
    'habilidad': 'Claude Code no crea habilidades por sí mismo; aquí se guardan en su carpeta con aprobación.',
}

# Alias que sólo sirven para elegir hooks (hook_names.rs: apply_patch con Write y
# Edit): el hook recibe el nombre nativo y su entrada nativa.
# Un hook que vigila WebSearch o WebFetch también vigila la web de AlfalfaCode.
ALIAS_FILTRO = {'apply_patch': ['Edit', 'Write'], 'spawn_agent': ['Agent', 'Task'],
                'web_search': ['WebSearch'], 'read_web': ['WebFetch']}

# Campos de entrada de Claude Code que se saben traducir, por herramienta.
CAMPOS_CLAUDE = {
    'Bash': {'command', 'description', 'timeout', 'run_in_background'},
    'Read': {'file_path', 'offset', 'limit'},
    'Edit': {'file_path', 'old_string', 'new_string', 'replace_all'},
    'Write': {'file_path', 'content'},
}

# Caracteres que la shell interpreta fuera de comillas: con ellos la orden no es
# un argv literal (tuberías, redirecciones, listas, subshells, variables,
# comodines, llaves, tilde, historia y comentarios).
_SHELL_FUERA = set('|&;<>()$`*?[]{}~!#\n\r')
# Dentro de comillas dobles la shell aún expande $, ` y las barras.
_SHELL_DOBLES = set('$`\\!')


class NoRepresentable(ValueError):
    """La entrada del hook no tiene traducción exacta a la herramienta nativa."""


def nombre_hook(nativo):
    """Nombre que recibe el hook: el de Claude si hay equivalencia exacta."""
    return EQUIVALENCIAS.get(nativo, nativo)


def entradas_de_filtro(nativo):
    """Nombres con los que se comparan los filtros: el del hook primero, luego el nativo."""
    claude = EQUIVALENCIAS.get(nativo)
    return ([claude, nativo] if claude else [nativo]) + ALIAS_FILTRO.get(nativo, [])


def _absoluta(raiz, ruta):
    return str((Path(raiz) / ruta).resolve()) if isinstance(ruta, str) else ruta


def a_claude(nativo, args, raiz):
    """Entrada con la forma de Claude Code; las nativas sin equivalencia, tal cual."""
    args = args if isinstance(args, dict) else {}
    if nativo in ('run_command', 'run_checks'):
        argv = args.get('argv')
        entrada = {'command': shlex.join(str(a) for a in argv) if isinstance(argv, list) else ''}
        if isinstance(args.get('purpose'), str):
            entrada['description'] = args['purpose']
        if isinstance(args.get('timeout_seconds'), int) and not isinstance(args['timeout_seconds'], bool):
            entrada['timeout'] = args['timeout_seconds'] * 1000
        return entrada
    if nativo == 'read_file':
        entrada = {'file_path': _absoluta(raiz, args.get('path'))}
        for campo in ('offset', 'limit'):
            if campo in args:
                entrada[campo] = args[campo]
        return entrada
    if nativo == 'edit_file':
        return {'file_path': _absoluta(raiz, args.get('path')), 'old_string': args.get('old'),
                'new_string': args.get('new'), 'replace_all': False}
    if nativo == 'write_file':
        return {'file_path': _absoluta(raiz, args.get('path')), 'content': args.get('content')}
    return dict(args)


def argv_literal(orden):
    """argv de una orden de shell que no usa ninguna expansión; si no, NoRepresentable."""
    if not isinstance(orden, str) or not orden.strip():
        raise NoRepresentable('command debe ser una orden no vacía.')
    comillas = None
    escape = False
    for caracter in orden:
        if escape:
            escape = False
        elif comillas == "'":
            if caracter == "'":
                comillas = None
        elif comillas == '"':
            if caracter == '"':
                comillas = None
            elif caracter in _SHELL_DOBLES:
                raise NoRepresentable('La orden usa «' + caracter + '» entre comillas dobles: la shell lo '
                                      'expandiría y run_command no usa shell.')
        elif caracter in ('"', "'"):
            comillas = caracter
        elif caracter == '\\':
            escape = True
        elif caracter in _SHELL_FUERA:
            raise NoRepresentable('La orden usa «' + caracter.replace('\n', '\\n') + '», que la shell '
                                  'interpreta (tuberías, redirecciones, variables o comodines): no es un '
                                  'argv literal y run_command no usa shell.')
    if comillas or escape:
        raise NoRepresentable('La orden tiene comillas o una barra final sin cerrar.')
    argv = shlex.split(orden, posix=True)
    if not argv:
        raise NoRepresentable('command no contiene ninguna palabra.')
    return argv


def _relativa(raiz, ruta):
    if not isinstance(ruta, str) or not ruta:
        raise NoRepresentable('file_path debe ser una ruta de texto.')
    base = Path(raiz).resolve()
    destino = Path(ruta)
    destino = (destino if destino.is_absolute() else base / destino).resolve()
    try:
        return str(destino.relative_to(base))
    except ValueError:
        raise NoRepresentable('file_path queda fuera de la carpeta de trabajo: ' + ruta[:200]) from None


def desde_claude(nativo, original, nueva, raiz):
    """Argumentos nativos a partir del `updatedInput` de un hook, o NoRepresentable.

    Lo que el hook no puede expresar en la forma de Claude (cwd de la orden,
    expected_sha256, needs_network…) se conserva de la llamada original; todo
    lo resultante vuelve a validarse y a pedir permiso como una llamada nueva.
    """
    if not isinstance(nueva, dict):
        raise NoRepresentable('updatedInput debe ser un objeto JSON.')
    original = original if isinstance(original, dict) else {}
    claude = EQUIVALENCIAS.get(nativo)
    if claude is None:
        # Nativa sin equivalencia o herramienta MCP: ya está en su propia forma.
        return dict(nueva)
    sobran = sorted(set(nueva) - CAMPOS_CLAUDE[claude])
    if sobran:
        raise NoRepresentable('Campos de ' + claude + ' sin traducción a ' + nativo + ': ' + ', '.join(sobran) + '.')
    resultado = dict(original)
    if claude == 'Bash':
        if nueva.get('run_in_background'):
            raise NoRepresentable('run_in_background no existe en AlfalfaCode: no se lanza en segundo plano.')
        resultado['argv'] = argv_literal(nueva.get('command'))
        if 'description' in nueva:
            if not isinstance(nueva['description'], str) or not nueva['description'].strip():
                raise NoRepresentable('description debe ser texto no vacío.')
            resultado['purpose'] = nueva['description']
        if 'timeout' in nueva:
            valor = nueva['timeout']
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
                raise NoRepresentable('timeout debe ser un número positivo de milisegundos.')
            resultado['timeout_seconds'] = max(1, math.ceil(valor / 1000))
        return resultado
    resultado['path'] = _relativa(raiz, nueva.get('file_path'))
    if claude == 'Read':
        for campo in ('offset', 'limit'):
            if campo in nueva:
                resultado[campo] = nueva[campo]
            else:
                resultado.pop(campo, None)
    elif claude == 'Edit':
        if nueva.get('replace_all'):
            raise NoRepresentable('replace_all no existe en edit_file: cada edición cambia un único bloque.')
        for campo, propio in (('old_string', 'old'), ('new_string', 'new')):
            if not isinstance(nueva.get(campo), str):
                raise NoRepresentable(campo + ' debe ser texto.')
            resultado[propio] = nueva[campo]
    elif claude == 'Write':
        if not isinstance(nueva.get('content'), str):
            raise NoRepresentable('content debe ser texto.')
        resultado['content'] = nueva['content']
    return resultado
