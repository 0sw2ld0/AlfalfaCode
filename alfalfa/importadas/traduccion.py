"""De los formatos de OpenCode, Codex y Claude Code a los de AlfalfaCode.

Servidores MCP: al diccionario de `$ALFALFA_HOME/mcp.json` (mcp_cliente.configuracion), que
lo valida igual que uno propio. Las referencias a variables (`{env:X}` de OpenCode, `${X}` de
Claude Code, `env_vars` y `bearer_token_env_var` de Codex) se resuelven en memoria al abrir la
sesión con las mismas reglas que `entorno_heredado`: nunca una clave de proveedor. Ningún valor
se escribe en disco y los que salen de variables se redactan como el entorno del servidor.

La carpeta de trabajo (`cwd`) de un servidor stdio es también la única que puede escribir dentro
del sandbox, así que se acota: la de un servidor del proyecto queda dentro del proyecto, y
ninguna puede ser la carpeta personal, una que la contenga ni una que contenga $ALFALFA_HOME.

Para autorizar se enseña la definición cruda, sin resolver: la orden con su ruta completa, los
argumentos, la carpeta, los nombres de las variables que hereda y, de una URL, sólo el host. Los
valores literales de entorno y cabeceras se enseñan si vienen del proyecto (los puso el
repositorio) y se ocultan si vienen de la configuración del usuario.

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
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..mcp_cliente import configuracion as configuracion_mcp
from .formatos import Invalido

ENV_OPENCODE = re.compile(r'\{env:(?P<nombre>[^}]*)\}')
ENV_CLAUDE = re.compile(r'\$\{(?P<nombre>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<defecto>[^}]*))?\}')
ARCHIVO_OPENCODE = re.compile(r'\{file:(?P<ruta>[^}]*)\}')
TIEMPOS = {'tiempo_inicio': (1, 300), 'tiempo_herramienta': (1, 3600)}
ESFUERZOS = ('low', 'medium', 'high', 'xhigh', 'max')
OCULTO = '…'


@dataclass
class Lugar:
    """Dónde vive una definición: para resolver rutas y acotar la carpeta del servidor."""
    base: Path                  # contra la que se resuelven las rutas relativas
    proyecto: Path | None       # la raíz del proyecto si la definición es del proyecto
    casa: Path                  # la carpeta personal
    hogar: Path                 # $ALFALFA_HOME

    @property
    def ambito(self):
        return 'usuario' if self.proyecto is None else 'proyecto'


def _dentro(ruta, carpeta):
    try:
        Path(ruta).relative_to(carpeta)
        return True
    except ValueError:
        return False


def _es_carpeta_git(ruta):
    """Una carpeta `.git` o un repositorio desnudo (HEAD, objects y refs): sus hooks corren fuera del
    sandbox en el siguiente `git commit`."""
    return ruta.name == '.git' or all((ruta / n).exists() for n in ('HEAD', 'objects', 'refs'))


def carpeta_segura(ruta, lugar):
    """La carpeta escribible de un servidor: nunca la personal, una que la contenga, ni nada dentro
    de un `.git` o de $ALFALFA_HOME (ni que lo contenga); la de un servidor del proyecto, dentro del
    proyecto, y el proyecto no puede ser la carpeta personal ni contenerla."""
    pedida = Path(ruta)
    real = pedida.resolve()
    hogar, casa = lugar.hogar.resolve(), lugar.casa.resolve()
    # Se mira la ruta tal como se pidió y la real: un enlace no esconde un `.git`.
    if '.git' in pedida.parts or '.git' in real.parts or any(_es_carpeta_git(c) for c in (real, *real.parents)):
        raise Invalido('la carpeta ' + str(real) + ' está dentro de un .git (sus hooks corren fuera del '
                       'sandbox); no se usa')
    if _dentro(hogar, real) or _dentro(real, hogar):
        raise Invalido('la carpeta ' + str(real) + ' contiene la configuración de AlfalfaCode o está dentro de '
                       'ella; no se usa')
    if real == casa or _dentro(casa, real):
        raise Invalido('la carpeta ' + str(real) + ' es tu carpeta personal o la contiene; no se usa')
    if lugar.proyecto is not None:
        proyecto = lugar.proyecto.resolve()
        if proyecto == casa or _dentro(casa, proyecto):
            raise Invalido('el proyecto es tu carpeta personal o la contiene; no se usa una carpeta suya')
        if not _dentro(real, proyecto):
            raise Invalido('la carpeta ' + str(real) + ' sale del proyecto; no se usa')
    return str(real)


def _resolver(valor, ambiente, patron, campo, recogidos):
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
            valor = configuracion_mcp._heredada(nombre, campo, ambiente)
        except configuracion_mcp._Invalido as exc:
            texto = str(exc)
            if patron is ENV_OPENCODE and nombre not in ambiente:
                texto += ' OpenCode la dejaría vacía; aquí el servidor no se inicia sin ella.'
            raise Invalido(texto) from None
        recogidos.add(valor)
        return valor
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


def cwd_de(valor, lugar):
    """La carpeta declarada, resuelta y acotada (None si no declara ninguna)."""
    if valor is None:
        return None
    if not isinstance(valor, str) or not valor or '\0' in valor:
        raise Invalido('cwd debe ser una ruta')
    ruta = Path(valor).expanduser()
    return carpeta_segura(ruta if ruta.is_absolute() else Path(lugar.base) / ruta, lugar)


# --- servidores MCP ----------------------------------------------------------------
# Cada traductor devuelve (datos para mcp.json, cwd, activo, avisos, secretos resueltos).

def mcp_opencode(entrada, ambiente, lugar):
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser un objeto')
    avisos, secretos = [], set()
    activo = entrada.get('enabled', True) is not False and entrada.get('disabled', False) is not True
    tipo = entrada.get('type')
    if tipo == 'local':
        orden = entrada.get('command')
        if not isinstance(orden, list) or not orden or any(not isinstance(p, str) or not p for p in orden):
            raise Invalido('command debe ser una lista: el ejecutable y sus argumentos')
        datos = dict(orden=_resolver(orden[0], ambiente, ENV_OPENCODE, 'command', secretos),
                     argumentos=[_resolver(a, ambiente, ENV_OPENCODE, 'command', secretos) for a in orden[1:]],
                     entorno={k: _resolver(v, ambiente, ENV_OPENCODE, 'environment.' + k, secretos)
                              for k, v in _mapa(entrada.get('environment'), 'environment').items()})
        cwd = cwd_de(entrada.get('cwd'), lugar)
    elif tipo == 'remote':
        cabeceras = _mapa(entrada.get('headers'), 'headers')
        datos = dict(url=_resolver(entrada.get('url'), ambiente, ENV_OPENCODE, 'url', secretos),
                     cabeceras={k: _resolver(v, ambiente, ENV_OPENCODE, 'headers.' + k, secretos)
                                for k, v in cabeceras.items()})
        cwd = None
        if isinstance(entrada.get('oauth'), dict):
            avisos.append('OAuth de OpenCode no se usa: sólo las cabeceras')
        elif entrada.get('oauth') is not False and not any(k.lower() == 'authorization' for k in cabeceras):
            avisos.append('OpenCode usaría OAuth por defecto; AlfalfaCode sólo manda las cabeceras, así que '
                          'si el servidor pide OAuth no conectará')
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
    return datos, cwd, activo, avisos, secretos


def mcp_codex(entrada, ambiente, lugar):
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser una tabla')
    avisos, secretos = [], set()
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
            if isinstance(variable, str) and variable not in ambiente:
                # Codex sigue sin ella; aquí también, avisando.
                avisos.append('la variable ' + variable + ' de env_vars no está definida; no se pasa')
                continue
            heredadas.append(variable)
        datos = dict(orden=entrada['command'], argumentos=list(argumentos),
                     entorno=_mapa(entrada.get('env'), 'env'))
        if heredadas:
            datos['entorno_heredado'] = heredadas
            secretos |= {ambiente[v] for v in heredadas if isinstance(v, str) and v in ambiente}
        cwd = cwd_de(entrada.get('cwd'), lugar)
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
    return datos, cwd, activo, avisos, secretos


def mcp_claude(entrada, ambiente, lugar):
    if not isinstance(entrada, dict):
        raise Invalido('la entrada del servidor debe ser un objeto')
    secretos = set()
    tipo = entrada.get('type', 'stdio' if 'command' in entrada else 'http')
    if tipo == 'sse':
        raise Invalido('MCP por SSE no está soportado; usa HTTP')
    if tipo == 'stdio':
        argumentos = entrada.get('args', [])
        if not isinstance(argumentos, list):
            raise Invalido('args debe ser una lista de textos')
        datos = dict(orden=_resolver(entrada.get('command'), ambiente, ENV_CLAUDE, 'command', secretos),
                     argumentos=[_resolver(a, ambiente, ENV_CLAUDE, 'args', secretos) for a in argumentos],
                     entorno={k: _resolver(v, ambiente, ENV_CLAUDE, 'env.' + k, secretos)
                              for k, v in _mapa(entrada.get('env'), 'env').items()})
        # Claude Code los lanza en la carpeta del proyecto.
        return datos, carpeta_segura(lugar.base, lugar), True, [], secretos
    if tipo != 'http':
        raise Invalido('type debe ser stdio o http')
    datos = dict(url=_resolver(entrada.get('url'), ambiente, ENV_CLAUDE, 'url', secretos),
                 cabeceras={k: _resolver(v, ambiente, ENV_CLAUDE, 'headers.' + k, secretos)
                            for k, v in _mapa(entrada.get('headers'), 'headers').items()})
    return datos, None, True, [], secretos


# --- lo que se enseña antes de autorizar ----------------------------------------------

def _texto(valor):
    return valor if isinstance(valor, str) else str(valor)


def _valores(mapa, oculto, ambitos=None, lugar=None):
    """NOMBRE=valor; un literal del usuario se oculta, una referencia ({env:X}, ${X}) se enseña tal cual."""
    filas = []
    for nombre, valor in (mapa or {}).items() if isinstance(mapa, dict) else ():
        valor = _texto(valor)
        referencia = ENV_OPENCODE.search(valor) or ENV_CLAUDE.search(valor)
        de_usuario = (ambitos or {}).get(nombre, lugar.ambito if lugar else 'usuario') == 'usuario'
        if oculto and de_usuario and not referencia:
            valor = OCULTO
        filas.append(str(nombre) + '=' + sin_tokens(valor))
    return filas


MAX_ORDEN = 8000
URL_EN_TEXTO = re.compile(r'https?://[^\s\'"]+')


def visible(texto):
    """Sin secuencias de escape ni retornos de carro crudos: no pueden tapar lo que se enseña."""
    return ''.join(c if c.isprintable() else repr(c)[1:-1] for c in str(texto))


def sin_tokens(texto):
    """Tapa lo que en una URL suele ser una credencial (/s/<token>/mcp, ?key=…)."""
    secretos = {secreto for url in URL_EN_TEXTO.findall(texto) for secreto in configuracion_mcp._secretos_de_url(url)
                if len(secreto) >= configuracion_mcp.MIN_SECRETO}
    for secreto in sorted(secretos, key=len, reverse=True):  # el más largo primero: no deja restos
        texto = texto.replace(secreto, OCULTO)
    return texto


def _ejecutable(nombre, entorno, carpeta):
    """La ruta que se lanzará: con el PATH de la propia definición si declara uno (sus tramos
    relativos cuentan desde la carpeta del proceso), y si no con el de quien lo enseña."""
    if '/' in nombre:
        return (Path(carpeta) / Path(nombre).expanduser()).resolve(), False
    ruta = (entorno or {}).get('PATH') if isinstance(entorno, dict) else None
    if isinstance(ruta, str) and ruta:
        tramos = [t if Path(t).is_absolute() else str(Path(carpeta) / t) for t in ruta.split(':') if t]
        encontrado = shutil.which(nombre, path=':'.join(tramos))
        return (Path(encontrado).resolve() if encontrado else None), True
    encontrado = shutil.which(nombre)
    return (Path(encontrado).resolve() if encontrado else None), False


def definicion_mcp(herramienta, entrada, lugar, cwd, ambitos=None, carpeta=None, entorno=None):
    """La definición cruda para decidir si se autoriza (ver el docstring del módulo).

    `carpeta` es donde correrá el proceso (su cwd o la de la sesión) y `entorno`, el entorno ya
    resuelto que recibirá: con ellos se dice qué ejecutable se lanza de verdad."""
    if not isinstance(entrada, dict):
        return {}
    if herramienta == 'OpenCode':
        orden = entrada.get('command') if entrada.get('type') == 'local' else None
        orden = list(orden) if isinstance(orden, list) else None
        url, crudo_entorno, cabeceras = entrada.get('url'), entrada.get('environment'), entrada.get('headers')
        heredadas = []
    elif herramienta == 'Codex':
        orden = [entrada['command'], *entrada.get('args', [])] if 'command' in entrada else None
        url, crudo_entorno, cabeceras = entrada.get('url'), entrada.get('env'), entrada.get('http_headers')
        heredadas = [v.get('name') if isinstance(v, dict) else v for v in entrada.get('env_vars', []) or []]
        heredadas += list((entrada.get('env_http_headers') or {}).values())
        if entrada.get('bearer_token_env_var'):
            heredadas.append(entrada['bearer_token_env_var'])
    else:
        orden = [entrada['command'], *entrada.get('args', [])] if 'command' in entrada else None
        url, crudo_entorno, cabeceras = entrada.get('url'), entrada.get('env'), entrada.get('headers')
        heredadas = []
    fila = {}
    if orden:
        orden = [_texto(p) for p in orden]
        fila['transporte'] = 'stdio'
        # Entera, sin recortar (lo que pase de MAX_ORDEN no se usa), con los tokens de URL tapados.
        fila['orden'] = sin_tokens(shlex.join(orden))
        real, con_path = _ejecutable(orden[0], entorno, carpeta or cwd or lugar.base)
        fila['ejecutable'] = (str(real) if real else orden[0] + ' (no está en el PATH)') + (
            ' (con el PATH de la definición)' if con_path else '')
        fila['del_repositorio'] = bool(real and lugar.proyecto is not None and _dentro(real, lugar.proyecto.resolve()))
        if cwd:
            fila['carpeta'] = cwd
    elif isinstance(url, str):
        fila['transporte'] = 'http'
        # Sólo el host: la ruta o la consulta pueden llevar una clave (/s/<token>/mcp).
        fila['host'] = urlparse(ENV_OPENCODE.sub('X', ENV_CLAUDE.sub('X', url))).hostname or '?'
    entorno_filas = _valores(crudo_entorno, True, ambitos and ambitos.get('environment'), lugar)
    if entorno_filas:
        fila['entorno'] = entorno_filas
    cabecera_filas = _valores(cabeceras, True, ambitos and ambitos.get('headers'), lugar)
    if cabecera_filas:
        fila['cabeceras'] = cabecera_filas
    heredadas = [h for h in heredadas if isinstance(h, str)]
    referencias = [m.group('nombre') for valor in [*(crudo_entorno or {}).values(), *(cabeceras or {}).values(),
                                                    *(orden or []), _texto(url or '')]
                   if isinstance(valor, str) for m in [*ENV_OPENCODE.finditer(valor), *ENV_CLAUDE.finditer(valor)]]
    heredadas = list(dict.fromkeys(heredadas + referencias))
    if heredadas:
        fila['variables'] = heredadas
    return fila


def resumen_mcp(definicion):
    """Una línea: la orden entera o el host."""
    if definicion.get('transporte') == 'stdio':
        orden = definicion['orden']
        corta = orden if len(orden) <= 200 else orden[:200] + '… (sigue: mira la orden entera)'
        return visible('stdio · ' + corta + (' · del repositorio' if definicion.get('del_repositorio') else ''))
    if definicion.get('transporte') == 'http':
        return 'HTTP · ' + definicion['host']
    return ''


def lineas_mcp(definicion, red):
    """La definición en líneas para la terminal y la app."""
    lineas = []
    if definicion.get('transporte') == 'stdio':
        lineas.append('orden: ' + definicion['orden'])
        lineas.append('ejecutable: ' + definicion['ejecutable']
                      + (' (del repositorio)' if definicion.get('del_repositorio') else ''))
        if definicion.get('carpeta'):
            lineas.append('carpeta (la única que puede escribir): ' + definicion['carpeta'])
        lineas.append('internet: ' + ('sí' if red else 'no'))
    elif definicion.get('transporte') == 'http':
        lineas.append('servidor HTTP en ' + definicion['host'])
    for campo, titulo in (('entorno', 'entorno'), ('cabeceras', 'cabeceras'), ('variables', 'variables que lee')):
        if definicion.get(campo):
            lineas.append(titulo + ': ' + ', '.join(definicion[campo]))
    return [visible(linea) for linea in lineas]


# --- agentes -------------------------------------------------------------------------

def _efecto(regla):
    """Lo que dice una regla V1 (texto o mapa de patrones): deny, parcial o None."""
    if isinstance(regla, str):
        return 'deny' if regla == 'deny' else None
    if isinstance(regla, dict) and regla:
        efectos = {v for v in regla.values() if isinstance(v, str)}
        if efectos == {'deny'}:
            return 'deny'
        return 'parcial' if 'deny' in efectos else None
    return None


def _efecto_v2(reglas, acciones):
    """Reglas V2 en orden, la última que encaja gana; un deny limitado a unas rutas no se puede
    expresar aquí, así que cuenta como «parcial» (se aplica entero, por precaución)."""
    efecto, parcial = None, False
    for regla in reglas:
        if not isinstance(regla, dict) or regla.get('action') not in (*acciones, '*'):
            continue
        if regla.get('resource', '*') in ('*', '**'):
            efecto = regla.get('effect')
        elif regla.get('effect') == 'deny':
            parcial = True
    if efecto == 'deny':
        return 'deny'
    return 'parcial' if parcial else None


def agente_opencode(datos, cuerpo, base):
    """Tipo de subagente de AlfalfaCode (como los de subagentes.cargar_tipos) desde OpenCode.

    `prompt` (V1) o `system` (V2) o el cuerpo del .md son las instrucciones; `{file:ruta}` lee
    un archivo de la misma carpeta del proyecto. Editar denegado (`permission.edit: deny`,
    `tools.write/edit: false` o reglas V2, la última gana) lo deja de sólo lectura y `bash`
    denegado le quita run_command. Un deny limitado a unas rutas no se puede limitar aquí: se
    aplica entero y se avisa. `provider/model` no es un modelo de AlfalfaCode: hereda el de la
    sesión y la variante (`#high` o `variant`) pasa a esfuerzo si es uno conocido."""
    instrucciones = cuerpo or datos.get('system') or datos.get('prompt') or ''
    if not isinstance(instrucciones, str):
        raise Invalido('prompt/system debe ser texto')
    archivo = ARCHIVO_OPENCODE.fullmatch(instrucciones.strip())
    if archivo:
        instrucciones = _archivo_de_prompt(archivo.group('ruta'), base)
    permisos = datos.get('permission') if isinstance(datos.get('permission'), dict) else {}
    herramientas = datos.get('tools') if isinstance(datos.get('tools'), dict) else {}
    reglas = datos.get('permissions') if isinstance(datos.get('permissions'), list) else []
    edicion = {_efecto(permisos.get('edit')), _efecto(permisos.get('write')), _efecto_v2(reglas, ('edit',))}
    orden = {_efecto(permisos.get('bash')), _efecto_v2(reglas, ('shell', 'bash'))}
    avisos = []
    if 'parcial' in edicion or 'parcial' in orden:
        avisos.append('niega editar u ordenar sólo en algunas rutas; aquí no se puede limitar por ruta y se '
                      'niega entero')
    solo_lectura = (bool(edicion & {'deny', 'parcial'})
                    or (herramientas.get('write') is False and herramientas.get('edit') is False))
    sin_orden = bool(orden & {'deny', 'parcial'}) or herramientas.get('bash') is False
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
            'esfuerzo': variante if variante in ESFUERZOS else None, 'avisos': avisos}


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
MARCADOR = re.compile(r'\$ARGUMENTS|\{\{args\}\}|\$(\d+)')


def expandir_plantilla(plantilla, argumentos):
    """$ARGUMENTS y $1, $2… (el más alto se lleva el resto) como OpenCode y Claude Code.

    Una sola pasada: lo que escribe el usuario no se vuelve a sustituir y `$10` es el décimo.
    Sin marcadores, los argumentos van detrás tras una línea en blanco. `` !`orden` `` no se
    ejecuta aquí: queda como petición al agente, que la corre con los permisos de siempre."""
    argumentos = (argumentos or '').strip()
    try:
        partes = shlex.split(argumentos)
    except ValueError:
        partes = argumentos.split()
    marcas = list(MARCADOR.finditer(plantilla))
    tope = max((int(m.group(1)) for m in marcas if m.group(1)), default=0)

    def cambiar(m):
        if not m.group(1):
            return argumentos
        n = int(m.group(1))
        if n == 0:
            return m.group(0)
        if n == tope:
            return ' '.join(partes[n - 1:])
        return partes[n - 1] if n <= len(partes) else ''
    plantilla = ORDEN_EN_LINEA.sub(lambda m: '(ejecuta `' + m.group(1) + '` y usa su salida)', plantilla)
    texto = MARCADOR.sub(cambiar, plantilla)
    if argumentos and not marcas:
        texto = texto.rstrip() + '\n\n' + argumentos
    return texto
