"""Carga pasiva de complementos Codex, Claude Code, Cursor y Agent Plugins.

Precedencia de manifiestos, traducida de OpenAI Codex (Apache-2.0) en la
revisión `faeb379646bdbad016e11f7c9ca91113cad4bc9d`; procedencia y cambios en
`PROCEDENCIA.md`, avisos en `NOTICE`:

- `plugin.json` raíz sólo cuenta si declara un `$schema` de agent-plugins
  (`plugin_namespace.rs`). Si la versión es no soportada se rechaza; si el
  `$schema` es ajeno o falta, el archivo se ignora y se sigue con la lista.
- Después, en este orden exacto y sólo el primero existente:
  `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`,
  `.cursor-plugin/plugin.json` (`DISCOVERABLE_PLUGIN_MANIFEST_PATHS`,
  `exec-server-protocol/src/protocol.rs`). Un segundo manifiesto no se carga:
  una raíz produce un complemento como mucho.
- Las rutas declaradas en el manifiesto exigen prefijo `./`, no admiten `..`
  ni rutas absolutas y deben resolver dentro de la raíz (`manifest.rs`,
  `resolve_manifest_path`); la ruta MCP declarada sigue la misma regla.
- Los campos opcionales fuera del contrato Agent Plugins 1.0.0 se avisan y se
  ignoran, en vez de invalidar el manifiesto (`agent_plugin_manifest.rs`).
  Se conservan las capacidades compatibles, que son las únicas que se leen.
- Valores predeterminados por formato (`loader.rs`): `skills/` y `commands/`;
  MCP en `mcp.json` para Agent Plugins y en `.mcp.json` para el formato legado.

Además, como Claude Code y Codex, lee habilidades sueltas (`SKILL.md`) sin
complemento: las del usuario (~/.claude/skills, ~/.codex/skills, ~/.agents/skills,
$ALFALFA_HOME/skills) y las del proyecto (.claude/skills, .agents/skills,
.codex/skills, .alfalfa/skills). Sólo guardan nombre, descripción y hash; el texto
se abre con abrir_habilidad. `"habilidades_sueltas": false` en complementos.json
las desactiva.

Nada de este módulo ejecuta hooks, comandos, conectores ni procesos MCP: sólo
lee archivos autorizados. Ningún campo del complemento concede confianza,
herramientas directas ni herencia de secretos. Un complemento se registra
únicamente cuando terminó de validarse: un rechazo no deja capacidades vivas.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..mcp_cliente import configuracion as configuracion_mcp
from . import configuracion as configuracion_complementos

SCHEMA = 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
PREFIJO_ESQUEMA = 'https://agent-plugins.org/schemas/'
ARCHIVO = 'complementos.json'
# Orden de DISCOVERABLE_PLUGIN_MANIFEST_PATHS (Codex, protocol.rs); sólo el primero
# existente se carga, así que una raíz nunca produce dos complementos.
MANIFIESTOS = (('codex', '.codex-plugin/plugin.json'),
               ('claude', '.claude-plugin/plugin.json'),
               ('cursor', '.cursor-plugin/plugin.json'))
# plugin_namespace.rs: el portable raíz sólo cuenta si declara agent-plugins.
PORTABLE = ('agent-plugins', 'plugin.json')
MAX_ARCHIVO = 1024 * 1024
# Carpeta personal donde buscar ~/.claude/skills y compañía; None es la del usuario.
# Las pruebas la apuntan a una carpeta vacía para no leer las habilidades de quien las corre.
CASA = None
MAX_CAPACIDADES = 256
NOMBRE = re.compile(r'^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$')
# Marcadores de una plantilla de comando: $ARGUMENTS, {{args}} y $1…$9.
_MARCADOR = re.compile(r'\$ARGUMENTS|\{\{args\}\}|\$([1-9])')
RUTAS_MANIFIESTO = ('skills', 'commands', 'mcp', 'mcpServers', 'hooks', 'agents')
# Campos del manifiesto Agent Plugins 1.0.0 (plugin.schema.json, additionalProperties
# false) más los que admite el formato legado de Codex y Claude Code.
AGENT_PLUGINS = {'$schema', 'name', 'version', 'description', 'author', 'homepage', 'repository',
                 'license', 'keywords', 'extensions'}
LEGADO = {'skills', 'commands', 'mcpServers', 'mcp', 'hooks', 'agents', 'interface', 'apps',
          'documentationUrl', 'supportUrl', 'privacyPolicyUrl', 'termsOfServiceUrl', 'icon',
          'displayName', 'category', 'brandColor', 'logo', 'screenshots', 'composerIcon', 'logoDark'}
COMUNES = AGENT_PLUGINS | LEGADO
# Campos conocidos que no producen ninguna capacidad en AlfalfaCode: se
# enumeran como ignorados y avisados, nunca se finge soporte.
SIN_CAPACIDAD = {'interface', 'agents', 'apps', 'documentationUrl', 'supportUrl', 'privacyPolicyUrl',
                 'termsOfServiceUrl', 'icon', 'displayName', 'category', 'brandColor', 'logo',
                 'screenshots', 'composerIcon', 'logoDark'}


class ErrorComplemento(ValueError):
    def __init__(self, codigo, texto):
        super().__init__(texto)
        self.codigo, self.texto = codigo, texto


@dataclass(frozen=True)
class Diagnostico:
    codigo: str
    texto: str
    complemento: str | None = None
    capacidad: str | None = None
    grave: bool = True

    def como_dict(self):
        return dict(codigo=self.codigo, texto=self.texto, complemento=self.complemento,
                    capacidad=self.capacidad, grave=self.grave)


@dataclass
class Habilidad:
    id: str
    nombre: str
    descripcion: str
    ruta: str
    sha256: str

    def como_dict(self):
        return dict(id=self.id, nombre=self.nombre, descripcion=self.descripcion, ruta=self.ruta,
                    referencia=self.id + '@sha256:' + self.sha256)


@dataclass
class Comando:
    id: str
    ruta: str
    descripcion: str
    _texto: str = field(repr=False)

    def invocar(self, argumentos=''):
        """Devuelve una plantilla de prompt; nunca interpreta ni ejecuta su texto."""
        if not isinstance(argumentos, str) or len(argumentos) > 20000:
            raise ErrorComplemento('COMP-ARGUMENTOS', 'Los argumentos deben ser texto de hasta 20000 caracteres.')
        # $1…$9 se sustituyen por palabras con comillas respetadas; no por shell.
        import shlex
        try:
            partes = shlex.split(argumentos)
        except ValueError as exc:
            raise ErrorComplemento('COMP-ARGUMENTOS', 'Argumentos con comillas incompletas: ' + str(exc)) from None

        # Una sola pasada sobre la plantilla original. Sustituir por
        # tandas reprocesaba lo ya insertado ($ARGUMENTS="$1" devolvía
        # «literal literal»); aquí el texto del usuario nunca se reinterpreta.
        def sustituir(marcador):
            encontrado = marcador.group(0)
            if encontrado == '$ARGUMENTS' or encontrado == '{{args}}':
                return argumentos
            indice = int(marcador.group(1))
            return partes[indice - 1] if indice <= len(partes) else ''

        texto = _MARCADOR.sub(sustituir, self._texto)
        return dict(comando=self.id, texto=texto, ejecucion=False,
                    nota='Texto para revisar como prompt; no se ejecutó ni concedió ningún permiso.')


@dataclass
class Complemento:
    identidad: str
    nombre: str
    version: str | None
    raiz: Path
    formato: str
    manifiesto: str
    descripcion: str = ''
    habilidades: dict = field(default_factory=dict)
    comandos: dict = field(default_factory=dict)
    servidores: dict = field(default_factory=dict)
    # Fuentes de hooks ya leídas ({procedencia, datos}); las ejecuta arnes/hooks.
    hooks: list = field(default_factory=list)
    diagnosticos: list = field(default_factory=list)
    ignoradas: list = field(default_factory=list)

    def aviso(self, codigo, texto, capacidad=None, grave=True):
        self.diagnosticos.append(Diagnostico(codigo, texto[:600], self.identidad, capacidad, grave))

    def como_dict(self):
        return dict(identidad=self.identidad, nombre=self.nombre, version_disponible=self.version,
                    raiz=str(self.raiz), formato=self.formato, manifiesto=self.manifiesto,
                    descripcion=self.descripcion,
                    # Orden estable por identificador: el JSON no depende del orden de lectura.
                    capacidades=dict(habilidades=[self.habilidades[k].como_dict() for k in sorted(self.habilidades)],
                                     comandos=[dict(id=self.comandos[k].id, ruta=self.comandos[k].ruta,
                                                    descripcion=self.comandos[k].descripcion)
                                               for k in sorted(self.comandos)],
                                     servidores_mcp=sorted(self.servidores),
                                     servidores=[dict(id=k, original=self.servidores[k]['original'],
                                                      procedencia=self.servidores[k]['procedencia'],
                                                      transporte=self.servidores[k]['servidor'].transporte)
                                                 for k in sorted(self.servidores)],
                                     hooks=[h['procedencia'] for h in self.hooks]),
                    # Archivos presentes que no se interpretaron: nunca conectores.
                    ignoradas=sorted(self.ignoradas),
                    diagnosticos=[dict(d.como_dict()) for d in self.diagnosticos])


def _manifiesto_contenido(root, ruta, relativa):
    """Ruta real del manifiesto, comprobada dentro de la raíz autorizada.

    `ruta.is_symlink()` no ve un enlace en el directorio padre: si
    `.codex-plugin` es un enlace a una carpeta externa, `plugin.json` es un
    archivo regular alcanzado por ese enlace. Se resuelve el manifiesto completo
    y se compara con la raíz, como `resolve_manifest_path` de manifest.rs.
    """
    try:
        real = ruta.resolve(strict=True)
    except OSError:
        raise ErrorComplemento('COMP-ARCHIVO', 'El manifiesto ' + relativa
                               + ' no existe o es inaccesible.') from None
    try:
        real.relative_to(root)
    except ValueError:
        raise ErrorComplemento('COMP-RUTA', 'El manifiesto ' + relativa
                               + ' o el enlace de su carpeta escapa de la raíz autorizada.') from None
    return real


def _json_regular(ruta, limite=MAX_ARCHIVO):
    if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > limite:
        raise ErrorComplemento('COMP-ARCHIVO', 'Debe ser un archivo regular de hasta 1 MB, no un enlace.')
    try:
        valor = json.loads(ruta.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ErrorComplemento('COMP-JSON', 'JSON ilegible (' + type(exc).__name__ + '); no se carga.') from None
    if not isinstance(valor, dict):
        raise ErrorComplemento('COMP-MANIFIESTO', 'El documento JSON debe ser un objeto.')
    return valor


def _texto(datos, campo, defecto='', maximo=4096):
    valor = datos.get(campo, defecto)
    if not isinstance(valor, str) or len(valor) > maximo or '\0' in valor:
        raise ErrorComplemento('COMP-MANIFIESTO', campo + ' debe ser texto de hasta ' + str(maximo) + ' caracteres.')
    return valor


def _normalizar_id(nombre):
    return re.sub(r'[^a-z0-9.-]+', '-', nombre.lower()).strip('-')[:64]


def _exigir_relativa(valor):
    """Reglas de `resolve_manifest_path` (manifest.rs de Codex).

    Una ruta declarada en el manifiesto empieza por `./`, no puede ser la raíz
    `./`, no puede contener un componente `..` y debe seguir siendo relativa.
    Sin esta comprobación `./skills/../skills` y `./` llegaban hasta la
    resolución final y sólo entonces, o nunca, se rechazaban.
    """
    if not valor.startswith('./'):
        raise ErrorComplemento('COMP-RUTA', 'Una ruta declarada en el manifiesto debe empezar por ./')
    resto = valor[2:]
    # `./`, `./.` y `././` nombran la propia raíz: Codex rechaza `./` porque el
    # resto queda vacío; aquí también las variantes equivalentes.
    if not re.search(r'[^./]', resto):
        raise ErrorComplemento('COMP-RUTA', 'Una ruta declarada no puede ser la raíz ./; '
                               'nombra una carpeta o un archivo.')
    if any(parte == '..' for parte in re.split(r'[\\/]', resto)):
        raise ErrorComplemento('COMP-RUTA', "Una ruta declarada no puede contener '..'")
    if resto.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', resto):
        raise ErrorComplemento('COMP-RUTA', 'Una ruta declarada debe quedar dentro de la raíz del complemento.')


def _segura(root, valor, defecto=None, obligar_relativa=False):
    """Ruta declarada, real y contenida. Un enlace interno sí; cualquier escape, no."""
    valor = defecto if valor is None else valor
    if not isinstance(valor, str) or not valor or '\0' in valor or len(valor) > 4096:
        raise ErrorComplemento('COMP-RUTA', 'La ruta declarada debe ser texto no vacío.')
    if obligar_relativa:
        _exigir_relativa(valor)
    relativa = Path(valor)
    if relativa.is_absolute():
        raise ErrorComplemento('COMP-RUTA', 'No se admiten rutas absolutas dentro del complemento.')
    candidata = root / relativa
    try:
        resuelta = candidata.resolve(strict=True)
    except OSError:
        # Antes de darla por ausente: ¿el literal o su parte existente escapa?
        # Un escape nunca debe quedar como simple «ruta ausente».
        try:
            _contenida(root, candidata)
        except ErrorComplemento:
            raise
        except OSError:
            pass
        raise
    try:
        resuelta.relative_to(root)
    except ValueError:
        raise ErrorComplemento('COMP-RUTA', 'La ruta o su enlace simbólico escapa de la raíz autorizada.') from None
    return candidata, resuelta


def _contenida(root, ruta):
    """Comprueba el objetivo sin exigir que el archivo ya exista."""
    resuelta = ruta.resolve(strict=False)
    try:
        resuelta.relative_to(root)
    except ValueError:
        raise ErrorComplemento('COMP-RUTA', 'La ruta o su enlace escapa de la raíz autorizada.') from None
    return resuelta


def _front_matter(texto):
    """Sólo name/description, sin depender de YAML ni interpretar el cuerpo."""
    lineas = texto.splitlines()
    if not lineas or lineas[0].strip() != '---':
        return {}
    resultado = {}
    for linea in lineas[1:]:
        if linea.strip() == '---':
            break
        if ':' not in linea or linea[:1].isspace():
            continue
        clave, valor = linea.split(':', 1)
        if clave.strip() in ('name', 'description'):
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
                valor = valor[1:-1]
            resultado[clave.strip()] = valor[:4096]
    return resultado


def _cuerpo(texto):
    """Quita el bloque de front matter: se expone lo que sigue al segundo «---»."""
    lineas = texto.splitlines(keepends=True)
    if not lineas or lineas[0].strip() != '---':
        return texto
    for indice in range(1, len(lineas)):
        if lineas[indice].strip() == '---':
            return ''.join(lineas[indice + 1:]).lstrip('\r\n')
    return ''


def _ruta_declarada(raiz, valor, campo):
    """Ruta absoluta ya expandida: debe existir y quedar dentro de la raíz.

    Tras sustituir ${PLUGIN_ROOT} el valor deja de ser relativo, así que no se
    reaplica _segura (que rechaza rutas absolutas): se compara la ruta real.
    """
    if not isinstance(valor, str) or not valor or '\0' in valor or len(valor) > 4096:
        raise ErrorComplemento('COMP-MCP-RUTA', campo + ' debe ser texto no vacío.')
    candidata = Path(valor)
    if not candidata.is_absolute():
        candidata = raiz / candidata
    try:
        real = candidata.resolve(strict=True)
    except OSError:
        raise ErrorComplemento('COMP-MCP-RUTA', campo + ' no existe o es inaccesible.') from None
    if not real.is_dir() and not real.is_file():
        raise ErrorComplemento('COMP-MCP-RUTA', campo + ' no es un archivo ni una carpeta.')
    try:
        real.relative_to(raiz)
    except ValueError:
        raise ErrorComplemento('COMP-MCP-RUTA', campo + ' escapa de la raíz del complemento.') from None
    return real


def _id_servidor(identidad, original):
    """Identificador MCP estable y legible: <complemento>-<servidor>, sin «_».

    El cliente MCP exige [A-Za-z0-9][A-Za-z0-9-]{0,23}. Se pasa a minúsculas para
    que dos nombres que sólo difieren en mayúsculas no convivan en el catálogo:
    así el segundo se diagnostica en vez de duplicarse en silencio. Si el
    nombre no cabe se recorta y, si hace falta, se recurre a un sufijo del hash.
    """
    def limpio(texto):
        return re.sub(r'[^A-Za-z0-9-]+', '-', texto.lower()).strip('-')
    candidato = (limpio(identidad) + '-' + limpio(original)).strip('-')[:24].strip('-')
    if configuracion_mcp.ID.fullmatch(candidato):
        return candidato
    return 'p-' + hashlib.sha256((identidad + '\0' + original).encode()).hexdigest()[:22]


def _estado_esquema(ruta):
    """Traduce agent_plugin_schema_status de plugin_namespace.rs (Codex).

    Devuelve ('soportado'|'no-soportado'|'ajeno', datos). Un JSON ilegible o un
    archivo que no es regular ni se considera portable: cuenta como ajeno y la
    búsqueda sigue por DISCOVERABLE_PLUGIN_MANIFEST_PATHS.
    """
    try:
        if ruta.is_symlink() or not ruta.is_file():
            return 'ajeno', {}
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 'ajeno', {}
    if not isinstance(datos, dict):
        return 'ajeno', {}
    schema = datos.get('$schema')
    if not isinstance(schema, str):
        return 'ajeno', datos
    if schema == SCHEMA:
        return 'soportado', datos
    if schema.startswith(PREFIJO_ESQUEMA):
        return 'no-soportado', datos
    return 'ajeno', datos


class CargadorComplementos:
    def __init__(self, home, cwd=None, ambiente=None):
        self.home = Path(home).absolute()
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self.ambiente = dict(os.environ if ambiente is None else ambiente)
        self.complementos = []
        self.por_raiz = {}
        self.diagnosticos = []
        self.politicas = {}
        self._ids = set()
        self._ids_servidor = set()
        self._ids_duplicados_usuario = set()
        self._avisos_mcp = []
        self._raices_vistas = set()

    def cargar(self):
        raices, self.politicas, diagnosticos = configuracion_complementos.leer(self.home)
        for codigo, texto in diagnosticos:
            self.diagnosticos.append(Diagnostico(codigo, texto))
        for configurada in sorted(raices, key=lambda p: str(p)):
            # El listado siempre termina. Un fallo inesperado en una raíz
            # se diagnostica y no impide informar del resto ni emitir el JSON.
            try:
                self._raiz(configurada)
            except Exception as exc:  # noqa: BLE001 - límite de la unidad de carga
                self._error('COMP-RAIZ', 'La raíz autorizada no se pudo leer ('
                            + type(exc).__name__ + '); el resto sigue cargándose.', configurada)
        self._sueltas()
        self.complementos.sort(key=lambda p: p.identidad)
        return self

    def carpetas_de_habilidades(self):
        """Carpetas de habilidades sueltas, como las de Claude Code y Codex: (identidad, carpeta).

        Del usuario: ~/.claude/skills, ~/.codex/skills, ~/.agents/skills y $ALFALFA_HOME/skills.
        Del proyecto (su raíz git y la carpeta de trabajo): .claude/skills, .agents/skills,
        .codex/skills y .alfalfa/skills. Sólo se lee su SKILL.md al abrirla; nada se ejecuta."""
        casa = Path(CASA or self.ambiente.get('HOME') or Path.home())
        carpetas = [('usuario-claude', casa / '.claude' / 'skills'), ('usuario-codex', casa / '.codex' / 'skills'),
                    ('usuario-agents', casa / '.agents' / 'skills'), ('usuario-alfalfa', self.home / 'skills')]
        raiz = next((c for c in (self.cwd, *self.cwd.parents) if (c / '.git').exists()), self.cwd)
        for base in dict.fromkeys((raiz, self.cwd)):
            sufijo = '' if base == raiz else '-' + _normalizar_id(base.name)
            for nombre in ('claude', 'agents', 'codex', 'alfalfa'):
                carpetas.append(('proyecto-' + nombre + sufijo, base / ('.' + nombre) / 'skills'))
        return carpetas

    def _sueltas(self):
        if not configuracion_complementos.habilidades_sueltas(self.home):
            return
        vistas = set()
        for identidad, carpeta in self.carpetas_de_habilidades():
            try:
                if not carpeta.is_dir() or carpeta.is_symlink():
                    continue
                real = carpeta.resolve(strict=True)
            except OSError:
                continue
            if real in vistas or identidad in self._ids:
                continue
            vistas.add(real)
            plugin = Complemento(identidad, identidad, None, real, 'habilidades', '')
            for ruta in self._recorrer(real, 'SKILL.md')[:MAX_CAPACIDADES]:
                relativa = ruta.relative_to(real)
                if any(parte.startswith('.') for parte in relativa.parts):
                    continue  # .system de Codex y carpetas ocultas: usan herramientas de otro agente
                try:
                    _, contenido = _segura(real, str(relativa))
                    _, texto = self._leer_texto(contenido)
                    meta = _front_matter(texto)
                    nombre = meta.get('name') or ruta.parent.name
                    descripcion = meta.get('description', '')
                    if not nombre or len(nombre) > 128 or len(descripcion) > 2048:
                        raise ErrorComplemento('COMP-SKILL', 'name/description de SKILL.md inválidos o ausentes.')
                    ident = identidad + ':' + _normalizar_id(nombre)
                    if ident in plugin.habilidades:
                        raise ErrorComplemento('COMP-DUPLICADO', 'Habilidad duplicada: ' + ident)
                    plugin.habilidades[ident] = Habilidad(ident, nombre, descripcion, str(relativa),
                                                          hashlib.sha256(texto.encode('utf-8')).hexdigest())
                except (OSError, ValueError, ErrorComplemento) as exc:
                    plugin.aviso(getattr(exc, 'codigo', 'COMP-SKILL'),
                                 getattr(exc, 'texto', 'Habilidad ilegible; se ignora.'), 'skills')
            if plugin.habilidades or plugin.diagnosticos:
                self._ids.add(identidad)
                self.complementos.append(plugin)

    def informe(self):
        # Determinista: complementos por identidad y diagnósticos por (código,
        # complemento, capacidad, texto). Sin secretos: sólo el nombre del
        # servidor y su procedencia, nunca orden, argumentos, entorno ni cabeceras.
        diagnosticos = sorted(self.diagnosticos,
                              key=lambda d: (d.codigo, d.complemento or '', d.capacidad or '', d.texto))
        return dict(version=1, complementos=[p.como_dict() for p in self.complementos],
                    diagnosticos=[d.como_dict() for d in diagnosticos],
                    ejecucion='ninguna: sólo lectura de archivos autorizados')

    def _error(self, codigo, texto, raiz=None):
        self.diagnosticos.append(Diagnostico(codigo, texto[:600], str(raiz) if raiz else None))

    def _raiz(self, configurada):
        try:
            root = configurada.resolve(strict=True)
            if not root.is_dir():
                raise ErrorComplemento('COMP-RAIZ', 'La raíz autorizada no es una carpeta.')
            if root in self._raices_vistas:
                raise ErrorComplemento('COMP-RAIZ-DUPLICADA', 'La misma raíz autorizada aparece dos veces; no se '
                                                              'recarga.')
            self._raices_vistas.add(root)
            if root == self.cwd or root in self.cwd.parents:
                raise ErrorComplemento('COMP-CWD-NO-AUTORIZADO',
                    'La carpeta de trabajo no se activa por su contenido: configura una instalación distinta.')
            # La configuración puede nombrar un enlace de instalación; los hijos ya no pueden escapar.
            self._manifest(root)
        except (OSError, ErrorComplemento) as exc:
            self._error(getattr(exc, 'codigo', 'COMP-RAIZ'),
                        getattr(exc, 'texto', 'Raíz autorizada inaccesible (' + type(exc).__name__ + ').'), configurada)

    def _manifest(self, root):
        # plugin_namespace.rs: el portable raíz precede, pero sólo cuenta si su
        # $schema pertenece a agent-plugins. Un esquema no soportado se rechaza
        # aquí y no se degrada al manifiesto de cliente (no se adivina formato).
        elegido = None
        portable = root / PORTABLE[1]
        if portable.exists() or portable.is_symlink():
            estado, datos = _estado_esquema(portable)
            if estado == 'soportado':
                elegido = (PORTABLE[0], PORTABLE[1], portable, datos)
            elif estado == 'no-soportado':
                raise ErrorComplemento('COMP-SCHEMA', 'Versión Agent Plugins no soportada en plugin.json raíz: '
                                       + str(datos.get('$schema'))[:160] + '. No se carga nada.')
        if elegido is None:
            for formato, relativa in MANIFIESTOS:
                ruta = root / relativa
                if not (ruta.exists() or ruta.is_symlink()):
                    continue
                # El enlace puede estar en la carpeta (.codex-plugin), no
                # en el archivo; se comprueba la ruta real antes de leerla.
                real = _manifiesto_contenido(root, ruta, relativa)
                datos = _json_regular(real)
                schema = datos.get('$schema')
                if schema is not None and schema != SCHEMA:
                    raise ErrorComplemento('COMP-SCHEMA', 'Versión Agent Plugins no soportada en ' + relativa
                                           + ': ' + str(schema)[:160] + '.')
                elegido = (formato, relativa, real, datos)
                break
        if elegido is None:
            raise ErrorComplemento('COMP-SIN-MANIFIESTO', 'La raíz autorizada no contiene un manifiesto reconocido.')
        formato, relativa, ruta, datos = elegido
        # Los campos fuera del contrato Agent Plugins 1.0.0 no invalidan la
        # carga; se avisan como ignorados y sólo se leen los que producen
        # capacidad, igual que agent_plugin_manifest.rs (que filtra
        # AGENT_PLUGIN_FIELDS y avisa del resto).
        declarado = datos
        datos = self._validar_manifest(root, formato, datos)
        nombre = datos['name']
        identidad = _normalizar_id(nombre)
        if not identidad:
            raise ErrorComplemento('COMP-MANIFIESTO', 'name no produce un identificador utilizable.')
        if identidad in self._ids:
            raise ErrorComplemento('COMP-ID-DUPLICADO', 'Identidad duplicada; la segunda raíz no sustituye a la '
                                                        'primera: '
                                   + identidad)
        plugin = Complemento(identidad, nombre, datos.get('version'), root, formato, relativa,
                             _texto(datos, 'description'))
        # El manifiesto de cliente puede traer campos de su propio ecosistema;
        # sólo los desconocidos se avisan, nunca invalidan la carga.
        permitidos = AGENT_PLUGINS if formato == 'agent-plugins' else COMUNES
        for campo in sorted(set(declarado) - permitidos):
            plugin.aviso('COMP-CAMPO-DESCONOCIDO', 'Campo opcional desconocido ignorado: ' + campo, campo, False)
        # Un campo conocido sin capacidad equivalente tampoco se calla:
        # aparece en `ignoradas` y con diagnóstico, sin fingir soporte.
        for campo in sorted(set(declarado) & SIN_CAPACIDAD):
            plugin.ignoradas.append(campo)
            plugin.aviso('COMP-CAMPO-NO-SOPORTADO',
                         'Campo conocido sin capacidad compatible en AlfalfaCode; se ignora: ' + campo,
                         campo, False)
        # Se enumera, nunca se interpreta: .app.json no se abre. Los
        # hooks se leen aquí y sólo los ejecuta arnes/hooks, en el sandbox.
        if (root / '.app.json').exists() or (root / '.app.json').is_symlink():
            plugin.ignoradas.append('.app.json')
        # Se valida el complemento entero antes de registrarlo. Antes se
        # añadía a `self.complementos` al principio: con un rechazo estructural
        # (p. ej. COMP-LIMITE por 257 rutas en commands) quedaban visibles el
        # complemento y sus habilidades, y abrir_habilidad seguía funcionando
        # sobre un complemento rechazado.
        reservados_servidor = set(self._ids_servidor)
        try:
            self._habilidades(plugin, datos)
            self._comandos(plugin, datos)
            self._mcp(plugin, datos)
            self._hooks(plugin, declarado)
        except (ErrorComplemento, OSError) as exc:
            # Nada del complemento rechazado se registra: ni identidad, ni
            # capacidades, ni los identificadores de servidor que hubiera tomado.
            self._ids_servidor = set(reservados_servidor)
            self._error(getattr(exc, 'codigo', 'COMP-COMPLEMENTO'),
                        getattr(exc, 'texto', 'El complemento no se pudo cargar.'), root)
            return
        self._ids.add(identidad)
        self.complementos.append(plugin)
        self.por_raiz[root] = plugin

    @staticmethod
    def _validar_manifest(root, formato, datos):
        # Los campos opcionales desconocidos no invalidan: sólo se avisan después.
        # Lo que sí rechaza es un $schema ajeno o una versión no soportada, y un
        # tipo imposible en los campos que se van a interpretar.
        if formato == 'agent-plugins':
            if datos.get('$schema') != SCHEMA:
                raise ErrorComplemento('COMP-SCHEMA', 'Falta el identificador exacto del esquema 1.0.0.')
            # agent_plugin_manifest.rs avisa y descarta los campos fuera de
            # AGENT_PLUGIN_FIELDS en lugar de invalidar todo el manifiesto. Aquí
            # se conserva la carga con las capacidades compatibles y los campos
            # fuera del contrato se enumeran como ignorados en el informe.
            datos = {campo: valor for campo, valor in datos.items() if campo in AGENT_PLUGINS}
            keywords = datos.get('keywords', [])
            if not isinstance(keywords, list) or not all(isinstance(k, str) and k for k in keywords):
                raise ErrorComplemento('COMP-MANIFIESTO', 'keywords debe ser una lista de textos no vacíos.')
            # plugin.schema.json declara estos campos como texto: un 7 o un null no
            # es un campo opcional ausente, es un manifiesto inválido.
            for campo in ('description', 'homepage', 'repository', 'license'):
                if campo in datos and not isinstance(datos[campo], str):
                    raise ErrorComplemento('COMP-MANIFIESTO', campo + ' debe ser texto.')
        elif '$schema' in datos and datos['$schema'] != SCHEMA:
            raise ErrorComplemento('COMP-SCHEMA', 'Sólo se admite el esquema Agent Plugins 1.0.0.')
        nombre = datos.get('name')
        if not isinstance(nombre, str) or not 1 <= len(nombre) <= 64 or not NOMBRE.fullmatch(nombre):
            raise ErrorComplemento('COMP-MANIFIESTO', 'name debe cumplir el identificador Agent Plugins (máx. 64).')
        version = datos.get('version')
        # `version: null` tampoco vale: si el campo está, es texto.
        if 'version' in datos and (not isinstance(version, str) or not version or len(version) > 128):
            raise ErrorComplemento('COMP-MANIFIESTO', 'version debe ser texto no vacío de hasta 128 caracteres.')
        autor = datos.get('author')
        # `author: null` tampoco es un campo ausente.
        if 'author' in datos:
            if not isinstance(autor, dict):
                raise ErrorComplemento('COMP-MANIFIESTO', 'author debe ser un objeto.')
            if any(not isinstance(autor.get(c), str) for c in autor if c in ('name', 'email', 'url')):
                raise ErrorComplemento('COMP-MANIFIESTO', 'author.name, author.email y author.url deben ser texto.')
        for campo in ('extensions',):
            if campo in datos and not isinstance(datos[campo], dict):
                raise ErrorComplemento('COMP-MANIFIESTO', campo + ' debe ser un objeto.')
        for campo in RUTAS_MANIFIESTO:
            if campo in datos and not isinstance(datos[campo], (str, list, dict)):
                raise ErrorComplemento('COMP-MANIFIESTO', campo + ' tiene un tipo no soportado.')
        return datos

    def _ubicaciones(self, plugin, datos, campo, defecto):
        """Rutas declaradas en orden, sin duplicar ni abandonar la raíz.

        El valor por defecto es un nombre relativo del formato; una ruta que el
        manifiesto declara sigue la regla de Codex (resolve_manifest_path):
        prefijo `./`, sin `..` y dentro de la raíz.
        """
        declarado = datos.get(campo, defecto)
        if isinstance(declarado, dict):
            declarado = declarado.get('ruta', defecto)
        rutas = declarado if isinstance(declarado, list) else [declarado]
        if len(rutas) > MAX_CAPACIDADES:
            raise ErrorComplemento('COMP-LIMITE', 'Demasiadas rutas declaradas para ' + campo)
        for valor in rutas:
            try:
                yield _segura(plugin.raiz, valor, obligar_relativa=campo in datos)
            except ErrorComplemento as exc:
                plugin.aviso(exc.codigo, exc.texto, campo)
            except FileNotFoundError:
                # Una carpeta declarada que aún no existe no es un escape: se
                # informa sin gravedad y no aporta capacidades.
                plugin.aviso('COMP-RUTA-AUSENTE', 'La ruta declarada en ' + campo
                             + ' no existe o no es accesible; no aporta capacidades.', campo, False)
            except OSError as exc:
                plugin.aviso('COMP-RUTA', 'Ruta inaccesible (' + type(exc).__name__ + ').', campo)

    @staticmethod
    def _recorrer(real, patron):
        """Archivos del patrón bajo `real`, sin seguir enlaces de directorio."""
        encontrados = []
        pendientes = [real]
        while pendientes and len(encontrados) < MAX_CAPACIDADES * 4:
            actual = pendientes.pop()
            try:
                hijos = sorted(actual.iterdir(), key=lambda p: str(p))
            except OSError:
                continue
            for hijo in hijos:
                if hijo.is_symlink():
                    continue
                if hijo.is_dir():
                    pendientes.append(hijo)
                elif hijo.name == patron:
                    encontrados.append(hijo)
        return sorted(encontrados, key=lambda p: str(p))

    def _habilidades(self, plugin, datos):
        for _, real in self._ubicaciones(plugin, datos, 'skills', 'skills'):
            if not real.is_dir():
                continue
            # Descubrimiento recursivo (loader.rs usa SkillDiscoveryMode::Recursive).
            for ruta in self._recorrer(real, 'SKILL.md')[:MAX_CAPACIDADES]:
                try:
                    candidata, contenido = _segura(plugin.raiz, str(ruta.relative_to(plugin.raiz)))
                    _, texto = self._leer_texto(contenido)
                    meta = _front_matter(texto)
                    nombre = meta.get('name') or candidata.parent.name
                    descripcion = meta.get('description', '')
                    if not nombre or len(nombre) > 128 or len(descripcion) > 2048:
                        raise ErrorComplemento('COMP-SKILL', 'name/description de SKILL.md inválidos o ausentes.')
                    ident = plugin.identidad + ':' + _normalizar_id(nombre)
                    if ident in plugin.habilidades:
                        raise ErrorComplemento('COMP-DUPLICADO', 'Habilidad duplicada: ' + ident)
                    # Sólo metadatos y hash: el cuerpo se lee al abrirla.
                    plugin.habilidades[ident] = Habilidad(
                        ident, nombre, descripcion, str(candidata.relative_to(plugin.raiz)),
                        hashlib.sha256(texto.encode('utf-8')).hexdigest())
                except (OSError, ValueError, ErrorComplemento) as exc:
                    plugin.aviso(getattr(exc, 'codigo', 'COMP-SKILL'),
                                 getattr(exc, 'texto', 'Habilidad ilegible; se ignora.'), 'skills')

    @staticmethod
    def _leer_texto(ruta):
        if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > MAX_ARCHIVO:
            raise ErrorComplemento('COMP-ARCHIVO', 'Archivo de capacidad no regular, enlazado o demasiado grande.')
        try:
            return None, ruta.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise ErrorComplemento('COMP-ARCHIVO', 'Archivo de capacidad ilegible: ' + type(exc).__name__) from None

    def _comandos(self, plugin, datos):
        for _, real in self._ubicaciones(plugin, datos, 'commands', 'commands'):
            if not real.is_dir():
                continue
            for ruta in sorted(real.glob('*.md'), key=lambda p: str(p))[:MAX_CAPACIDADES]:
                try:
                    candidata, contenido = _segura(plugin.raiz, str(ruta.relative_to(plugin.raiz)))
                    _, texto = self._leer_texto(contenido)
                    meta = _front_matter(texto)
                    # Espacio de nombres estable: <identidad>:<archivo>, sin depender
                    # del orden de carga ni de la carpeta padre.
                    ident = plugin.identidad + ':' + _normalizar_id(ruta.stem)
                    if ident in plugin.comandos:
                        raise ErrorComplemento('COMP-DUPLICADO', 'Comando duplicado: ' + ident)
                    plugin.comandos[ident] = Comando(ident, str(candidata.relative_to(plugin.raiz)),
                                                     meta.get('description', ''), _cuerpo(texto))
                except (OSError, ErrorComplemento) as exc:
                    plugin.aviso(getattr(exc, 'codigo', 'COMP-COMANDO'),
                                 getattr(exc, 'texto', 'Comando ilegible; se ignora.'), 'commands')

    def abrir_habilidad(self, identidad):
        """Apertura explícita; descubrimiento sólo guardó metadatos y hash."""
        for plugin in self.complementos:
            if identidad in plugin.habilidades:
                habilidad = plugin.habilidades[identidad]
                try:
                    _, real = _segura(plugin.raiz, habilidad.ruta)
                    _, texto = self._leer_texto(real)
                    if hashlib.sha256(texto.encode('utf-8')).hexdigest() != habilidad.sha256:
                        raise ErrorComplemento('COMP-SKILL-CAMBIADA', 'La habilidad cambió desde el descubrimiento.')
                    return dict(id=identidad, contenido=texto, sha256=habilidad.sha256)
                except (OSError, ErrorComplemento) as exc:
                    raise ErrorComplemento(getattr(exc, 'codigo', 'COMP-SKILL'),
                                           getattr(exc, 'texto', 'No se pudo abrir la habilidad.')) from None
        raise ErrorComplemento('COMP-SKILL-DESCONOCIDA', 'Habilidad desconocida o no autorizada.')

    def invocar_comando(self, identidad, argumentos=''):
        for plugin in self.complementos:
            if identidad in plugin.comandos:
                comando = plugin.comandos[identidad]
                # Relee la raíz autorizada: no se conserva texto de un archivo cambiado.
                _, real = _segura(plugin.raiz, comando.ruta)
                _, texto = self._leer_texto(real)
                actual = Comando(comando.id, comando.ruta, comando.descripcion, _cuerpo(texto))
                return actual.invocar(argumentos)
        raise ErrorComplemento('COMP-COMANDO-DESCONOCIDO', 'Comando desconocido o no autorizado.')

    def _mcp(self, plugin, datos):
        declarada = datos.get('mcpServers', datos.get('mcp'))
        if isinstance(declarada, dict):
            # Claude admite el mapa inline; se acepta envuelto en mcpServers o
            # como mapa simple {servidor: entrada}.
            self._mcp_datos(plugin, declarada, '<manifiesto:mcpServers>')
            return
        rutas = []
        declarada_por_manifiesto = isinstance(declarada, str)
        if declarada_por_manifiesto:
            rutas = [declarada]
        elif declarada is not None:
            plugin.aviso('COMP-MCP', 'mcpServers/mcp debe ser una ruta u objeto; se ignora.', 'mcp')
            return
        else:
            # El predeterminado sólo cuenta si el manifiesto no declara
            # ruta. Con ruta declarada se lee únicamente esa, como Codex
            # (loader.rs: DEFAULT_MCP_CONFIG_FILE = «.mcp.json» es el respaldo).
            predeterminados = (('mcp.json',) if plugin.formato == 'agent-plugins'
                               else ('.mcp.json', 'mcp.json'))
            for nombre in predeterminados:
                candidato = plugin.raiz / nombre
                if candidato.exists() or candidato.is_symlink():
                    rutas.append(nombre)
                    break  # una única configuración por complemento
        for ruta in rutas:
            try:
                # Una ruta declarada en mcpServers/mcp sigue el contrato de
                # manifest.rs (`./`, sin `..`, raíz explícita); el predeterminado
                # del formato es un nombre relativo, no una declaración.
                _, real = _segura(plugin.raiz, ruta, obligar_relativa=declarada_por_manifiesto)
                # La procedencia es la ruta real relativa a la raíz, no el literal
                # declarado: «./.mcp.json» y «.mcp.json» describen el mismo archivo.
                self._mcp_datos(plugin, _json_regular(real), real.relative_to(plugin.raiz).as_posix())
            except (OSError, ErrorComplemento) as exc:
                plugin.aviso(getattr(exc, 'codigo', 'COMP-MCP'),
                             getattr(exc, 'texto', 'Configuración MCP ilegible; se ignora.'), 'mcp')

    @staticmethod
    def _servidores_de(datos):
        """Mapa de servidores MCP: envuelto en mcpServers o simple.

        Codex acepta ambas formas y Claude admite el mapa inline. Un envoltorio
        con claves adicionales se rechaza: no se adivina qué es cada cosa.
        """
        if not isinstance(datos, dict):
            return None
        if 'mcpServers' in datos:
            servidores = datos['mcpServers']
            return servidores if isinstance(servidores, dict) else None
        return datos or None

    def _mcp_datos(self, plugin, datos, procedencia):
        servidores = self._servidores_de(datos)
        if servidores is None:
            plugin.aviso('COMP-MCP', 'Se esperaba un objeto mcpServers; no se carga MCP.', 'mcp')
            return
        for original, entrada in sorted(servidores.items()):
            if len(plugin.servidores) >= configuracion_mcp.MAX_SERVIDORES:
                plugin.aviso('COMP-MCP-LIMITE', 'Se alcanzó el límite de servidores; se ignoran los restantes.', 'mcp')
                break
            if not isinstance(original, str) or not original:
                plugin.aviso('COMP-MCP-ID', 'Nombre de servidor inválido; se ignora.', 'mcp')
                continue
            # El namespace evita colisiones entre plugins y el identificador del
            # cliente MCP no admite «_» (configuracion.ID): se deriva del nombre.
            # El namespace se registra en minúsculas para que dos nombres que sólo
            # difieren en mayúsculas no convivan: el segundo se diagnostica.
            ident = _id_servidor(plugin.identidad, original)
            if ident in self._ids_servidor:
                plugin.aviso('COMP-MCP-ID-DUPLICADO', 'Dos servidores producen el mismo identificador ('
                             + ident + '); se ignora el segundo y no se sobrescribe el primero.', original)
                continue
            try:
                normalizada = self._normalizar_servidor(plugin, original, entrada)
                politica, avisos = configuracion_complementos.servidor_usuario(
                    self.politicas, plugin.identidad, original)
                for _, texto in avisos:
                    plugin.aviso('COMP-POLITICA-MCP', texto, original, False)
                normalizada.update(politica)
                # `activo` es una decisión del usuario («activo» admite
                # true/false). Un servidor desactivado no llega al gestor MCP.
                activo = normalizada.pop('activo', True)
                if not isinstance(activo, bool):
                    raise ErrorComplemento('COMP-POLITICA-MCP', 'La política activo debe ser true o false.')
                if activo is False:
                    plugin.aviso('COMP-POLITICA-MCP', 'El servidor está desactivado en la configuración '
                                 'del usuario; no se registra ni se inicia.', original, False)
                    continue
                cwd_servidor = normalizada.pop('_cwd', None)
                # Ningún campo del plugin puede conceder directas, confianza o herencia.
                servidor = configuracion_mcp._servidor(ident, normalizada, self.ambiente)
                servidor.cwd = cwd_servidor
                servidor.procedencia = procedencia
                servidor.complemento = plugin.identidad
                servidor.nombre_original = original
                if ident in plugin.servidores:
                    raise ErrorComplemento('COMP-DUPLICADO', 'Servidor duplicado; no se sobrescribe: ' + original)
                plugin.servidores[ident] = dict(original=original, servidor=servidor, procedencia=procedencia)
                self._ids_servidor.add(ident)
                # Los avisos del cliente MCP son otro tipo de
                # Diagnostico (sin capacidad): mezclados tal cual, diagnosticos_mcp()
                # lanzaba AttributeError y TerminalManager no arrancaba.
                for aviso in configuracion_mcp.avisos(servidor):
                    plugin.aviso(aviso.codigo, aviso.texto, ident, aviso.grave)
            except (configuracion_mcp._Invalido, ErrorComplemento) as exc:
                # Un servidor inválido no reserva su identificador: otro válido
                # con el mismo nombre normalizado todavía puede cargarse.
                plugin.aviso(getattr(exc, 'codigo', 'COMP-MCP'),
                             getattr(exc, 'texto', str(exc)) + ' El servidor no se inicia.', original)

    def _normalizar_servidor(self, plugin, original, entrada):
        if not isinstance(entrada, dict):
            raise ErrorComplemento('COMP-MCP', 'La entrada del servidor debe ser un objeto.')
        # Estas claves del paquete se informan y se ignoran, nunca se copian.
        for prohibida in ('entorno_heredado', 'cabeceras_entorno', 'token_entorno', 'directas', 'confianza',
                          'habilitadas', 'deshabilitadas', 'max_bytes_esquema', 'red'):
            if prohibida in entrada:
                plugin.aviso('COMP-POLITICA-IGNORADA', 'La declaración no puede definir ' + prohibida
                             + '; sólo la configuración del usuario.', prohibida, False)
        tipo = entrada.get('type', 'stdio' if 'command' in entrada else 'streamable-http')
        if tipo == 'sse':
            raise ErrorComplemento('COMP-MCP-TRANSPORTE', 'MCP SSE heredado no está soportado por el cliente.')
        if tipo not in ('stdio', 'streamable-http', 'http'):
            raise ErrorComplemento('COMP-MCP-TRANSPORTE', 'Transporte MCP no soportado: ' + str(tipo)[:80])
        if tipo == 'stdio':
            comando = entrada.get('command')
            if not isinstance(comando, str) or not comando or '\0' in comando:
                raise ErrorComplemento('COMP-MCP', 'command debe ser texto no vacío.')
            argumentos = entrada.get('args', [])
            if not isinstance(argumentos, list) or any(not isinstance(a, str) for a in argumentos):
                raise ErrorComplemento('COMP-MCP', 'args debe ser una lista de textos.')
            entorno = entrada.get('env', {})
            if not isinstance(entorno, dict) or any(not isinstance(v, str) for v in entorno.values()):
                raise ErrorComplemento('COMP-MCP', 'env debe ser un objeto de textos.')
            # PLUGIN_ROOT se resuelve estáticamente; otras variables jamás se leen
            # del entorno: ninguna declaración hereda secretos del usuario. Los
            # complementos de Claude Code escriben ${CLAUDE_PLUGIN_ROOT}: es la
            # misma raíz (Codex también lo expone así a sus hooks).
            def expandir(valor):
                if re.search(r'\$\{(?!(?:CLAUDE_)?PLUGIN_ROOT\})[^}]+\}', valor):
                    raise ErrorComplemento('COMP-MCP-ENTORNO', 'Sólo se admite ${PLUGIN_ROOT} (o '
                                           '${CLAUDE_PLUGIN_ROOT}); no se resuelven otras variables ni '
                                           'se heredan secretos.')
                return valor.replace('${CLAUDE_PLUGIN_ROOT}', str(plugin.raiz)).replace('${PLUGIN_ROOT}',
                                                                                        str(plugin.raiz))
            comando = expandir(comando)
            if '/' in comando or comando.startswith('.'):
                real = _ruta_declarada(plugin.raiz, comando, 'El ejecutable declarado')
                if not real.is_file():
                    raise ErrorComplemento('COMP-MCP-RUTA', 'El ejecutable declarado no es un archivo.')
                comando = str(real)
            normalizada = dict(orden=comando, argumentos=[expandir(a) for a in argumentos],
                               entorno={k: expandir(v) for k, v in entorno.items()})
            cwd = entrada.get('cwd', str(plugin.raiz))
            # Un cwd no textual (7, {}, []) reventaba en expandir() con
            # TypeError y se llevaba por delante el resto del informe. Se valida
            # antes de expandirlo: el servidor se diagnostica y los demás siguen.
            if not isinstance(cwd, str) or not cwd or '\0' in cwd or len(cwd) > 4096:
                raise ErrorComplemento('COMP-MCP-RUTA', 'cwd debe ser una ruta de texto no vacía.')
            cwd = expandir(cwd)
            # El cliente MCP tiene un cwd por gestor; este servidor exige el suyo.
            normalizada['_cwd'] = str(_ruta_declarada(plugin.raiz, cwd, 'cwd'))
            return normalizada
        url = entrada.get('url')
        if not isinstance(url, str) or '${' in url:
            raise ErrorComplemento('COMP-MCP-URL', 'url no admite expansiones de entorno.')
        cabeceras = entrada.get('headers', {})
        if not isinstance(cabeceras, dict) or any(not isinstance(v, str) for v in cabeceras.values()):
            raise ErrorComplemento('COMP-MCP', 'headers debe ser un objeto de textos.')
        if any(re.search(r'\$\{[^}]+\}', v) for v in cabeceras.values()):
            raise ErrorComplemento('COMP-MCP-ENTORNO', 'No se resuelven secretos declarados por el complemento.')
        return dict(url=url, cabeceras=cabeceras)

    def _hooks(self, plugin, datos):
        """hooks/hooks.json, o lo que declare el manifiesto (ruta `./…`, lista u objeto inline)."""
        declarada = datos.get('hooks')
        if isinstance(declarada, dict):
            plugin.hooks.append(dict(procedencia='<manifiesto:hooks>', datos=declarada))
            return
        if declarada is None:
            rutas, obligar = (['hooks/hooks.json'] if (plugin.raiz / 'hooks' / 'hooks.json').exists() else []), False
        elif isinstance(declarada, (str, list)):
            rutas, obligar = (declarada if isinstance(declarada, list) else [declarada]), True
        else:
            plugin.aviso('COMP-HOOKS', 'hooks debe ser una ruta, una lista de rutas u objeto; se ignora.', 'hooks')
            return
        if len(rutas) > MAX_CAPACIDADES:
            raise ErrorComplemento('COMP-LIMITE', 'Demasiadas rutas declaradas para hooks')
        for ruta in rutas:
            try:
                _, real = _segura(plugin.raiz, ruta, obligar_relativa=obligar)
                procedencia = real.relative_to(plugin.raiz).as_posix()
                if all(h['procedencia'] != procedencia for h in plugin.hooks):
                    plugin.hooks.append(dict(procedencia=procedencia, datos=_json_regular(real)))
            except FileNotFoundError:
                plugin.aviso('COMP-RUTA-AUSENTE', 'La ruta declarada en hooks no existe; no aporta hooks.', 'hooks',
                             False)
            except (OSError, ErrorComplemento) as exc:
                plugin.aviso(getattr(exc, 'codigo', 'COMP-HOOKS'),
                             getattr(exc, 'texto', 'Archivo de hooks ilegible; se ignora.'), 'hooks')

    def fuentes_hooks(self):
        """Fuentes para arnes/hooks: sólo de complementos autorizados y con hooks activos.

        La política del usuario (`politicas.<id>.hooks`) puede apagarlos
        (`activo: false`) y declarar variables (`entorno`); el complemento no.
        """
        fuentes = []
        for plugin in self.complementos:
            politica = self.politicas.get(plugin.identidad, {}).get('hooks', {})
            if politica.get('activo', True) is False:
                continue
            for hook in plugin.hooks:
                fuentes.append(dict(archivo=str(plugin.raiz / hook['procedencia'])
                                    if not hook['procedencia'].startswith('<') else hook['procedencia'],
                                    origen='complemento:' + plugin.identidad, raiz_complemento=plugin.raiz,
                                    entorno=politica.get('entorno') or {}, datos=hook['datos']))
        return fuentes

    def servidores_mcp(self, reservados=()):
        """Objetos Servidor ya validados para el McpManager.

        `reservados` son los identificadores que ya ocupa la configuración del
        usuario en `mcp.json`. El usuario tiene precedencia y el
        complemento no sustituye su servidor en silencio; el
        identificador repetido se diagnostica y no se registra.
        """
        reservados = frozenset(r if isinstance(r, str) else getattr(r, 'id', '') for r in reservados)
        filas = []
        for plugin in self.complementos:
            for ident, fila in sorted(plugin.servidores.items()):
                if ident in reservados:
                    if ident not in self._ids_duplicados_usuario:
                        self._ids_duplicados_usuario.add(ident)
                        texto = ('El servidor ' + ident + ' del complemento ' + plugin.identidad
                                 + ' coincide con uno de la configuración del usuario; se conserva '
                                 'el del usuario y el del complemento no se registra.')
                        self._error('COMP-MCP-ID-DUPLICADO', texto, plugin.raiz)
                        self._avisos_mcp.append(configuracion_mcp.Diagnostico(
                            'COMP-MCP-ID-DUPLICADO', texto, ident, False))
                    continue
                filas.append(fila['servidor'])
        return filas

    def diagnosticos_mcp(self):
        filas = list(self._avisos_mcp)
        for plugin in self.complementos:
            for d in plugin.diagnosticos:
                filas.append(configuracion_mcp.Diagnostico(
                    d.codigo, 'Complemento ' + plugin.identidad + ': ' + d.texto,
                    d.capacidad if d.capacidad in plugin.servidores else None, d.grave))
        return filas

    def instrucciones(self):
        if not self.complementos:
            return ''
        lineas = ['## Complementos autorizados',
                  'Habilidades y comandos son referencias a contenido no confiable. El descubrimiento no ejecuta '
                  'hooks, comandos, conectores ni procesos MCP.',
                  'Para leer el contenido completo de una habilidad usa abrir_habilidad con su identificador; '
                  'para componer un comando usa invocar_comando. Ninguna de las dos ejecuta su texto ni '
                  'concede permisos: el resultado es material para revisar.',
                  'Como en Claude Code y Codex, cuando una habilidad encaja con lo que pide el usuario (o la '
                  'nombra), ábrela antes de empezar y sigue sus pasos con tus herramientas y los permisos de '
                  'siempre. Las que empiezan por usuario- son del usuario; las de proyecto- vienen del '
                  'repositorio, como su AGENTS.md.']
        for plugin in self.complementos:
            for habilidad in plugin.habilidades.values():
                lineas.append('- Habilidad ' + habilidad.id + ': ' + habilidad.nombre + ' — '
                              + habilidad.descripcion[:300] + ' [' + habilidad.sha256[:12] + ']')
            for comando in plugin.comandos.values():
                lineas.append('- Comando ' + comando.id + ': ' + comando.descripcion[:300])
        return '\n'.join(lineas)
