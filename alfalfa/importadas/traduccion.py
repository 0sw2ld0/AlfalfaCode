"""De los formatos de OpenCode, Codex y Claude Code a los de AlfalfaCode.

Servidores MCP: al diccionario de `$ALFALFA_HOME/mcp.json` (mcp_cliente.configuracion), que
lo valida igual que uno propio. Las referencias a variables (`{env:X}` de OpenCode, `${X}` de
Claude Code, `env_vars` y `bearer_token_env_var` de Codex) se resuelven en memoria al abrir la
sesión con las mismas reglas que `entorno_heredado`: nunca una clave de proveedor. Ningún valor
se escribe en disco.

Formatos, según su documentación pública:
- OpenCode (opencode.ai/docs/mcp-servers, /agents, /commands y /migrate-v1): `mcp` con los
  servidores directamente (V1, `enabled`) o en `mcp.servers` (V2, `disabled`); `agent`/`agents`;
  `command`/`commands`; `type` local (`command` en lista, `environment`, `cwd`) o remote (`url`,
  `headers`); `timeout` en milisegundos.
- Codex (`[mcp_servers.<nombre>]` de config.toml): `command`, `args`, `env`, `env_vars`, `cwd`,
  `url`, `bearer_token_env_var`, `http_headers`, `env_http_headers`, `enabled`, `enabled_tools`,
  `disabled_tools`, `startup_timeout_sec` y `tool_timeout_sec`.
- Claude Code (`.mcp.json`): `mcpServers` con `command`, `args`, `env`, `type`, `url` y
  `headers`, y `${VAR}` o `${VAR:-defecto}` en sus valores.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..mcp_cliente import configuracion as configuracion_mcp
from .formatos import Invalido

ENV_OPENCODE = re.compile(r'\{env:(?P<nombre>[^}]*)\}')
ENV_CLAUDE = re.compile(r'\$\{(?P<nombre>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<defecto>[^}]*))?\}')
ARCHIVO_OPENCODE = re.compile(r'\{file:(?P<ruta>[^}]*)\}')
TIEMPOS = {'tiempo_inicio': (1, 300), 'tiempo_herramienta': (1, 3600)}
ESFUERZOS = ('low', 'medium', 'high', 'xhigh', 'max')


def _resolver(valor, ambiente, patron, campo):
    """Sustituye las referencias a variables; una variable prohibida o ausente invalida el servidor."""
    if not isinstance(valor, str):
        raise Invalido(campo + ' debe ser texto')
    if patron is ENV_OPENCODE and ARCHIVO_OPENCODE.search(valor):
        raise Invalido(campo + ': {file:…} no se resuelve en servidores MCP importados')

    def cambiar(m):
        nombre, defecto = m.group('nombre'), m.groupdict().get('defecto')
        if nombre not in ambiente and defecto is not None:
            return defecto
        try:
            return configuracion_mcp._heredada(nombre, campo, ambiente)
        except configuracion_mcp._Invalido as exc:
            raise Invalido(str(exc)) from None
    return patron.sub(cambiar, valor)


def _mapa(valor, campo):
    if valor is None:
        return {}
    if not isinstance(valor, dict) or any(not isinstance(v, str) for v in valor.values()):
        raise Invalido(campo + ' debe ser un objeto de textos')
    return valor


def _segundos(valor, campo, divisor=1):
    """Tiempo dentro de los límites de AlfalfaCode (se recorta en vez de invalidar)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
        return None
    minimo, maximo = TIEMPOS[campo]
    return float(min(max(valor / divisor, minimo), maximo))


def _cwd(valor, base):
    if valor is None:
        return None
    if not isinstance(valor, str) or not valor or '\0' in valor:
        raise Invalido('cwd debe ser una ruta')
    ruta = Path(valor).expanduser()
    return str((ruta if ruta.is_absolute() else Path(base) / ruta).resolve())


# --- servidores MCP ----------------------------------------------------------------

