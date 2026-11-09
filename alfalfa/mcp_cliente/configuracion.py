"""Configuración de servidores MCP de AlfalfaCode: $ALFALFA_HOME/mcp.json.

Formato (todas las claves en español; cualquier clave desconocida invalida el
servidor, para que una errata en «deshabilitadas» no active herramientas):

    {"servidores": {
        "docs": {"orden": "/ruta/servidor", "argumentos": ["--stdio"],
                 "entorno": {"NIVEL": "info"}, "entorno_heredado": ["GITHUB_TOKEN"],
                 "red": false},
        "remoto": {"url": "https://mcp.example.com/mcp",
                   "cabeceras": {"X-Cliente": "alfalfa"},
                   "cabeceras_entorno": {"X-Api-Key": "REMOTO_API_KEY"},
                   "token_entorno": "REMOTO_TOKEN"}}}

Campos comunes: habilitadas (lista o null = todas), deshabilitadas (prevalece
sobre habilitadas), directas (se exponen con esquema sin buscar), confianza
(nombres originales exactos que se llaman sin preguntar), tiempo_inicio,
tiempo_herramienta, tiempo_permiso (segundos), max_bytes_esquema y activo.

max_bytes_esquema mide como tool_input_schema_max_bytes de Codex (bytes UTF-8
del JSON compacto; cabe si es <= tope), pero al superarlo rechaza la
herramienta en vez de compactarla con pérdida: Codex quita descripciones y
definiciones en silencio (tools/src/json_schema/compaction.rs, 5000 B por
defecto), y aquí un esquema nunca se altera sin aviso. Por eso el
predeterminado es más alto (16384 B, cerca del de 16 000 B de Code Mode).

Una entrada inválida produce un diagnóstico y ese servidor no se inicia; los
demás siguen. Nada de este módulo lanza procesos ni abre conexiones.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ARCHIVO = 'mcp.json'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,23}')
VARIABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
CABECERA = re.compile(r'[A-Za-z0-9-]{1,64}')
# Nunca llegan a un servidor MCP aunque se declaren.
VARIABLES_PROHIBIDAS = frozenset({'OLLAMA_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ALFALFA_CODIGO',
                                  'OPENROUTER_API_KEY', 'OPENCODE_API_KEY'})
CABECERAS_PROHIBIDAS = frozenset({'host', 'content-length', 'transfer-encoding', 'connection',
                                  'mcp-session-id', 'mcp-protocol-version'})
# Su valor es «<esquema> <credencial>»; secretos() redacta también la credencial sola.
CABECERAS_CREDENCIAL = frozenset({'authorization', 'proxy-authorization'})
MAX_SERVIDORES = 32
MAX_LISTA = 1000
MAX_BYTES_ESQUEMA = 16384
LIMITES_BYTES_ESQUEMA = (256, 1024 * 1024)
TIEMPOS = {'tiempo_inicio': (30.0, 1, 300), 'tiempo_herramienta': (120.0, 1, 3600),
           'tiempo_permiso': (600.0, 1, 3600)}
COMUNES = {'habilitadas', 'deshabilitadas', 'directas', 'confianza', 'max_bytes_esquema', 'activo',
           *TIEMPOS}
STDIO = {'orden', 'argumentos', 'entorno', 'entorno_heredado', 'red'}
HTTP = {'url', 'cabeceras', 'cabeceras_entorno', 'token_entorno'}
# Los valores declarados de menos longitud («1», «info») no se redactan: no
# son credenciales y redactarlos dañaría cualquier texto.
MIN_SECRETO = 8


@dataclass
class Diagnostico:
    codigo: str
    texto: str
    servidor: str | None = None
    grave: bool = True

    def como_dict(self):
        return dict(codigo=self.codigo, servidor=self.servidor, texto=self.texto, grave=self.grave)


@dataclass
class Servidor:
    id: str
    transporte: str  # 'stdio' | 'http'
    orden: str | None = None
    argumentos: list = field(default_factory=list)
    entorno: dict = field(default_factory=dict)
    url: str | None = None
    cabeceras: dict = field(default_factory=dict)
    red: bool = False
    habilitadas: frozenset | None = None
    deshabilitadas: frozenset = frozenset()
    directas: tuple = ()
    confianza: frozenset = frozenset()
    tiempo_inicio: float = 30.0
    tiempo_herramienta: float = 120.0
    tiempo_permiso: float = 600.0
    max_bytes_esquema: int = MAX_BYTES_ESQUEMA
    # Metadatos de procedencia; sólo los rellena el cargador de complementos.
    cwd: str | None = None
    procedencia: str | None = None
    complemento: str | None = None
    nombre_original: str | None = None
    # Valores que salieron de variables del usuario al importar una configuración ajena
    # (alfalfa/importadas): se redactan igual que el entorno y las cabeceras.
    secretos_extra: frozenset = frozenset()

    def habilitada(self, original):
        """deshabilitadas prevalece; habilitadas None significa todas."""
        if original in self.deshabilitadas:
            return False
        return self.habilitadas is None or original in self.habilitadas

    def secretos(self):
        valores = list(self.entorno.values()) + list(self.cabeceras.values())
        # «Bearer <token>» sólo viaja completo en la cabecera: el servidor puede
        # devolver el token suelto, así que también se redacta la credencial.
        for nombre, valor in self.cabeceras.items():
            if nombre.lower() in CABECERAS_CREDENCIAL and isinstance(valor, str):
                partes = valor.split(None, 1)
                if partes:
                    valores.append(partes[-1].strip())
        valores += list(self.secretos_extra) + _secretos_de_url(self.url)
        # También las URL que van en los argumentos (`mcp-remote https://…/s/<token>/mcp`).
        for argumento in self.argumentos:
            for url in re.findall(r'https?://[^\s\'"]+', argumento if isinstance(argumento, str) else ''):
                valores += _secretos_de_url(url)
        return {v for v in valores if isinstance(v, str) and len(v) >= MIN_SECRETO}


def _secretos_de_url(url):
    """Lo que en una URL suele ser una credencial: los valores de la consulta y los tramos de la
    ruta con pinta de token (largos y con letras y cifras, como /api/mcp/s/<token>/mcp)."""
    if not isinstance(url, str) or not url:
        return []
    from urllib.parse import parse_qsl
    partes = urlparse(url)
    valores = [v for _, v in parse_qsl(partes.query, keep_blank_values=False)]
    valores += [t for t in partes.path.split('/') if len(t) >= 16 and re.search(r'\d', t)
                and re.search(r'[A-Za-z]', t)]
    return valores


class _Invalido(ValueError):
    def __init__(self, codigo, texto):
        super().__init__(texto)
        self.codigo = codigo


def _nombres(valor, campo):
    if valor is None and campo == 'habilitadas':
        return None
    if (not isinstance(valor, list) or len(valor) > MAX_LISTA
            or any(not isinstance(v, str) or not v or len(v) > 256 or '\0' in v for v in valor)):
        raise _Invalido('MCP-CONFIG-TIPO', campo + ' debe ser una lista de nombres de herramienta.')
    return valor


def _texto(valor, campo):
    if not isinstance(valor, str) or not valor or '\0' in valor or len(valor) > 4096:
        raise _Invalido('MCP-CONFIG-TIPO', campo + ' debe ser texto no vacío sin caracteres nulos.')
    return valor


def _variable(nombre, campo):
    if not isinstance(nombre, str) or not VARIABLE.fullmatch(nombre):
        raise _Invalido('MCP-CONFIG-ENTORNO', campo + ': nombre de variable inválido.')
    if nombre in VARIABLES_PROHIBIDAS:
        raise _Invalido('MCP-CONFIG-ENTORNO', campo + ': ' + nombre + ' no se entrega a servidores MCP.')
    return nombre


def _heredada(nombre, campo, ambiente):
    _variable(nombre, campo)
    if nombre not in ambiente:
        raise _Invalido('MCP-CONFIG-ENTORNO', campo + ': la variable ' + nombre + ' no está definida.')
    # Tampoco con otro nombre: un integrador guarda su clave de proveedor en su
    # propia variable (p. ej. <APP>_OLLAMA_API_KEY) y la registra en `ajustes`.
    try:
        from ..proveedores import secretos
        conocidas = set(secretos())
    except Exception:
        conocidas = set()
    if ambiente[nombre] in conocidas:
        raise _Invalido('MCP-CONFIG-ENTORNO', campo + ': ' + nombre + ' lleva una clave de proveedor y no se '
                        'entrega a servidores MCP.')
    return ambiente[nombre]


def _url(valor):
    _texto(valor, 'url')
    url = urlparse(valor)
    if url.username or url.password:
        raise _Invalido('MCP-CONFIG-URL', 'url no puede llevar credenciales; usa cabeceras o token_entorno.')
    if url.fragment or not url.hostname:
        raise _Invalido('MCP-CONFIG-URL', 'url debe ser http(s)://host/ruta sin fragmento.')
    local = url.hostname in ('localhost',)
    try:
        local = local or ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        pass
    # Igual que el endpoint de Ollama: texto claro sólo hacia este equipo.
    if url.scheme != 'https' and not (url.scheme == 'http' and local):
        raise _Invalido('MCP-CONFIG-URL', 'url remota requiere HTTPS; http sólo para localhost.')
    return valor


def _tiempo(valor, campo):
    _, minimo, maximo = TIEMPOS[campo]
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not minimo <= valor <= maximo:
        raise _Invalido('MCP-CONFIG-TIPO', campo + ' debe ser un número de ' + str(minimo) + ' a '
                        + str(maximo) + ' segundos.')
    return float(valor)


def _servidor(ident, datos, ambiente):
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise _Invalido('MCP-CONFIG-ID', 'Identificador de servidor inválido: usa letras, cifras y guiones '
                        '(hasta 24, sin guion bajo).')
    if not isinstance(datos, dict):
        raise _Invalido('MCP-CONFIG-TIPO', 'La entrada del servidor debe ser un objeto.')
    desconocidas = sorted(set(datos) - COMUNES - STDIO - HTTP)
    if desconocidas:
        raise _Invalido('MCP-CONFIG-CAMPO', 'Campos desconocidos: ' + ', '.join(desconocidas) + '.')
    es_stdio, es_http = 'orden' in datos, 'url' in datos
    if es_stdio == es_http:
        raise _Invalido('MCP-CONFIG-TRANSPORTE', 'Indica exactamente uno: orden (stdio) o url (HTTP).')
    ajenos = sorted(set(datos) & (HTTP if es_stdio else STDIO))
    if ajenos:
        raise _Invalido('MCP-CONFIG-CAMPO', 'Campos de otro transporte: ' + ', '.join(ajenos) + '.')
    servidor = Servidor(id=ident, transporte='stdio' if es_stdio else 'http')
    if es_stdio:
        servidor.orden = _texto(datos['orden'], 'orden')
        argumentos = datos.get('argumentos', [])
        if (not isinstance(argumentos, list) or len(argumentos) > 200
                or any(not isinstance(a, str) or '\0' in a for a in argumentos)):
            raise _Invalido('MCP-CONFIG-TIPO', 'argumentos debe ser una lista de textos.')
        servidor.argumentos = list(argumentos)
        literal = datos.get('entorno', {})
        if not isinstance(literal, dict):
            raise _Invalido('MCP-CONFIG-TIPO', 'entorno debe ser un objeto NOMBRE: valor.')
        for nombre, valor in literal.items():
            servidor.entorno[_variable(nombre, 'entorno')] = _texto(valor, 'entorno.' + nombre)
        heredadas = datos.get('entorno_heredado', [])
        if not isinstance(heredadas, list):
            raise _Invalido('MCP-CONFIG-TIPO', 'entorno_heredado debe ser una lista de nombres.')
        for nombre in heredadas:
            servidor.entorno[nombre] = _heredada(nombre, 'entorno_heredado', ambiente)
        red = datos.get('red', False)
        if not isinstance(red, bool):
            raise _Invalido('MCP-CONFIG-TIPO', 'red debe ser true o false.')
        servidor.red = red
    else:
        servidor.url = _url(datos['url'])
        literal = datos.get('cabeceras', {})
        desde_entorno = datos.get('cabeceras_entorno', {})
        if not isinstance(literal, dict) or not isinstance(desde_entorno, dict):
            raise _Invalido('MCP-CONFIG-TIPO', 'cabeceras y cabeceras_entorno deben ser objetos.')
        pares = [(k, _texto(v, 'cabeceras.' + str(k))) for k, v in literal.items()]
        pares += [(k, _heredada(v, 'cabeceras_entorno.' + str(k), ambiente)) for k, v in desde_entorno.items()]
        if 'token_entorno' in datos:
            token = _heredada(datos['token_entorno'], 'token_entorno', ambiente)
            # «Bearer » + vacío pasaría la comprobación de abajo: se valida el token solo.
            pares.append(('Authorization', 'Bearer ' + token if token.strip() else ''))
        for nombre, valor in pares:
            if not isinstance(nombre, str) or not CABECERA.fullmatch(nombre):
                raise _Invalido('MCP-CONFIG-TIPO', 'Nombre de cabecera inválido.')
            if nombre.lower() in CABECERAS_PROHIBIDAS:
                raise _Invalido('MCP-CONFIG-TIPO', 'La cabecera ' + nombre + ' la gestiona el cliente MCP.')
            # Vacía o en blanco no autentica nada y sólo enmascara un error de configuración.
            if not valor.strip():
                raise _Invalido('MCP-CONFIG-TIPO', 'La cabecera ' + nombre + ' está vacía o en blanco.')
            if any(c in valor for c in '\r\n'):
                raise _Invalido('MCP-CONFIG-TIPO', 'La cabecera ' + nombre + ' contiene saltos de línea.')
            if any(k.lower() == nombre.lower() for k in servidor.cabeceras):
                raise _Invalido('MCP-CONFIG-TIPO', 'Cabecera repetida: ' + nombre + '.')
            servidor.cabeceras[nombre] = valor
    if 'habilitadas' in datos:
        habilitadas = _nombres(datos['habilitadas'], 'habilitadas')
        servidor.habilitadas = None if habilitadas is None else frozenset(habilitadas)
    servidor.deshabilitadas = frozenset(_nombres(datos.get('deshabilitadas', []), 'deshabilitadas'))
    servidor.directas = tuple(dict.fromkeys(_nombres(datos.get('directas', []), 'directas')))
    servidor.confianza = frozenset(_nombres(datos.get('confianza', []), 'confianza'))
    for campo, (defecto, _, _) in TIEMPOS.items():
        setattr(servidor, campo, _tiempo(datos.get(campo, defecto), campo))
    tope = datos.get('max_bytes_esquema', MAX_BYTES_ESQUEMA)
    if (isinstance(tope, bool) or not isinstance(tope, int)
            or not LIMITES_BYTES_ESQUEMA[0] <= tope <= LIMITES_BYTES_ESQUEMA[1]):
        raise _Invalido('MCP-CONFIG-TIPO', 'max_bytes_esquema debe ser un entero de '
                        + str(LIMITES_BYTES_ESQUEMA[0]) + ' a ' + str(LIMITES_BYTES_ESQUEMA[1]) + '.')
    servidor.max_bytes_esquema = tope
    return servidor


def avisos(servidor):
    """Conflictos que no invalidan el servidor: se resuelven en el sentido restrictivo."""
    filas = []
    for campo, nombres in (('habilitadas', servidor.habilitadas or frozenset()),
                           ('directas', frozenset(servidor.directas)), ('confianza', servidor.confianza)):
        conflicto = sorted(nombres & servidor.deshabilitadas)
        if conflicto:
            filas.append(Diagnostico('MCP-CONFLICTO', 'En ' + campo + ' y deshabilitadas a la vez (queda '
                                     'deshabilitada): ' + ', '.join(conflicto)[:500] + '.', servidor.id, False))
    return filas


def cargar(home, ambiente=None):
    """(servidores válidos, diagnósticos). Sin archivo no hay MCP ni diagnósticos."""
    ambiente = dict(os.environ if ambiente is None else ambiente)
    ruta = Path(home) / ARCHIVO
    if not ruta.exists() and not ruta.is_symlink():
        return [], []
    try:
        if ruta.is_symlink() or not ruta.is_file():
            raise ValueError('debe ser un archivo regular, no un enlace')
        if ruta.stat().st_size > 1024 * 1024:
            raise ValueError('supera 1 MB')
        datos = json.loads(ruta.read_text(encoding='utf-8'))
        if not isinstance(datos, dict) or set(datos) - {'servidores'} or not isinstance(datos.get('servidores'), dict):
            raise ValueError('se esperaba {"servidores": {id: {...}}}')
    except (OSError, ValueError) as exc:
        # No se cita el contenido: podría llevar credenciales literales.
        motivo = exc.args[0] if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) \
            else type(exc).__name__
        return [], [Diagnostico('MCP-CONFIG-JSON', ARCHIVO + ' inválido: ' + str(motivo)[:200] + '.')]
    servidores, diagnosticos = [], []
    for ident, entrada in datos['servidores'].items():
        if len(servidores) >= MAX_SERVIDORES:
            diagnosticos.append(Diagnostico('MCP-CONFIG-LIMITE', 'Más de ' + str(MAX_SERVIDORES)
                                            + ' servidores; se ignoran los restantes.', str(ident)[:24]))
            break
        nombre = ident if isinstance(ident, str) and ID.fullmatch(ident) else None
        try:
            if isinstance(entrada, dict) and entrada.get('activo', True) is False:
                continue
            if isinstance(entrada, dict) and not isinstance(entrada.get('activo', True), bool):
                raise _Invalido('MCP-CONFIG-TIPO', 'activo debe ser true o false.')
            servidor = _servidor(ident, entrada, ambiente)
        except _Invalido as exc:
            diagnosticos.append(Diagnostico(exc.codigo, str(exc) + ' El servidor no se inicia.', nombre))
            continue
        servidores.append(servidor)
        diagnosticos.extend(avisos(servidor))
    return servidores, diagnosticos
