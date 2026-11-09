"""Proyectos preparados para OpenCode, Codex o Claude Code, sin tocar nada.

Qué se lee (sólo lectura, nunca se escribe en esos archivos):

- OpenCode: `opencode.json(c)` y `.opencode/opencode.json(c)` de la carpeta de trabajo y de la
  raíz del repositorio, y `~/.config/opencode/opencode.json(c)`: sus servidores MCP (`mcp`),
  agentes (`agent`/`agents`), comandos (`command`/`commands`) e `instructions`; y los
  `.opencode/agent(s)/*.md` y `.opencode/command(s)/*.md` (también los de ~/.config/opencode).
- Codex: `~/.codex/config.toml` (o `$CODEX_HOME/config.toml`) y `.codex/config.toml` del
  proyecto: sólo `[mcp_servers.*]`, `developer_instructions` y `project_doc_fallback_filenames`.
  Nada más de `~/.codex`.
- Claude Code: `.mcp.json` del proyecto y los comandos de `.claude/commands/*.md` (del proyecto
  y de `~/.claude/commands`). Sus agentes (`.claude/agents`) y sus CLAUDE.md ya los leen
  subagentes.py y agents_md.

Precedencia: lo propio de AlfalfaCode (`mcp.json`, complementos, `.alfalfa/agents`) gana; luego
lo del proyecto (la carpeta de trabajo antes que la raíz, `.opencode/` antes que el
`opencode.json` suelto, como OpenCode) y por último lo del usuario. Un nombre repetido se enseña
como «duplicado» y no se activa.

Seguridad: un servidor MCP o un agente importado no se activa hasta que el usuario lo autoriza
una vez (`alfalfa --importar --autorizar CLAVE` o Ajustes ▸ Importar), y vuelve a «pendiente»
si su definición cambia. Las claves de esas configuraciones no se copian: las referencias a
variables se resuelven en memoria al abrir la sesión y `importadas.json` sólo guarda huellas.
Los comandos «/» y las instrucciones no necesitan autorización: un comando sólo corre cuando el
usuario lo escribe, y las instrucciones son como el AGENTS.md del repositorio.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..mcp_cliente import configuracion as configuracion_mcp
from . import traduccion
from .autorizacion import Autorizaciones
from .formatos import Invalido, cabecera, leer_jsonc, leer_texto, leer_toml

# Carpeta personal donde buscar ~/.codex, ~/.config/opencode y ~/.claude; None es la del usuario.
# Las pruebas la apuntan a una carpeta de demo para no leer las configuraciones de quien las corre.
CASA = None
OPENCODE, CODEX, CLAUDE = 'OpenCode', 'Codex', 'Claude Code'
REQUIEREN_AUTORIZACION = ('mcp', 'agente')
MAX_ELEMENTOS = 200
MAX_INSTRUCCIONES = 32 * 1024
NOMBRE_DE_COMANDO = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,63}')
NOMBRE_DE_AGENTE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,63}')


@dataclass
class Elemento:
    tipo: str          # mcp | agente | comando | instrucciones
    nombre: str        # el que usa AlfalfaCode
    herramienta: str   # OpenCode | Codex | Claude Code
    origen: Path       # el archivo de donde viene
    ambito: str        # proyecto | usuario
    detalle: str = ''
    activo: bool = True        # enabled/disabled en su propia configuración
    problema: str | None = None
    avisos: list = field(default_factory=list)
    estado: str = ''
    tapado_por: str | None = None
    crudo: object = field(default=None, repr=False)   # la definición tal cual: sólo para la huella
    valor: object = field(default=None, repr=False)   # Servidor, tipo de agente o plantilla

    @property
    def clave(self):
        return self.tipo + ':' + self.nombre + '@' + str(self.origen)

    def como_dict(self, autorizaciones=None):
        """Lo que se enseña: nunca argumentos, entorno, cabeceras ni el texto de la plantilla."""
        fila = dict(clave=self.clave, tipo=self.tipo, nombre=self.nombre, herramienta=self.herramienta,
                    origen=str(self.origen), ambito=self.ambito, detalle=self.detalle, estado=self.estado,
                    autorizable=self.tipo in REQUIEREN_AUTORIZACION and self.estado in ('pendiente', 'cambio',
                                                                                        'autorizado'))
        if self.problema:
            fila['problema'] = self.problema
        if self.avisos:
            fila['avisos'] = list(self.avisos)
        if self.tapado_por:
            fila['tapado_por'] = self.tapado_por
        if self.tipo == 'mcp' and autorizaciones is not None and self.estado == 'autorizado':
            fila['red'] = bool((autorizaciones.registro(self) or {}).get('red', True))
        return fila


def _herramienta_de(ruta, de_opencode):
    texto = str(ruta)
    if texto.endswith('#developer_instructions'):
        return CODEX
    if ruta in de_opencode:
        return OPENCODE
    nombre = Path(texto).name
    if nombre.startswith('CLAUDE') or nombre == 'CLAUDE.md':
        return CLAUDE
    # AGENTS.md es el estándar abierto que leen Codex, OpenCode y AlfalfaCode; otro nombre es un
    # project_doc_fallback_filenames de Codex.
    return 'AGENTS.md' if nombre.startswith('AGENTS') else CODEX


def _fija(ambiente):
    """La carpeta personal impuesta (pruebas): CASA o ALFALFA_IMPORTAR_CASA, para los procesos hijos."""
    return CASA or ambiente.get('ALFALFA_IMPORTAR_CASA') or None


def _casa(ambiente):
    return Path(_fija(ambiente) or ambiente.get('HOME') or Path.home())


def _carpetas(cwd):
    """La carpeta de trabajo y la raíz del repositorio, de la más cercana a la más lejana."""
    from .. import agents_md
    cwd = Path(cwd).resolve()
    raiz = agents_md.raiz_del_proyecto(cwd) or cwd
    return list(dict.fromkeys((cwd, raiz))), raiz


def archivos_opencode(cwd, ambiente):
    """[(ruta, ámbito)] en orden de precedencia: `.opencode/` gana al suelto y lo cercano a lo lejano."""
    carpetas, _ = _carpetas(cwd)
    nombres = ('opencode.jsonc', 'opencode.json')
    filas = [(c / '.opencode' / n, 'proyecto') for c in carpetas for n in nombres]
    filas += [(c / n, 'proyecto') for c in carpetas for n in nombres]
    filas += [(_config_opencode(ambiente) / n, 'usuario') for n in nombres]
    return [(ruta, ambito) for ruta, ambito in filas if ruta.is_file()]


def _config_opencode(ambiente):
    xdg = ambiente.get('XDG_CONFIG_HOME') if _fija(ambiente) is None else None
    return (Path(xdg) if xdg else _casa(ambiente) / '.config') / 'opencode'


def archivos_codex(cwd, ambiente):
    carpetas, _ = _carpetas(cwd)
    hogar_codex = Path(ambiente['CODEX_HOME']) if ambiente.get('CODEX_HOME') and _fija(ambiente) is None \
        else _casa(ambiente) / '.codex'
    filas = [(c / '.codex' / 'config.toml', 'proyecto') for c in carpetas]
    filas.append((hogar_codex / 'config.toml', 'usuario'))
    return [(ruta, ambito) for ruta, ambito in filas if ruta.is_file()]


def instrucciones(cwd, ambiente=None):
    """Lo que dicen las configuraciones ajenas además de AGENTS.md y CLAUDE.md.

    (antes, despues, alternativos, avisos): `antes` son los `developer_instructions` de Codex
    (del usuario y luego del proyecto; Codex los pone delante de AGENTS.md), `despues` los
    archivos de `instructions` de OpenCode y `alternativos` los `project_doc_fallback_filenames`
    de Codex. Cada texto va con su origen: [(ruta o etiqueta, texto)]."""
    ambiente = dict(os.environ if ambiente is None else ambiente)
    antes, despues, alternativos, avisos = [], [], [], []
    for ruta, _ in reversed(archivos_codex(cwd, ambiente)):  # usuario primero, luego proyecto
        try:
            datos = leer_toml(ruta)
        except Invalido as exc:
            avisos.append(str(ruta) + ': ' + str(exc))
            continue
        texto = datos.get('developer_instructions')
        if isinstance(texto, str) and texto.strip():
            antes.append((Path(str(ruta) + '#developer_instructions'), texto))
        for nombre in datos.get('project_doc_fallback_filenames') or []:
            if isinstance(nombre, str) and nombre and '/' not in nombre and nombre not in alternativos:
                alternativos.append(nombre)
    _, raiz = _carpetas(cwd)
    for ruta, ambito in reversed(archivos_opencode(cwd, ambiente)):  # lo más general primero
        try:
            lista = leer_jsonc(ruta).get('instructions') or []
        except Invalido as exc:
            avisos.append(str(ruta) + ': ' + str(exc))
            continue
        limite = (raiz if ambito == 'proyecto' else ruta.parent).resolve()
        for patron in lista if isinstance(lista, list) else []:
            if not isinstance(patron, str) or not patron:
                continue
            if re.match(r'https?://', patron):
                avisos.append(str(ruta) + ': no se descargan instrucciones de ' + patron)
                continue
            base = ruta.parent.parent if ruta.parent.name == '.opencode' else ruta.parent
            for archivo in _glob(base, patron, limite):
                try:
                    despues.append((archivo, leer_texto(archivo, MAX_INSTRUCCIONES)))
                except Invalido as exc:
                    avisos.append(str(archivo) + ': ' + str(exc))
    return antes, despues, alternativos, avisos


def _glob(base, patron, limite):
    """Archivos del patrón dentro de `limite` (el proyecto o ~/.config/opencode), sin salir de él."""
    ruta = Path(patron).expanduser()
    try:
        candidatos = [ruta] if ruta.is_absolute() and not any(c in patron for c in '*?[') \
            else sorted(Path(base).glob(patron))[:50]
    except (ValueError, NotImplementedError):
        return []
    filas = []
    for candidato in candidatos:
        real = candidato.resolve()
        try:
            real.relative_to(limite)
        except ValueError:
            continue
        if real.is_file() and real not in filas:
            filas.append(real)
    return filas


def _markdowns(carpeta, anidados=True):
    """[(nombre, ruta)] de los .md de una carpeta: la ruta relativa sin .md, como OpenCode."""
    if not carpeta.is_dir():
        return []
    try:
        archivos = sorted(carpeta.rglob('*.md') if anidados else carpeta.glob('*.md'))[:MAX_ELEMENTOS]
    except OSError:
        return []
    return [(archivo.relative_to(carpeta).with_suffix('').as_posix(), archivo) for archivo in archivos
            if not any(p.startswith('.') for p in archivo.relative_to(carpeta).parts)]


def _id_mcp(nombre):
    """El cliente MCP sólo admite letras, cifras y guiones (hasta 24)."""
    ident = re.sub(r'[^A-Za-z0-9-]+', '-', nombre).strip('-')[:24].rstrip('-')
    return ident or 'mcp'


class Importadas:
    """Lo que hay para una sesión en `cwd`, con su estado de autorización."""

    def __init__(self, cwd, hogar, ambiente=None):
        self.cwd = Path(cwd).resolve()
        self.hogar = Path(hogar)
        self.ambiente = dict(os.environ if ambiente is None else ambiente)
        self.casa = _casa(self.ambiente)
        self.carpetas, self.raiz = _carpetas(self.cwd)
        self.elementos = []
        self.avisos = []
        self.autorizaciones = Autorizaciones(self.hogar)
        if self.autorizaciones.error:
            self.avisos.append(self.autorizaciones.error)
        for paso in (self._opencode, self._codex, self._claude, self._instrucciones):
            try:
                paso()
            except Exception as exc:  # noqa: BLE001 - una configuración ajena rara no impide abrir la sesión
                self.avisos.append('No se pudo leer todo lo importado (' + type(exc).__name__ + ').')
        self._estados()

    # --- descubrimiento ------------------------------------------------------------

    def _anadir(self, elemento):
        if len(self.elementos) < MAX_ELEMENTOS:
            self.elementos.append(elemento)

    def _mcp(self, herramienta, origen, ambito, nombre, entrada, traducir, base):
        ident = _id_mcp(str(nombre))
        elemento = Elemento('mcp', ident, herramienta, origen, ambito, crudo=entrada)
        if ident != nombre:
            elemento.detalle = '«' + str(nombre)[:60] + '» · '
        try:
            datos, cwd, elemento.activo, elemento.avisos = traducir(entrada, self.ambiente, base)
            elemento.detalle += traduccion.resumen_mcp(datos)
            servidor = configuracion_mcp._servidor(ident, datos, self.ambiente)
            servidor.cwd = cwd
            servidor.procedencia = herramienta + ' · ' + str(origen)
            servidor.nombre_original = str(nombre)
            elemento.valor = servidor
            elemento.avisos += [a.texto for a in configuracion_mcp.avisos(servidor)]
        except (Invalido, configuracion_mcp._Invalido) as exc:
            elemento.problema = str(exc)
        self._anadir(elemento)

    def _agente(self, herramienta, origen, ambito, nombre, datos, cuerpo=''):
        elemento = Elemento('agente', str(nombre), herramienta, origen, ambito, crudo=[datos, cuerpo])
        try:
            if not isinstance(datos, dict):
                raise Invalido('la definición del agente debe ser un objeto')
            if not NOMBRE_DE_AGENTE.fullmatch(str(nombre)):
                raise Invalido('nombre de agente inválido')
            elemento.activo = not (datos.get('disable') is True or datos.get('disabled') is True
                                   or datos.get('hidden') is True)
            tipo = traduccion.agente_opencode(datos, cuerpo, origen.parent)
            if not tipo['instrucciones'].strip() and not datos.get('description'):
                # Una entrada sin prompt ni descripción sólo ajusta un agente de OpenCode (build, plan…).
                raise Invalido('sólo ajusta un agente propio de OpenCode; no hay nada que importar')
            elemento.valor = dict(tipo, origen=herramienta + ' · ' + str(origen))
            modo = datos.get('mode')
            elemento.detalle = (tipo['descripcion'][:120] + (' · sólo lee' if tipo['solo_lectura'] else '')
                                + (' · primario en OpenCode, aquí subagente' if modo == 'primary' else ''))
        except Invalido as exc:
            elemento.problema = str(exc)
        self._anadir(elemento)

    def _comando(self, herramienta, origen, ambito, nombre, datos, cuerpo=None):
        elemento = Elemento('comando', str(nombre), herramienta, origen, ambito, crudo=[datos, cuerpo])
        try:
            if not isinstance(datos, dict):
                raise Invalido('la definición del comando debe ser un objeto')
            if not NOMBRE_DE_COMANDO.fullmatch(str(nombre)):
                raise Invalido('nombre de comando inválido')
            plantilla = cuerpo if cuerpo is not None else datos.get('template')
            if not isinstance(plantilla, str) or not plantilla.strip():
                raise Invalido('falta la plantilla (template o el cuerpo del .md)')
            descripcion = datos.get('description') if isinstance(datos.get('description'), str) else ''
            agente = datos.get('agent') if isinstance(datos.get('agent'), str) else None
            subagente = datos.get('subagent', datos.get('subtask')) is True
            elemento.valor = dict(plantilla=plantilla.strip(), agente=agente, subagente=subagente)
            elemento.detalle = descripcion[:160]
            if traduccion.ORDEN_EN_LINEA.search(plantilla):
                elemento.avisos.append('`!orden` no se ejecuta sola: se le pide al agente, con permisos')
        except Invalido as exc:
            elemento.problema = str(exc)
        self._anadir(elemento)

    def _opencode(self):
        for ruta, ambito in archivos_opencode(self.cwd, self.ambiente):
            try:
                datos = leer_jsonc(ruta)
            except Invalido as exc:
                self.avisos.append(str(ruta) + ': ' + str(exc))
                continue
            mcp = datos.get('mcp')
            if isinstance(mcp, dict):
                servidores = mcp['servers'] if isinstance(mcp.get('servers'), dict) else mcp  # V2 o V1
                for nombre, entrada in servidores.items():
                    self._mcp(OPENCODE, ruta, ambito, nombre, entrada, traduccion.mcp_opencode, self.raiz)
            # V2 `agents` gana a V1 `agent` con el mismo nombre (el primero que entra tapa al otro).
            for clave in ('agents', 'agent'):
                if isinstance(datos.get(clave), dict):
                    for nombre, entrada in datos[clave].items():
                        self._agente(OPENCODE, ruta, ambito, nombre, entrada)
            for clave in ('commands', 'command'):
                if isinstance(datos.get(clave), dict):
                    for nombre, entrada in datos[clave].items():
                        self._comando(OPENCODE, ruta, ambito, nombre, entrada)
        bases = [(c / '.opencode', 'proyecto') for c in self.carpetas]
        bases.append((_config_opencode(self.ambiente), 'usuario'))
        for base, ambito in bases:
            for carpeta in ('agents', 'agent'):
                for nombre, ruta in _markdowns(base / carpeta):
                    self._md(ruta, lambda d, c, n=nombre, r=ruta, a=ambito: self._agente(OPENCODE, r, a, n, d, c))
            for carpeta in ('commands', 'command'):
                for nombre, ruta in _markdowns(base / carpeta):
                    self._md(ruta, lambda d, c, n=nombre, r=ruta, a=ambito: self._comando(OPENCODE, r, a, n, d, c))

    def _md(self, ruta, crear):
        try:
            datos, cuerpo = cabecera(leer_texto(ruta, 64 * 1024))
        except Invalido as exc:
            self.avisos.append(str(ruta) + ': ' + str(exc))
            return
        crear(datos, cuerpo)

    def _codex(self):
        for ruta, ambito in archivos_codex(self.cwd, self.ambiente):
            try:
                datos = leer_toml(ruta)
            except Invalido as exc:
                self.avisos.append(str(ruta) + ': ' + str(exc))
                continue
            servidores = datos.get('mcp_servers')
            if isinstance(servidores, dict):
                base = self.raiz if ambito == 'proyecto' else ruta.parent
                for nombre, entrada in servidores.items():
                    self._mcp(CODEX, ruta, ambito, nombre, entrada, traduccion.mcp_codex, base)

    def _claude(self):
        for carpeta in self.carpetas:
            ruta = carpeta / '.mcp.json'
            if ruta.is_file():
                try:
                    servidores = leer_jsonc(ruta).get('mcpServers')
                except Invalido as exc:
                    self.avisos.append(str(ruta) + ': ' + str(exc))
                    servidores = None
                for nombre, entrada in (servidores.items() if isinstance(servidores, dict) else ()):
                    self._mcp(CLAUDE, ruta, 'proyecto', nombre, entrada, traduccion.mcp_claude, carpeta)
        bases = [(c / '.claude' / 'commands', 'proyecto') for c in self.carpetas]
        bases.append((self.casa / '.claude' / 'commands', 'usuario'))
        for base, ambito in bases:
            # Claude Code nombra el comando por el archivo; la subcarpeta sólo lo agrupa.
            for _, ruta in _markdowns(base):
                self._md(ruta, lambda d, c, r=ruta, a=ambito: self._comando(CLAUDE, r, a, r.stem, d, c))

    def _instrucciones(self):
        """Lo que entra como instrucciones (lo mismo que carga agents_md) y los CLAUDE.md que se omiten."""
        from .. import agents_md
        docs = agents_md.cargar(self.cwd, None)
        self.avisos += docs.avisos
        de_opencode = {ruta for ruta, _ in instrucciones(self.cwd, self.ambiente)[1]}
        cargadas = {ruta.resolve() for ruta, _ in docs.proyecto if '#' not in str(ruta)}
        for ruta, texto in docs.proyecto:
            self._anadir(Elemento('instrucciones', Path(str(ruta).split('#')[0]).name,
                                  _herramienta_de(ruta, de_opencode), ruta, self._ambito(ruta),
                                  detalle=str(len(texto.encode('utf-8'))) + ' bytes'))
        directorios, _ = agents_md.carpetas(self.cwd)
        for directorio in directorios:
            for nombre in agents_md.CLAUDE:
                ruta = directorio / nombre
                if ruta.is_file() and ruta.resolve() not in cargadas:
                    self._anadir(Elemento('instrucciones', nombre, CLAUDE, ruta, 'proyecto',
                                          detalle='repite lo que ya está cargado (p. ej. sólo importa AGENTS.md)',
                                          tapado_por='AGENTS.md'))

    def _ambito(self, ruta):
        try:
            Path(str(ruta).split('#')[0]).resolve().relative_to(self.raiz)
            return 'proyecto'
        except ValueError:
            return 'usuario'

    def lugar(self, ruta):
        """La ruta para enseñarla: relativa al proyecto o con ~ para la carpeta personal."""
        texto = str(ruta)
        for base, prefijo in ((self.raiz, ''), (self.casa, '~/')):
            try:
                return prefijo + Path(texto).relative_to(base).as_posix()
            except ValueError:
                try:
                    return prefijo + Path(texto).relative_to(base.resolve()).as_posix()
                except ValueError:
                    continue
        return texto

    # --- estados ---------------------------------------------------------------------

    def _estados(self):
        vistos = {}
        for elemento in self.elementos:
            if elemento.tipo == 'instrucciones':
                elemento.estado = 'duplicado' if elemento.tapado_por else 'activo'
                continue
            if elemento.problema:
                elemento.estado = 'invalido'
                continue
            if not elemento.activo:
                elemento.estado = 'desactivado'
                continue
            clave = (elemento.tipo, elemento.nombre)
            if clave in vistos:
                elemento.estado, elemento.tapado_por = 'duplicado', self.lugar(vistos[clave].origen)
                continue
            vistos[clave] = elemento
            elemento.estado = (self.autorizaciones.estado(elemento) if elemento.tipo in REQUIEREN_AUTORIZACION
                               else 'activo')

    def buscar(self, clave):
        """Por su clave completa o, si no hay dudas en esta carpeta, por «tipo:nombre» (mcp:docs)."""
        exacto = next((e for e in self.elementos if e.clave == clave), None)
        if exacto is not None:
            return exacto
        cortos = [e for e in self.elementos if e.clave.split('@', 1)[0] == clave
                  and e.estado not in ('duplicado', 'invalido')]
        return cortos[0] if len(cortos) == 1 else None

    def autorizables(self):
        return [e for e in self.elementos if e.tipo in REQUIEREN_AUTORIZACION and e.estado in ('pendiente', 'cambio')]

    def autorizar(self, elementos, red=True):
        for elemento in elementos:
            self.autorizaciones.autorizar(elemento, red=red)
        self.autorizaciones.guardar()
        self._estados()

    def retirar(self, elementos):
        for elemento in elementos:
            self.autorizaciones.retirar(elemento)
        self.autorizaciones.guardar()
        self._estados()

    def informe(self):
        filas = []
        for elemento in self.elementos:
            fila = elemento.como_dict(self.autorizaciones)
            fila['lugar'] = self.lugar(fila['origen'])
            # La clave corta vale en --autorizar si no hay otra igual activa en esta carpeta.
            corta = elemento.clave.split('@', 1)[0]
            fila['corta'] = corta if self.buscar(corta) is elemento else elemento.clave
            filas.append(fila)
        return dict(cwd=str(self.cwd), raiz=str(self.raiz), archivo=str(self.autorizaciones.ruta),
                    elementos=filas, avisos=list(self.avisos))

    # --- lo que usa la sesión --------------------------------------------------------

    def _autorizados(self, tipo):
        return [e for e in self.elementos if e.tipo == tipo and e.estado == 'autorizado']

    def servidores_mcp(self, reservados=()):
        """Servidores autorizados que no choquen con un identificador ya ocupado (mcp.json, complementos)."""
        ocupados = {r if isinstance(r, str) else getattr(r, 'id', '') for r in reservados}
        filas = []
        for elemento in self._autorizados('mcp'):
            if elemento.valor.id in ocupados:
                elemento.estado, elemento.tapado_por = 'duplicado', 'la configuración de AlfalfaCode'
                continue
            elemento.valor.red = bool((self.autorizaciones.registro(elemento) or {}).get('red', True))
            filas.append(elemento.valor)
        return filas

    def diagnosticos_mcp(self):
        filas = []
        for elemento in self.elementos:
            if elemento.tipo != 'mcp':
                continue
            nombre = elemento.nombre + ' (' + elemento.herramienta + ', ' + str(elemento.origen) + ')'
            if elemento.estado in ('pendiente', 'cambio'):
                motivo = 'sin autorizar' if elemento.estado == 'pendiente' else 'cambió desde que lo autorizaste'
                filas.append(configuracion_mcp.Diagnostico(
                    'IMPORTADA-PENDIENTE', 'Servidor MCP importado ' + nombre + ' ' + motivo + '; no se inicia. '
                    'Autorízalo con alfalfa --importar o en Ajustes ▸ Importar.', None, False))
            elif elemento.estado == 'invalido':
                filas.append(configuracion_mcp.Diagnostico('IMPORTADA-INVALIDA', 'Servidor MCP importado ' + nombre
                                                           + ': ' + elemento.problema + '.', None, False))
            elif elemento.estado == 'autorizado':
                for aviso in elemento.avisos:
                    filas.append(configuracion_mcp.Diagnostico('IMPORTADA-AVISO', nombre + ': ' + aviso,
                                                               elemento.nombre, False))
        return filas

    def agentes(self):
        """{nombre: tipo de subagente} de los agentes autorizados."""
        return {e.nombre: dict(e.valor) for e in self._autorizados('agente')}

    def comandos(self):
        return {e.nombre: e for e in self.elementos if e.tipo == 'comando' and e.estado == 'activo'}

    def expandir(self, texto):
        """El mensaje para el modelo si `texto` es «/comando argumentos» importado; si no, None."""
        if not isinstance(texto, str) or not texto.startswith('/'):
            return None
        nombre, _, argumentos = texto[1:].partition(' ')
        nombre = nombre.strip()
        if '\n' in nombre:
            nombre, _, resto = nombre.partition('\n')
            argumentos = resto + (' ' + argumentos if argumentos else '')
        elemento = self.comandos().get(nombre)
        if elemento is None:
            return None
        valor = elemento.valor
        cuerpo = traduccion.expandir_plantilla(valor['plantilla'], argumentos)
        nota = 'Orden /' + nombre + ' (' + elemento.herramienta + ', ' + str(elemento.origen) + ')'
        # El agente de OpenCode sólo se nombra si aquí existe (build y plan son de OpenCode).
        agente = valor['agente'] if valor['agente'] in self.agentes() else None
        if valor['subagente'] or agente:
            nota += ': hazla con un subagente (spawn_agent' + (', agent_type «' + agente + '»' if agente else '') + ')'
        return nota + '.\n\n' + cuerpo