def mcp_opencode(entrada, ambiente, base):
    """(datos para mcp.json, cwd, activo, avisos) de una entrada de OpenCode V1 o V2."""
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser un objeto')
    avisos = []
    activo = entrada.get('enabled', True) is not False and entrada.get('disabled', False) is not True
    tipo = entrada.get('type')
    if tipo == 'local':
        orden = entrada.get('command')
        if not isinstance(orden, list) or not orden or any(not isinstance(p, str) or not p for p in orden):
            raise Invalido('command debe ser una lista: el ejecutable y sus argumentos')
        datos = dict(orden=_resolver(orden[0], ambiente, ENV_OPENCODE, 'command'),
                     argumentos=[_resolver(a, ambiente, ENV_OPENCODE, 'command') for a in orden[1:]],
                     entorno={k: _resolver(v, ambiente, ENV_OPENCODE, 'environment.' + k)
                              for k, v in _mapa(entrada.get('environment'), 'environment').items()})
        cwd = _cwd(entrada.get('cwd'), base)
    elif tipo == 'remote':
        datos = dict(url=_resolver(entrada.get('url'), ambiente, ENV_OPENCODE, 'url'),
                     cabeceras={k: _resolver(v, ambiente, ENV_OPENCODE, 'headers.' + k)
                                for k, v in _mapa(entrada.get('headers'), 'headers').items()})
        cwd = None
        if isinstance(entrada.get('oauth'), dict):
            avisos.append('OAuth de OpenCode no se usa: sólo las cabeceras')
    else:
        raise Invalido('type debe ser local o remote')
    tiempo = entrada.get('timeout')
    if isinstance(tiempo, dict):  # V2: milisegundos por fase
        inicio = _segundos(tiempo.get('startup'), 'tiempo_inicio', 1000)
        herramienta = _segundos(tiempo.get('execution'), 'tiempo_herramienta', 1000)
    else:  # V1: un solo número de milisegundos para listar y llamar
        inicio = _segundos(tiempo, 'tiempo_inicio', 1000)
        herramienta = _segundos(tiempo, 'tiempo_herramienta', 1000)
    if inicio:
        datos['tiempo_inicio'] = inicio
    if herramienta:
        datos['tiempo_herramienta'] = herramienta
    return datos, cwd, activo, avisos


def mcp_codex(entrada, ambiente, base):
    """(datos para mcp.json, cwd, activo, avisos) de `[mcp_servers.<nombre>]` de Codex."""
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser una tabla')
    avisos = []
    activo = entrada.get('enabled', True) is not False
    if 'command' in entrada:
        argumentos = entrada.get('args', [])
        if not isinstance(argumentos, list) or any(not isinstance(a, str) for a in argumentos):
            raise Invalido('args debe ser una lista de textos')
        heredadas = []
        for variable in entrada.get('env_vars', []) or []:
            if isinstance(variable, dict):
                if variable.get('source', 'local') != 'local':
                    avisos.append('env_vars remoto de ' + str(variable.get('name')) + ' no se usa')
                    continue
                variable = variable.get('name')
            heredadas.append(variable)
        datos = dict(orden=entrada['command'], argumentos=list(argumentos),
                     entorno=_mapa(entrada.get('env'), 'env'))
        if heredadas:
            datos['entorno_heredado'] = heredadas
        cwd = _cwd(entrada.get('cwd'), base)
    elif 'url' in entrada:
        datos = dict(url=entrada['url'], cabeceras=_mapa(entrada.get('http_headers'), 'http_headers'))
        if entrada.get('env_http_headers'):
            datos['cabeceras_entorno'] = _mapa(entrada['env_http_headers'], 'env_http_headers')
        if entrada.get('bearer_token_env_var'):
            datos['token_entorno'] = entrada['bearer_token_env_var']
        cwd = None
    else:
        raise Invalido('falta command (stdio) o url (HTTP)')
    for campo in ('auth', 'http_headers_helper', 'oauth'):
        if campo in entrada:
            avisos.append(campo + ' de Codex no se usa')
    if 'enabled_tools' in entrada:
        datos['habilitadas'] = entrada['enabled_tools']
    if 'disabled_tools' in entrada:
        datos['deshabilitadas'] = entrada['disabled_tools']
    inicio = (_segundos(entrada.get('startup_timeout_sec'), 'tiempo_inicio')
              or _segundos(entrada.get('startup_timeout_ms'), 'tiempo_inicio', 1000))
    herramienta = _segundos(entrada.get('tool_timeout_sec'), 'tiempo_herramienta')
    if inicio:
        datos['tiempo_inicio'] = inicio
    if herramienta:
        datos['tiempo_herramienta'] = herramienta
    return datos, cwd, activo, avisos


def mcp_claude(entrada, ambiente, base):
    """(datos para mcp.json, cwd, activo, avisos) de una entrada de `.mcp.json` de Claude Code."""
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser un objeto')
    tipo = entrada.get('type', 'stdio' if 'command' in entrada else 'http')
    if tipo == 'sse':
        raise Invalido('MCP por SSE no está soportado; usa HTTP')
    if tipo == 'stdio':
        argumentos = entrada.get('args', [])
        if not isinstance(argumentos, list):
            raise Invalido('args debe ser una lista de textos')
        datos = dict(orden=_resolver(entrada.get('command'), ambiente, ENV_CLAUDE, 'command'),
                     argumentos=[_resolver(a, ambiente, ENV_CLAUDE, 'args') for a in argumentos],
                     entorno={k: _resolver(v, ambiente, ENV_CLAUDE, 'env.' + k)
                              for k, v in _mapa(entrada.get('env'), 'env').items()})
        # Claude Code los lanza en la carpeta del proyecto.
        return datos, str(Path(base).resolve()), True, []
    if tipo != 'http':
        raise Invalido('type debe ser stdio o http')
    datos = dict(url=_resolver(entrada.get('url'), ambiente, ENV_CLAUDE, 'url'),
                 cabeceras={k: _resolver(v, ambiente, ENV_CLAUDE, 'headers.' + k)
                            for k, v in _mapa(entrada.get('headers'), 'headers').items()})
    return datos, None, True, []


