"""Hooks compatibles con Claude Code para AlfalfaCode.

Traducción y adaptación de codex-rs/hooks (OpenAI Codex, Apache-2.0; ver NOTICE
y PROCEDENCIA.md de esta carpeta): la lectura de `hooks/hooks.json`, el filtro
(engine/matcher.rs), la interpretación de la salida (engine/output_parser.rs)
y la de los códigos de salida de cada evento (events/*.rs).

Eventos con equivalente: SessionStart, UserPromptSubmit, PreToolUse,
PostToolUse y Stop. Los demás eventos de Claude Code y los tipos de hook que no
sean `command` se diagnostican y no se ejecutan.

Diferencias deliberadas con Codex y con Claude Code:
- Cada hook corre con /bin/sh dentro del sandbox Seatbelt de AlfalfaCode (sin
  red, escritura sólo en la carpeta de trabajo y los temporales) y con un
  entorno mínimo: nunca hereda la clave de Ollama ni otros secretos.
- Un PreToolUse que falla (no arranca, vence su plazo, sale con un código
  distinto de 0 y 2, o devuelve una salida inválida) bloquea la herramienta:
  un fallo nunca deja pasar la operación sin querer.
- `permissionDecision: allow` no concede ningún permiso: la herramienta sigue
  pasando por la política de AlfalfaCode, los permisos MCP y la lista de
  confianza del usuario. Sólo sirve, con `updatedInput`, para reescribir la
  entrada, que se vuelve a validar y a autorizar.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import time

from ..procesos import terminar_grupo

EVENTOS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')
# Eventos de Claude Code o de Codex que AlfalfaCode todavía no emite.
SIN_EQUIVALENTE = ('PermissionRequest', 'Notification', 'SubagentStart', 'SubagentStop', 'PreCompact',
                   'PostCompact', 'SessionEnd', 'Interrupt', 'ModelRequest')
# Sólo estos eventos filtran: herramientas, o el origen de la sesión.
CON_FILTRO = ('PreToolUse', 'PostToolUse', 'SessionStart')
CAMPOS_GRUPO = {'matcher', 'hooks'}
CAMPOS_MANEJADOR = {'type', 'command', 'timeout'}
# Como Claude Code: 60 s por defecto. El techo es el de run_command.
TIEMPO_DEFECTO = 60
TIEMPO_MAX = 600
MAX_ARCHIVO = 1024 * 1024
MAX_MANEJADORES = 128
# Salida que se lee de cada hook; lo que sobra se descarta y se diagnostica.
MAX_SALIDA = 64 * 1024
# additionalContext que se entrega al modelo por hook (el tope de Claude Code).
MAX_CONTEXTO = 10000
# Veces que un Stop puede pedir seguir dentro del mismo turno.
MAX_CONTINUACIONES = 3
UNIVERSALES = {'continue', 'stopReason', 'suppressOutput', 'systemMessage'}
ESPECIFICOS = {
    'SessionStart': {'additionalContext'},
    'UserPromptSubmit': {'additionalContext'},
    'PreToolUse': {'permissionDecision', 'permissionDecisionReason', 'updatedInput', 'additionalContext'},
    'PostToolUse': {'additionalContext', 'updatedMCPToolOutput'},
    'Stop': set(),
}
# Campos de primer nivel que acepta cada evento además de los universales.
DECISION = {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop'}
ENTORNO_BASE = ('PATH', 'HOME', 'TMPDIR', 'LANG')
# Prefijos de las variables que el usuario no puede declarar para un hook: las
# que llevan credenciales del arnés.
PROHIBIDAS = re.compile(r'^(OLLAMA_|ALFALFA_HOME$|DYLD_|LD_)')
NOMBRE_VARIABLE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')


@dataclass(frozen=True)
class Diagnostico:
    codigo: str
    texto: str
    origen: str | None = None

    def como_dict(self):
        return dict(codigo=self.codigo, texto=self.texto, origen=self.origen)


class Filtro:
    """engine/matcher.rs: vacío o «*» todo; [A-Za-z0-9_|] lista exacta; si no, regex."""

    def __init__(self, patron):
        self.patron = patron
        if patron in ('', '*'):
            self.tipo, self.valor = 'todo', None
        elif re.fullmatch(r'[A-Za-z0-9_|]+', patron):
            self.tipo, self.valor = 'exacto', set(patron.split('|'))
        else:
            # Rust Regex::is_match busca en cualquier posición, como re.search.
            self.tipo, self.valor = 'regex', re.compile(patron)

    def coincide(self, entradas):
        if self.tipo == 'todo':
            return True
        if self.tipo == 'exacto':
            return any(e in self.valor for e in entradas)
        return any(self.valor.search(e) for e in entradas)


@dataclass(frozen=True)
class Manejador:
    evento: str
    filtro: Filtro = field(compare=False)
    comando: str
    tiempo: int
    origen: str
    archivo: str
    entorno: tuple = ()

    def clave(self):
        # Claude Code no repite una misma orden dos veces en un evento.
        return (self.evento, self.comando, self.entorno)

    def resumen(self):
        return dict(evento=self.evento, filtro=self.filtro.patron, origen=self.origen, archivo=self.archivo,
                    tiempo=self.tiempo)


@dataclass
class Ejecucion:
    manejador: Manejador
    codigo: int | None = None
    stdout: str = ''
    stderr: str = ''
    error: str | None = None
    truncado: bool = False
    duracion: float = 0.0


@dataclass
class Resultado:
    """Lo que el motor debe aplicar tras un evento."""
    bloquear: bool = False
    motivo: str | None = None
    contextos: list = field(default_factory=list)
    entrada: dict | None = None
    detener: bool = False
    motivo_detener: str | None = None
    avisos: list = field(default_factory=list)
    ejecuciones: list = field(default_factory=list)

    def como_dict(self):
        return dict(bloquear=self.bloquear, motivo=self.motivo, contextos=self.contextos,
                    entrada_modificada=self.entrada is not None, detener=self.detener,
                    motivo_detener=self.motivo_detener, avisos=self.avisos, ejecuciones=self.ejecuciones)


def _entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def leer_fuente(archivo, origen, entorno=None, datos=None):
    """(manejadores, diagnósticos) de un hooks.json o de un objeto ya leído."""
    diagnosticos, manejadores = [], []
    nombre = str(archivo)
    try:
        if datos is None:
            ruta = Path(archivo)
            if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > MAX_ARCHIVO:
                raise ValueError('debe ser un archivo regular de hasta 1 MB, sin enlaces')
            datos = json.loads(ruta.read_text(encoding='utf-8'))
        if not isinstance(datos, dict) or not isinstance(datos.get('hooks'), dict):
            raise ValueError('debe ser un objeto con la clave «hooks»')
        sobran = sorted(set(datos) - {'hooks', 'description'})
        if sobran:
            diagnosticos.append(Diagnostico('HOOK-CAMPO', 'Campos desconocidos ignorados: ' + ', '.join(sobran) + '.', origen))
    except (OSError, ValueError, UnicodeError) as exc:
        motivo = str(exc)[:200] if type(exc) is ValueError else type(exc).__name__
        return [], [Diagnostico('HOOK-ARCHIVO', nombre + ' inválido: ' + motivo + '; no se carga ningún hook.', origen)]
    for evento, grupos in datos['hooks'].items():
        if evento in SIN_EQUIVALENTE:
            diagnosticos.append(Diagnostico('HOOK-SIN-EQUIVALENTE', evento + ' no tiene equivalente en AlfalfaCode; '
                                            'sus hooks no se ejecutan.', origen))
            continue
        if evento not in EVENTOS:
            diagnosticos.append(Diagnostico('HOOK-EVENTO', 'Evento desconocido ' + str(evento)[:80] + '; se ignora.', origen))
            continue
        if not isinstance(grupos, list):
            diagnosticos.append(Diagnostico('HOOK-FORMATO', evento + ' debe ser una lista de grupos; se ignora.', origen))
            continue
        for grupo in grupos:
            if not isinstance(grupo, dict) or not isinstance(grupo.get('hooks'), list):
                diagnosticos.append(Diagnostico('HOOK-FORMATO', 'Cada grupo de ' + evento + ' necesita una lista «hooks»; se ignora.', origen))
                continue
            sobran = sorted(set(grupo) - CAMPOS_GRUPO)
            if sobran:
                diagnosticos.append(Diagnostico('HOOK-CAMPO', 'Campos de grupo sin equivalente ignorados en ' + evento
                                                + ': ' + ', '.join(sobran) + '.', origen))
            patron = grupo.get('matcher', '')
            if not isinstance(patron, str):
                diagnosticos.append(Diagnostico('HOOK-FILTRO', 'El matcher de ' + evento + ' debe ser texto; grupo ignorado.', origen))
                continue
            if patron not in ('', '*') and evento not in CON_FILTRO:
                diagnosticos.append(Diagnostico('HOOK-FILTRO', evento + ' no filtra: se ignora el matcher «'
                                                + patron[:80] + '» y el grupo vale para todos.', origen))
                patron = ''
            try:
                filtro = Filtro(patron)
            except re.error as exc:
                diagnosticos.append(Diagnostico('HOOK-FILTRO', 'Matcher inválido «' + patron[:80] + '» en ' + evento
                                                + ' (' + str(exc)[:120] + '); grupo ignorado.', origen))
                continue
            for definicion in grupo['hooks']:
                if not isinstance(definicion, dict):
                    diagnosticos.append(Diagnostico('HOOK-FORMATO', 'Hook no válido en ' + evento + '; se ignora.', origen))
                    continue
                tipo = definicion.get('type', 'command')
                if tipo != 'command':
                    diagnosticos.append(Diagnostico('HOOK-TIPO', 'Hooks de tipo «' + str(tipo)[:40] + '» sin equivalente '
                                                    'en AlfalfaCode (sólo command); no se ejecuta.', origen))
                    continue
                sobran = sorted(set(definicion) - CAMPOS_MANEJADOR)
                if sobran:
                    diagnosticos.append(Diagnostico('HOOK-CAMPO', 'Campos sin equivalente ignorados en un hook de ' + evento
                                                    + ': ' + ', '.join(sobran) + ('; async corre en serie' if 'async' in sobran else '')
                                                    + '.', origen))
                comando = definicion.get('command')
                if not isinstance(comando, str) or not comando.strip() or len(comando) > 8000 or '\0' in comando:
                    diagnosticos.append(Diagnostico('HOOK-ORDEN', 'Hook de ' + evento + ' sin orden válida; se ignora.', origen))
                    continue
                tiempo = definicion.get('timeout', TIEMPO_DEFECTO)
                if not (_entero(tiempo) or (isinstance(tiempo, float) and tiempo.is_integer())) or tiempo < 1:
                    diagnosticos.append(Diagnostico('HOOK-PLAZO', 'timeout de ' + evento + ' inválido; se usan '
                                                    + str(TIEMPO_DEFECTO) + ' s.', origen))
                    tiempo = TIEMPO_DEFECTO
                if tiempo > TIEMPO_MAX:
                    diagnosticos.append(Diagnostico('HOOK-PLAZO', 'timeout de ' + evento + ' recortado a '
                                                    + str(TIEMPO_MAX) + ' s.', origen))
                    tiempo = TIEMPO_MAX
                if len(manejadores) >= MAX_MANEJADORES:
                    diagnosticos.append(Diagnostico('HOOK-LIMITE', 'Más de ' + str(MAX_MANEJADORES)
                                                    + ' hooks en ' + nombre + '; el resto se ignora.', origen))
                    return manejadores, diagnosticos
                manejadores.append(Manejador(evento, filtro, comando, int(tiempo), origen, nombre,
                                             tuple(sorted((entorno or {}).items()))))
    return manejadores, diagnosticos


def entorno_declarado(valores, origen):
    """Variables que el usuario declara para los hooks; nunca las del arnés."""
    limpias, diagnosticos = {}, []
    if not isinstance(valores, dict):
        return {}, [Diagnostico('HOOK-ENTORNO', 'entorno debe ser un objeto {VARIABLE: valor}; se ignora.', origen)]
    for clave, valor in valores.items():
        if (not isinstance(clave, str) or not NOMBRE_VARIABLE.match(clave) or PROHIBIDAS.match(clave)
                or clave in ENTORNO_BASE or not isinstance(valor, str) or len(valor) > 4096 or '\0' in valor):
            diagnosticos.append(Diagnostico('HOOK-ENTORNO', 'Variable de entorno no admitida: ' + str(clave)[:64]
                                            + '; se ignora.', origen))
            continue
        limpias[clave] = valor
    return limpias, diagnosticos


def _texto(datos):
    return datos.decode('utf-8', errors='replace')


async def _leer(flujo, limite):
    """Lee hasta `limite` bytes y descarta el resto sin bloquear al proceso."""
    guardado, sobra = bytearray(), False
    while True:
        trozo = await flujo.read(65536)
        if not trozo:
            return bytes(guardado), sobra
        espacio = limite - len(guardado)
        if espacio > 0:
            guardado += trozo[:espacio]
        if len(trozo) > max(espacio, 0):
            sobra = True


class MotorHooks:
    """Hooks de una sesión de AlfalfaCode: selecciona, ejecuta e interpreta."""

    def __init__(self, manejadores, diagnosticos=(), *, raiz, sandbox=None, entorno_base=None):
        self.manejadores = list(manejadores)
        self.diagnosticos = list(diagnosticos)
        self.raiz = str(Path(raiz).resolve())
        # Fábrica (raíz) -> SeatbeltSandbox; None sólo en pruebas sin macOS.
        self.sandbox = sandbox
        self.entorno_base = entorno_base
        self.secretos = set()

    @classmethod
    def desde_fuentes(cls, fuentes, *, raiz, sandbox=None):
        """fuentes: [{archivo, origen, raiz_complemento?, entorno?, datos?}] ya autorizadas."""
        manejadores, diagnosticos = [], []
        for fuente in fuentes:
            origen = fuente['origen']
            entorno, avisos = entorno_declarado(fuente.get('entorno') or {}, origen)
            diagnosticos += avisos
            complemento = fuente.get('raiz_complemento')
            if complemento:
                # Las mismas variables que Codex expone a los hooks de un complemento.
                for clave in ('PLUGIN_ROOT', 'CLAUDE_PLUGIN_ROOT'):
                    entorno[clave] = str(complemento)
                for clave in ('PLUGIN_DATA', 'CLAUDE_PLUGIN_DATA'):
                    if fuente.get('datos_complemento'):
                        entorno[clave] = str(fuente['datos_complemento'])
            nuevos, avisos = leer_fuente(fuente['archivo'], origen, entorno, fuente.get('datos'))
            manejadores += nuevos
            diagnosticos += avisos
        return cls(manejadores, diagnosticos, raiz=raiz, sandbox=sandbox)

    def activo(self):
        return bool(self.manejadores)

    def seleccionar(self, evento, entradas=None):
        vistos, elegidos = set(), []
        for manejador in self.manejadores:
            if manejador.evento != evento:
                continue
            if evento in CON_FILTRO and entradas is not None and not manejador.filtro.coincide(entradas):
                continue
            if manejador.clave() in vistos:
                continue
            vistos.add(manejador.clave())
            elegidos.append(manejador)
        return elegidos

    def entorno(self, manejador):
        base = self.entorno_base if self.entorno_base is not None else os.environ
        env = {k: base[k] for k in ENTORNO_BASE if k in base}
        env['CLAUDE_PROJECT_DIR'] = self.raiz
        env.update(dict(manejador.entorno))
        return env

    async def ejecutar(self, manejador, entrada):
        """Una ejecución con plazo, salida acotada y cancelación del grupo entero."""
        argv = ['/bin/sh', '-c', manejador.comando]
        inicio = time.monotonic()
        resultado = Ejecucion(manejador)
        try:
            if self.sandbox is not None:
                argv = await self.sandbox(self.raiz).prepare(argv)
        except Exception as exc:  # SandboxUnavailable u OSError: no se lanza sin sandbox.
            resultado.error = 'El sandbox no está disponible: ' + (str(exc) or type(exc).__name__)[:300]
            return resultado
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.raiz, env=self.entorno(manejador), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except (OSError, ValueError) as exc:
            resultado.error = 'No se pudo iniciar el hook: ' + (str(exc) or type(exc).__name__)[:300]
            return resultado
        datos = json.dumps(entrada, ensure_ascii=False).encode('utf-8')

        async def escribir():
            try:
                proc.stdin.write(datos)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # el hook no leyó su entrada: no es un error del arnés
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        async def completo():
            _, salida, error, _ = await asyncio.gather(escribir(), _leer(proc.stdout, MAX_SALIDA),
                                                       _leer(proc.stderr, MAX_SALIDA), proc.wait())
            return salida, error

        try:
            (salida, sobra_salida), (error, sobra_error) = await asyncio.wait_for(completo(), manejador.tiempo)
        except asyncio.TimeoutError:
            await terminar_grupo(proc)
            resultado.error = 'El hook no terminó en ' + str(manejador.tiempo) + ' s y se detuvo.'
            resultado.duracion = time.monotonic() - inicio
            return resultado
        except asyncio.CancelledError:
            await terminar_grupo(proc)
            raise
        resultado.codigo = proc.returncode
        resultado.stdout, resultado.stderr = _texto(salida), _texto(error)
        resultado.truncado = sobra_salida or sobra_error
        resultado.duracion = time.monotonic() - inicio
        if sobra_salida:
            resultado.error = 'La salida del hook supera ' + str(MAX_SALIDA // 1024) + ' KB; se descarta.'
        return resultado

    async def disparar(self, evento, entrada, entradas=None):
        """Ejecuta en orden los hooks que coinciden y combina sus decisiones."""
        resultado = Resultado()
        for manejador in self.seleccionar(evento, entradas):
            ejecucion = await self.ejecutar(manejador, entrada)
            interpretar(evento, ejecucion, resultado)
            resultado.ejecuciones.append(dict(manejador.resumen(), codigo=ejecucion.codigo,
                                              error=ejecucion.error, truncado=ejecucion.truncado,
                                              duracion=round(ejecucion.duracion, 3)))
            if resultado.bloquear and evento == 'PreToolUse':
                # Una herramienta bloqueada no se ejecuta: los siguientes hooks
                # no cambiarían nada y sólo gastarían tiempo.
                break
        return resultado


# Interpretación ------------------------------------------------------------

class SalidaInvalida(ValueError):
    pass


def _pide(evento, texto):
    return evento + ': ' + texto


def _objeto_json(stdout):
    texto = stdout.strip()
    if not texto:
        return None
    if not texto.startswith(('{', '[')):
        return 'texto'
    try:
        valor = json.loads(texto)
    except ValueError:
        raise SalidaInvalida('el hook devolvió JSON inválido') from None
    if not isinstance(valor, dict):
        raise SalidaInvalida('la salida JSON debe ser un objeto')
    return valor


def _validar(evento, valor, avisos):
    """Comprueba tipos de los campos conocidos (output_parser.rs); avisa de los desconocidos."""
    permitidos = UNIVERSALES | {'hookSpecificOutput'} | ({'decision', 'reason'} if evento in DECISION else set())
    sobran = sorted(set(valor) - permitidos)
    if sobran:
        avisos.append(_pide(evento, 'campos sin equivalente ignorados: ' + ', '.join(sobran) + '.'))
    for campo, tipo in (('continue', bool), ('suppressOutput', bool), ('stopReason', str),
                        ('systemMessage', str), ('reason', str)):
        if campo in valor and not isinstance(valor[campo], tipo):
            raise SalidaInvalida(campo + ' tiene un tipo inválido')
    if 'decision' in valor and valor['decision'] not in (('approve', 'block') if evento == 'PreToolUse' else ('block',)):
        raise SalidaInvalida('decision inválida: ' + str(valor['decision'])[:40])
    especifico = valor.get('hookSpecificOutput')
    if especifico is None:
        return {}
    if not isinstance(especifico, dict) or especifico.get('hookEventName') != evento:
        raise SalidaInvalida('hookSpecificOutput debe ser un objeto con hookEventName «' + evento + '»')
    sobran = sorted(set(especifico) - ESPECIFICOS[evento] - {'hookEventName'})
    if sobran:
        avisos.append(_pide(evento, 'campos de hookSpecificOutput sin equivalente ignorados: ' + ', '.join(sobran) + '.'))
    if 'additionalContext' in especifico and not isinstance(especifico['additionalContext'], str):
        raise SalidaInvalida('additionalContext debe ser texto')
    return especifico


def _contexto(resultado, texto, manejador):
    texto = texto.strip()
    if not texto:
        return
    if len(texto) > MAX_CONTEXTO:
        resultado.avisos.append('Contexto de un hook recortado a ' + str(MAX_CONTEXTO) + ' caracteres ('
                                + manejador.origen + ').')
        texto = texto[:MAX_CONTEXTO]
    resultado.contextos.append(texto)


def _bloquea(resultado, motivo):
    resultado.bloquear = True
    if resultado.motivo is None:
        resultado.motivo = motivo


def interpretar(evento, ejecucion, resultado):
    """Aplica la salida de un hook al resultado combinado del evento."""
    manejador = ejecucion.manejador
    quien = 'Hook ' + evento + ' (' + manejador.origen + ')'
    if ejecucion.stderr.strip() and ejecucion.codigo not in (0, 2):
        resultado.avisos.append(quien + ' stderr: ' + ejecucion.stderr.strip()[:500])
    fallo = ejecucion.error
    if fallo is None and ejecucion.codigo == 2:
        motivo = ejecucion.stderr.strip()
        if not motivo:
            fallo = 'salió con código 2 sin escribir el motivo en stderr'
        elif evento == 'PreToolUse':
            _bloquea(resultado, motivo)
        elif evento == 'PostToolUse':
            resultado.contextos.append(motivo[:MAX_CONTEXTO])
        elif evento == 'UserPromptSubmit':
            _bloquea(resultado, motivo)
        elif evento == 'Stop':
            _bloquea(resultado, motivo)
        else:
            resultado.avisos.append(quien + ': el código 2 no bloquea este evento. ' + motivo[:300])
        if not fallo:
            return
    if fallo is None and ejecucion.codigo != 0:
        fallo = 'salió con código ' + str(ejecucion.codigo)
    if fallo is None:
        try:
            valor = _objeto_json(ejecucion.stdout)
            if valor == 'texto':
                # Como Claude Code: en estos eventos el texto plano es contexto.
                if evento in ('SessionStart', 'UserPromptSubmit'):
                    _contexto(resultado, ejecucion.stdout, manejador)
                return
            if valor is not None:
                _aplicar(evento, valor, resultado, manejador)
            return
        except SalidaInvalida as exc:
            fallo = str(exc)
    resultado.avisos.append(quien + ' falló: ' + fallo + '.')
    if evento == 'PreToolUse':
        _bloquea(resultado, quien + ' falló (' + fallo + '); la herramienta no se ejecutó.')


def _aplicar(evento, valor, resultado, manejador):
    avisos = []
    especifico = _validar(evento, valor, avisos)
    resultado.avisos += avisos
    if valor.get('systemMessage'):
        resultado.avisos.append(valor['systemMessage'][:1000])
    if evento == 'PreToolUse':
        for campo, texto in (('continue', 'continue:false'), ('stopReason', 'stopReason'),
                             ('suppressOutput', 'suppressOutput')):
            if campo in valor and (valor[campo] is not True if campo == 'continue' else bool(valor[campo])):
                raise SalidaInvalida(texto + ' no está soportado en PreToolUse')
        decision = especifico.get('permissionDecision')
        motivo = especifico.get('permissionDecisionReason')
        nueva = especifico.get('updatedInput')
        if motivo is not None and not isinstance(motivo, str):
            raise SalidaInvalida('permissionDecisionReason debe ser texto')
        if decision is not None and decision not in ('allow', 'deny', 'ask'):
            raise SalidaInvalida('permissionDecision inválida: ' + str(decision)[:40])
        if nueva is not None and decision != 'allow':
            raise SalidaInvalida('updatedInput exige permissionDecision:allow')
        if nueva is not None and not isinstance(nueva, dict):
            raise SalidaInvalida('updatedInput debe ser un objeto')
        if decision == 'ask':
            # AlfalfaCode decide cuándo preguntar; obedecer «ask» a ciegas no es
            # posible, así que se bloquea antes que ejecutar sin preguntar.
            raise SalidaInvalida('permissionDecision:ask no está soportado')
        if decision == 'deny' or (decision is None and valor.get('decision') == 'block'):
            texto = (motivo if decision == 'deny' else valor.get('reason')) or ''
            if not texto.strip():
                raise SalidaInvalida('bloqueó sin un motivo no vacío')
            _bloquea(resultado, texto.strip())
        elif decision is None and motivo is not None:
            raise SalidaInvalida('permissionDecisionReason sin permissionDecision')
        elif decision is None and valor.get('reason') is not None and valor.get('decision') is None:
            raise SalidaInvalida('reason sin decision')
        if decision == 'allow' or valor.get('decision') == 'approve':
            resultado.avisos.append('Hook PreToolUse (' + manejador.origen + '): «allow» no concede permisos en '
                                    'AlfalfaCode; la herramienta sigue pidiendo los que le toquen.')
        if nueva is not None and not resultado.bloquear:
            # Como Codex: gana la reescritura del último hook que terminó.
            resultado.entrada = nueva
        if 'additionalContext' in especifico:
            _contexto(resultado, especifico['additionalContext'], manejador)
        return
    if valor.get('continue') is False:
        resultado.detener = True
        resultado.motivo_detener = resultado.motivo_detener or valor.get('stopReason') or 'Un hook detuvo el turno.'
    if valor.get('suppressOutput'):
        resultado.avisos.append('Hook ' + evento + ': suppressOutput no tiene equivalente; la salida se muestra.')
    if valor.get('decision') == 'block':
        motivo = (valor.get('reason') or '').strip()
        if not motivo:
            raise SalidaInvalida('decision:block sin un reason no vacío')
        if evento == 'PostToolUse':
            resultado.contextos.append(motivo[:MAX_CONTEXTO])
        else:
            _bloquea(resultado, motivo)
    elif valor.get('reason') is not None and evento == 'PostToolUse' and valor.get('continue', True):
        raise SalidaInvalida('reason sin decision')
    if 'updatedMCPToolOutput' in especifico:
        resultado.avisos.append('Hook PostToolUse: updatedMCPToolOutput no está soportado; el resultado no cambia.')
    if 'additionalContext' in especifico:
        _contexto(resultado, especifico['additionalContext'], manejador)