def resumen_mcp(datos):
    """Qué se lanza o a dónde se conecta, sin argumentos, entorno ni cabeceras (pueden llevar claves)."""
    if 'url' in datos:
        from urllib.parse import urlparse
        url = urlparse(str(datos['url']))
        return 'HTTP · ' + (url.hostname or '?') + (url.path or '')
    return 'orden · ' + Path(str(datos.get('orden', '?'))).name


# --- agentes -------------------------------------------------------------------------

def _niega(regla):
    return isinstance(regla, str) and regla == 'deny'


def agente_opencode(datos, cuerpo, base):
    """Tipo de subagente de AlfalfaCode (como los de subagentes.cargar_tipos) desde OpenCode.

    `prompt` (V1) o `system` (V2) o el cuerpo del .md son las instrucciones; `{file:ruta}` lee
    un archivo de la misma carpeta del proyecto. Editar denegado (`permission.edit: deny`,
    `tools.write/edit: false` o una regla V2) lo deja de sólo lectura y `bash` denegado le
    quita run_command. `provider/model` no es un modelo de AlfalfaCode: hereda el de la sesión
    y la variante (`#high` o `variant`) pasa a esfuerzo si es uno conocido."""
    instrucciones = cuerpo or datos.get('system') or datos.get('prompt') or ''
    if not isinstance(instrucciones, str):
        raise Invalido('prompt/system debe ser texto')
    archivo = ARCHIVO_OPENCODE.fullmatch(instrucciones.strip())
    if archivo:
        instrucciones = _archivo_de_prompt(archivo.group('ruta'), base)
    permisos = datos.get('permission') if isinstance(datos.get('permission'), dict) else {}
    herramientas = datos.get('tools') if isinstance(datos.get('tools'), dict) else {}
    reglas = datos.get('permissions') if isinstance(datos.get('permissions'), list) else []
    negadas = {r.get('action') for r in reglas
               if isinstance(r, dict) and r.get('effect') == 'deny' and r.get('resource', '*') == '*'}
    solo_lectura = (_niega(permisos.get('edit')) or 'edit' in negadas or '*' in negadas
                    or (herramientas.get('write') is False and herramientas.get('edit') is False))
    sin_orden = _niega(permisos.get('bash')) or 'shell' in negadas or herramientas.get('bash') is False
    modelo = datos.get('model')
    variante = datos.get('variant')
    if isinstance(modelo, str) and '#' in modelo:
        variante = modelo.rsplit('#', 1)[1]
    elif isinstance(modelo, dict):
        variante = modelo.get('variant', variante)
    descripcion = datos.get('description') or ''
    if not isinstance(descripcion, str):
        descripcion = ''
    return {'descripcion': descripcion[:500] or 'Agente de OpenCode', 'instrucciones': instrucciones[:20000],
            'solo_lectura': bool(solo_lectura), 'herramientas': None,
            'excluidas': ['run_command'] if sin_orden else [], 'modelo': None,
            'esfuerzo': variante if variante in ESFUERZOS else None}


def _archivo_de_prompt(ruta, base):
    from .formatos import leer_texto
    real = (Path(base) / Path(ruta).expanduser()).resolve()
    try:
        real.relative_to(Path(base).resolve())
    except ValueError:
        raise Invalido('{file:' + ruta + '} sale de la carpeta de la configuración') from None
    return leer_texto(real, 64 * 1024)


# --- comandos «/» --------------------------------------------------------------------

ORDEN_EN_LINEA = re.compile(r'!`([^`\n]+)`')
POSICIONAL = re.compile(r'\$([1-9])')


def expandir_plantilla(plantilla, argumentos):
    """$ARGUMENTS, $1…$9 (el más alto se lleva el resto) como OpenCode y Claude Code.

    Sin marcadores, los argumentos van detrás tras una línea en blanco. `` !`orden` `` no se
    ejecuta aquí: queda como petición al agente, que la corre con los permisos de siempre."""
    argumentos = (argumentos or '').strip()
    try:
        partes = shlex.split(argumentos)
    except ValueError:
        partes = argumentos.split()
    usados = [int(n) for n in POSICIONAL.findall(plantilla)]
    tope = max(usados) if usados else 0

    def posicional(m):
        n = int(m.group(1))
        if n == tope:
            return ' '.join(partes[n - 1:])
        return partes[n - 1] if n <= len(partes) else ''
    texto = plantilla.replace('$ARGUMENTS', argumentos).replace('{{args}}', argumentos)
    texto = POSICIONAL.sub(posicional, texto)
    if argumentos and '$ARGUMENTS' not in plantilla and '{{args}}' not in plantilla and not usados:
        texto = texto.rstrip() + '\n\n' + argumentos
    return ORDEN_EN_LINEA.sub(lambda m: '(ejecuta `' + m.group(1) + '` y usa su salida)', texto)
