"""De los eventos de `--stdio` v2 a mensajes PACAI 0.2 (anexo A de la especificación).

`Traductor` no hace entrada y salida ni espera nada: recibe un evento v2 (`tipo` y campos) y devuelve la lista
de mensajes PACAI que le corresponden, ya con su `seq`, su `id` y su hora. Lleva el estado que PACAI exige y v2
no tiene: ejecuciones (raíz y subejecuciones), herramientas abiertas, aprobaciones pendientes y su digest.

Lo que el núcleo no cubre va como extensión `alfalfa.*` (§8), con los campos de v2 en inglés (`nombres.py`).
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .jcs import digest
from .nombres import TIPOS, en_ingles
from .sobre import Emisor, ahora, en_segundos

__all__ = ['Traductor', 'archivos_del_diff', 'CADUCIDAD_APROBACION', 'NAVEGADOR']

# Segundos que una aprobación espera respuesta antes de darse por caducada (§4.5): PACAI 0.2 obliga a mandar
# `expires`, así que no puede no caducar; un día deja al usuario volver a su mesa (y responder al día siguiente)
# sin que una petición olvidada quede abierta para siempre. `ALFALFA_CADUCIDAD_APROBACION` o
# `--caducidad-aprobacion` la cambian.
CADUCIDAD_APROBACION = 24 * 3600
# La herramienta del cliente que hace de navegador propio de la app (`--navegador`): su `hello` la declara.
NAVEGADOR = 'browser'
TROZO = 4000                  # caracteres de texto por `message_delta`
MAX_ARGUMENTOS = 256 * 1024   # bytes de los `arguments` de un `tool_start` (el transporte lo ajusta al negociado)
RECORTADO = '… [recortado]'
MAX_SALIDA = 60000            # caracteres de la salida de una herramienta en un `tool_end`
EDITORES = frozenset({'write_file', 'edit_file', 'apply_patch'})
SINTETICA = 'request_permission'
# Estados de un subagente (subagentes.py) con los que ya no trabaja.
TERMINADOS = frozenset({'terminado', 'fallido', 'cerrado', 'interrumpido'})
RESULTADO_DE_TURNO = {'completed': 'success', 'blocked': 'blocked', 'failed': 'failed', 'cortado': 'interrupted',
                      'interrupted': 'interrupted'}
# Eventos v2 de AlfalfaCode sin equivalente en el núcleo → tipo de la extensión: `nombres.TIPOS`.
# `motivo` de un permiso v2 → `reason` de PACAI (§4.5). Los demás van como `alfalfa.<motivo>` si se negoció.
RAZON_DE = {'peligroso': 'dangerous', 'red': 'network', 'salir_sandbox': 'outside_sandbox',
            'reintento': 'sandbox_denied_retry', 'regla': 'policy', 'modo': 'policy'}
# Los demás motivos de AlfalfaCode, con nombre en inglés: `alfalfa.<nombre>` si se negoció la extensión.
RAZON_PROPIA = {'navegador': 'browser', 'edicion': 'edit', 'contenido_externo': 'external_content',
                'aprendizaje': 'learning'}
# Campos de un permiso v2 que PACAI no tiene: viajan en `payload.alfalfa` (en inglés) si se negoció la extensión.
PERMISO_EXTRA = ('argv', 'proposito', 'motivos', 'justificacion', 'regla', 'intento', 'reintento_de', 'red',
                 'sin_sandbox', 'fallo_sandbox', 'mcp', 'app', 'cwd', 'timeout_seconds', 'diff', 'sitio', 'prefijo')
CODIGOS_DE_ERROR = {'Línea demasiado larga.': 'message_too_large', 'Se requiere UTF-8.': 'invalid_message'}


@dataclass
class Herramienta:
    id: str
    run_id: str
    nombre: str
    argumentos: dict
    lado: str = 'agent'
    v2id: str | None = None          # petición del navegador de la app (lado cliente)
    sintetica: bool = False          # la abrió un permiso que ninguna herramienta abierta explicaba


@dataclass
class Ejecucion:
    run_id: str
    agente: str | None = None        # id del subagente en AlfalfaCode (aN); None en la raíz
    activa: bool = True


@dataclass
class Aprobacion:
    id: str
    v2id: str
    run_id: str
    tool_call_id: str
    nombre: str
    digest: str
    scopes: list[str]
    expira: datetime
    sitio: bool = False               # v2 pide `sitio` (una_vez|sesion|proyecto) en vez de `sesion`
    estado: str = 'pending'           # pending, approved, denied, expired


def _json_seguro(valor: Any) -> dict | None:
    """El valor como objeto JSON canonizable, o None si no lo es (NaN, claves que no son texto…)."""
    try:
        copia = json.loads(json.dumps(valor, default=str, allow_nan=False))
        digest(copia)
    except (TypeError, ValueError, OverflowError):
        return None
    return copia if isinstance(copia, dict) else None


def argumentos_de(texto: str, datos: dict | None) -> dict:
    """Los argumentos de una herramienta a partir de su evento v2 de inicio.

    El motor escribe `Inicio <nombre> · <argumentos>` con los argumentos ya sin secretos y recortados a 1000
    caracteres. Si caben, son un literal de Python y valen tal cual; si no, se rehace lo que enseña la app
    (orden, carpeta, archivos) y se avisa con `truncated`.
    """
    cuerpo = texto.split(' · ', 1)[1] if texto.startswith('Inicio ') and ' · ' in texto else ''
    if cuerpo:
        try:
            valor = ast.literal_eval(cuerpo)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            valor = None
        limpio = _json_seguro(valor) if isinstance(valor, dict) else None
        if limpio is not None:
            return limpio
    datos = datos or {}
    args: dict[str, Any] = {'truncated': True}
    for origen, destino in (('orden', 'command'), ('cwd', 'cwd'), ('objetivo', 'target'), ('objetivos', 'targets')):
        if datos.get(origen):
            args[destino] = datos[origen]
    return args


def _tamano(valor: Any) -> int:
    return len(json.dumps(valor, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def recortar_argumentos(args: dict, limite: int) -> tuple[dict, bool]:
    """Los argumentos tal cual si caben en `limite` bytes; si no, una copia con los textos más largos recortados
    (acaban en «… [recortado]») y True. Si ni así caben, se quedan los campos que no son texto largo."""
    if _tamano(args) <= limite:
        return args, False
    copia = json.loads(json.dumps(args))
    for _ in range(200):
        cadenas: list[tuple[Any, Any]] = []
        pendientes = [copia]
        while pendientes:
            actual = pendientes.pop()
            for clave, hijo in (actual.items() if isinstance(actual, dict) else enumerate(actual)):
                if isinstance(hijo, str) and len(hijo) > 200:
                    cadenas.append((actual, clave))
                elif isinstance(hijo, (dict, list)):
                    pendientes.append(hijo)
        if not cadenas:
            break
        contenedor, clave = max(cadenas, key=lambda par: len(par[0][par[1]]))
        texto = contenedor[clave].removesuffix(RECORTADO)
        contenedor[clave] = texto[:len(texto) // 2] + RECORTADO
        if _tamano(copia) <= limite:
            return copia, True
    resto = {k: v for k, v in copia.items() if _tamano(v) <= 1000}
    return (resto if _tamano(resto) <= limite else {}), True


def archivos_del_diff(parche: str) -> list[dict]:
    """`path`, `added` y `removed` de cada archivo de un diff unificado.

    Se cuentan las líneas de cada hunk según su cabecera `@@ -a,b +c,d @@`: una línea añadida que empiece por
    `++` no se confunde con la cabecera `+++` del archivo siguiente."""
    archivos: list[dict] = []
    actual: dict | None = None
    viejas = nuevas = 0
    for linea in parche.splitlines():
        if viejas > 0 or nuevas > 0:
            if linea.startswith('+'):
                actual['added'] += 1
                nuevas -= 1
            elif linea.startswith('-'):
                actual['removed'] += 1
                viejas -= 1
            elif not linea.startswith('\\'):
                viejas, nuevas = viejas - 1, nuevas - 1
        elif linea.startswith('+++ '):
            ruta = linea[4:].strip()
            actual = {'path': ruta[2:] if ruta.startswith('b/') else ruta, 'added': 0, 'removed': 0}
            archivos.append(actual)
        elif linea.startswith('@@') and actual is not None:
            cabecera = re.match(r'@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@', linea)
            if cabecera:
                viejas = int(cabecera.group(1) if cabecera.group(1) is not None else 1)
                nuevas = int(cabecera.group(2) if cabecera.group(2) is not None else 1)
    return archivos


def _fallo(datos: dict) -> bool:
    """Como el motor: la clasificación manda; sin ella, un `error` o un código de salida distinto de 0."""
    clasificacion = datos.get('clasificacion')
    if isinstance(clasificacion, dict) and 'disposition' in clasificacion:
        return clasificacion['disposition'] == 'failure'
    return bool(datos.get('error')) or datos.get('codigo', 0) != 0


class Traductor:
    def __init__(self, emisor: Emisor, *, perfiles=(), extensiones=(), herramientas_cliente=(),
                 caducidad: float = CADUCIDAD_APROBACION, reloj: Callable[[], datetime] = ahora,
                 max_argumentos: int = MAX_ARGUMENTOS):
        self.emisor, self.reloj, self.max_argumentos = emisor, reloj, max_argumentos
        self.perfiles, self.extensiones = set(perfiles), set(extensiones)
        self.herramientas_cliente = set(herramientas_cliente)
        self.caducidad = caducidad
        self.raiz: str | None = None                       # ejecución raíz activa
        self.ejecuciones: dict[str, Ejecucion] = {}
        self.subagentes: dict[str, str] = {}                # id aN → run_id de su invocación actual
        self.herramientas: dict[str, Herramienta] = {}      # abiertas, por tool_call_id
        self.aprobaciones: dict[str, Aprobacion] = {}
        self.permisos: dict[str, str] = {}                  # id del permiso v2 → id del approval_request
        self.de_sesion: list[tuple[str, str, str]] = []     # (nombre, digest, approval_id) con alcance session
        self.hay_cambios = False
        # Quien conteste a v2 cuando PACAI no puede (navegador sin herramienta del cliente): (id v2, texto).
        self.al_rechazar_navegador: Callable[[str, str], None] | None = None

    # -- utilidades --------------------------------------------------------
    @property
    def trabajo(self) -> bool:
        return 'work' in self.perfiles

    def _ext(self, nombre: str, payload: dict) -> list[dict]:
        if 'alfalfa' not in self.extensiones:
            return []
        return [self.emisor.mensaje('alfalfa.' + nombre, en_ingles(payload))]

    def _msg(self, tipo: str, payload: dict, reply_to: str | None = None) -> dict:
        return self.emisor.mensaje(tipo, payload, reply_to=reply_to)

    def _activa(self, run_id: str | None) -> bool:
        return run_id is not None and run_id in self.ejecuciones and self.ejecuciones[run_id].activa

    # -- entrada principal -------------------------------------------------
    def evento(self, tipo: str, campos: dict) -> list[dict]:
        """Los mensajes PACAI de un evento v2. Los que no tienen equivalente salen como extensión."""
        manejador = getattr(self, '_v2_' + tipo, None)
        if manejador is not None:
            return manejador(campos)
        return self._ext(TIPOS.get(tipo, tipo), {k: v for k, v in campos.items() if k != 'version'})

    # -- ejecuciones (§4.2) ------------------------------------------------
    def empezar_turno(self, input_id: str) -> list[dict]:
        """Un turno del motor abre una ejecución raíz: el `id` del `run_started` es su `run_id`."""
        m = self._msg('run_started', {'input_id': input_id})
        self.raiz = m['id']
        self.ejecuciones[m['id']] = Ejecucion(m['id'])
        self.hay_cambios = False
        return [m]

    def _cerrar_herramientas(self, run_id: str, estado: str = 'cancelled') -> list[dict]:
        """Cierra las herramientas del agente que siguen abiertas en una ejecución (las del cliente las cierra él)."""
        salida = []
        for tc in [t for t, h in self.herramientas.items() if h.run_id == run_id and h.lado == 'agent']:
            salida += self._cerrar(tc, estado)
        return salida

    def _cerrar(self, tc: str, estado: str, salida: list | None = None, resumen: str | None = None) -> list[dict]:
        h = self.herramientas.pop(tc)
        for a in self.aprobaciones.values():
            if a.tool_call_id == tc and a.estado == 'pending':
                a.estado = 'denied'
        payload: dict[str, Any] = {'tool_call_id': tc, 'status': estado}
        if salida:
            payload['output'] = salida
        if resumen:
            payload['summary'] = resumen[:300]
        if h.lado == 'client':
            return []
        return [self._msg('tool_end', payload)]

    def _terminar(self, run_id: str, resultado: str, detalle: str | None = None) -> list[dict]:
        ej = self.ejecuciones[run_id]
        salida = self._cerrar_herramientas(run_id)
        payload = {'run_id': run_id, 'outcome': resultado}
        if resultado in ('failed', 'blocked'):
            payload['detail'] = (detalle or 'sin detalle')[:1000]
        salida.append(self._msg('run_finished', payload))
        ej.activa = False
        if ej.agente is not None and self.subagentes.get(ej.agente) == run_id:
            del self.subagentes[ej.agente]
        return salida

    def herramientas_del_cliente_abiertas(self) -> list[str]:
        return [t for t, h in self.herramientas.items() if h.lado == 'client']

    def cerrar_turno(self, campos: dict, diff: str | dict | None = None) -> list[dict]:
        """`turno_terminado` → lo que queda del turno (subejecuciones, uso, cambios) y su `run_finished`."""
        if self.raiz is None:
            return []
        raiz = self.raiz
        salida: list[dict] = []
        for run_id, ej in list(self.ejecuciones.items()):
            if ej.activa and ej.agente is not None:      # de las hojas a la raíz (§4.6, §4.9)
                salida += self._terminar(run_id, 'interrupted')
        resultado = RESULTADO_DE_TURNO.get(campos.get('estado'), 'failed')
        uso = {}
        if isinstance(campos.get('coste_turno'), (int, float)):
            uso['cost'] = {'amount': campos['coste_turno'], 'currency': 'USD'}
        if uso or campos.get('tokens_turno'):
            extra = {'turn_tokens': campos['tokens_turno']} if isinstance(campos.get('tokens_turno'), int) else {}
            salida.append(self._msg('usage', {'run_id': raiz, **uso, **self._alfalfa(extra)}))
        if self.trabajo and diff:
            # Un diff unificado (instantáneas) o ya contado (`diff_git`: `files` y quizá `patch`).
            archivos, parche = (diff.get('files'), diff.get('patch')) if isinstance(diff, dict) else \
                (archivos_del_diff(diff), diff)
            if archivos:
                salida.append(self._msg('diff_updated', {'run_id': raiz, 'files': archivos,
                                                         **({'patch': parche} if parche else {})}))
        resto = {k: campos[k] for k in ('turno', 'cierre', 'cache', 'resumen') if k in campos}
        salida += self._ext('closing', {'run_id': raiz, **resto}) if resto else []
        salida += self._terminar(raiz, resultado, str(campos.get('resumen') or ''))
        self.raiz = None
        return salida

    def _alfalfa(self, datos: dict) -> dict:
        return {'alfalfa': en_ingles(datos)} if datos and 'alfalfa' in self.extensiones else {}

    def _v2_turno_terminado(self, campos: dict) -> list[dict]:
        return self.cerrar_turno(campos)

    def cerrar_todo(self, campos: dict) -> list[dict]:
        """`fin` → se cierra lo que siga abierto (como interrumpido) y llega el `close` del agente."""
        salida: list[dict] = []
        if self.raiz is not None:
            salida += self.cerrar_turno({'estado': 'interrupted', 'resumen': campos.get('resumen', '')})
        extra = {k: campos[k] for k in ('estado', 'codigo_salida', 'tokens', 'coste') if k in campos}
        salida.append(self._msg('close', {'reason': str(campos.get('resumen') or campos.get('estado') or ''),
                                          **self._alfalfa(extra)}))
        return salida

    # -- mensajes (§4.3) ---------------------------------------------------
    def _mensaje(self, run_id: str, rol: str, texto: str) -> list[dict]:
        if not texto:
            return []
        inicio = self._msg('message_start', {'run_id': run_id, 'role': rol})
        salida = [inicio]
        for n in range(0, len(texto), TROZO):
            salida.append(self._msg('message_delta', {'message_id': inicio['id'],
                                                      'blocks': [{'type': 'text', 'text': texto[n:n + TROZO]}]}))
        salida.append(self._msg('message_end', {'message_id': inicio['id']}))
        return salida

    def _v2_evento(self, campos: dict) -> list[dict]:
        rol, texto = campos.get('rol'), campos.get('texto')
        texto = texto if isinstance(texto, str) else ''
        if rol == 'user':
            return []                  # el cliente ya lo mandó él: PACAI no lo devuelve
        if rol == 'tool':
            datos = campos.get('herramienta')
            if isinstance(datos, dict) and datos.get('fase') == 'inicio':
                return self._tool_start(self.raiz, texto, datos, campos.get('_argumentos'))
            if isinstance(datos, dict) and datos.get('fase') == 'fin':
                return self._tool_end(self.raiz, datos, texto)
            return []
        propio = {k: v for k, v in campos.items() if k not in ('version', '_argumentos')}
        if rol in ('agent', 'assistant') and self._activa(self.raiz):
            return self._mensaje(self.raiz, 'assistant', texto)
        if rol == 'system' and self._activa(self.raiz) and 'compactacion' not in campos and 'tarifa' not in campos:
            return self._mensaje(self.raiz, 'system', texto)
        return self._ext('notice', propio)

    # -- herramientas (§4.4) -----------------------------------------------
    def _tool_start(self, run_id: str | None, texto: str, datos: dict, argumentos: Any = None) -> list[dict]:
        """`argumentos`: los de la llamada del modelo, ya sin secretos (`Transport.argumentos_herramienta`). Sin
        ellos (o si no son un objeto JSON) se sacan, como antes, del texto del evento."""
        if not self._activa(run_id):
            return []
        nombre = str(datos.get('nombre') or 'tool')
        reales = _json_seguro(argumentos) if isinstance(argumentos, dict) else None
        recortados = False
        if reales is not None:
            args, recortados = recortar_argumentos(reales, self.max_argumentos)
        else:
            args = argumentos_de(texto, datos)
        payload: dict[str, Any] = {'run_id': run_id, 'name': nombre, 'arguments': args, 'side': 'agent'}
        # Lo legible va aparte (como `title` de ACP): la descripción que enseña la app o, sin ella, el texto de
        # siempre del motor.
        legible = datos.get('descripcion') or (texto.split(' · ', 1)[1] if ' · ' in texto else '')
        if legible:
            payload['title'] = str(legible)[:200]
        if recortados:
            payload.update(self._alfalfa({'arguments_truncated': True}))
        d = digest(args)
        aprobada = next((a for n, g, a in self.de_sesion if n == nombre and g == d), None)
        if aprobada:
            payload['approval_id'] = aprobada
        m = self._msg('tool_start', payload)
        self.herramientas[m['id']] = Herramienta(m['id'], run_id, nombre, args)
        return [m]

    def _tool_end(self, run_id: str | None, datos: dict, texto: str) -> list[dict]:
        nombre = str(datos.get('nombre') or '')
        tc = next((t for t, h in self.herramientas.items()
                   if h.run_id == run_id and h.nombre == nombre and h.lado == 'agent'), None)
        if tc is None:
            return []
        estado = 'error' if _fallo(datos) else 'success'
        cuerpo = texto.split('\n', 1)[1] if '\n' in texto else ''
        salida = [{'type': 'text', 'text': cuerpo[:MAX_SALIDA]}] if cuerpo else None
        resumen = None
        if estado == 'error' and cuerpo:
            try:
                error = json.loads(cuerpo).get('error')
            except (ValueError, AttributeError):
                error = None
            resumen = error if isinstance(error, str) else None
        if estado == 'success' and nombre in EDITORES:
            self.hay_cambios = True
        return self._cerrar(tc, estado, salida, resumen)

    # -- subagentes = subejecuciones (§4.9) --------------------------------
    def _subejecucion(self, ident: str, nombre: str = 'subagent', descripcion: str = '') -> list[dict]:
        """Abre la subejecución de un subagente si no tiene una activa."""
        if self._activa(self.subagentes.get(ident)) or not self._activa(self.raiz):
            return []
        agente = {'name': nombre or 'subagent', **({'description': descripcion[:200]} if descripcion else {})}
        m = self._msg('run_started', {'parent_run_id': self.raiz, 'agent': agente})
        self.ejecuciones[m['id']] = Ejecucion(m['id'], agente=ident)
        self.subagentes[ident] = m['id']
        return [m]

    def _v2_subagentes(self, campos: dict) -> list[dict]:
        salida: list[dict] = []
        filas = [f for f in campos.get('subagentes') or [] if isinstance(f, dict) and f.get('id')]
        for fila in filas:
            ident, estado = str(fila['id']), fila.get('estado')
            if estado not in TERMINADOS or self._activa(self.subagentes.get(ident)):
                salida += self._subejecucion(ident, str(fila.get('tipo') or ''), str(fila.get('nombre') or ''))
            run_id = self.subagentes.get(ident)
            if estado in TERMINADOS and self._activa(run_id):
                resultado = {'terminado': 'success', 'fallido': 'failed', 'interrumpido': 'interrupted',
                             'cerrado': 'interrupted'}[estado]
                salida += self._terminar(run_id, resultado, str(fila.get('error') or ''))
        return salida + self._ext('subagents', {'subagentes': filas})

    def _v2_subagente_evento(self, campos: dict) -> list[dict]:
        ident = str(campos.get('agente') or '')
        salida = self._subejecucion(ident)
        run_id = self.subagentes.get(ident)
        if not self._activa(run_id):
            return salida
        rol, texto = campos.get('rol'), str(campos.get('texto') or '')
        if rol == 'tool' and texto.startswith('Inicio '):
            nombre = texto[len('Inicio '):].split(' · ', 1)[0]
            return salida + self._tool_start(run_id, texto, {'nombre': nombre}, campos.get('_argumentos'))
        if rol == 'tool':
            return salida + self._tool_end(run_id, {'nombre': texto.split('\n', 1)[0]}, texto)
        if rol in ('agent', 'assistant', 'system'):
            return salida + self._mensaje(run_id, 'system' if rol == 'system' else 'assistant', texto)
        return salida

    # -- aprobaciones (§4.5) -----------------------------------------------
    def _herramienta_del_permiso(self, run_id: str, argv: Any) -> Herramienta | None:
        abiertas = [h for h in self.herramientas.values() if h.run_id == run_id and h.lado == 'agent']
        for h in reversed(abiertas):
            propio = h.argumentos.get('argv')
            if propio is None or propio == argv:
                return h
        return None

    def permiso(self, campos: dict) -> list[dict] | None:
        """`permiso` v2 → `approval_request`. None si no hay ejecución a la que atribuirlo."""
        proposito = str(campos.get('proposito') or '')
        sub = re.match(r'Subagente (\S+) «', proposito)
        run_id = self.subagentes.get(sub.group(1)) if sub else None
        if not self._activa(run_id):
            run_id = self.raiz
        if not self._activa(run_id):
            return None
        argv = campos.get('argv')
        salida: list[dict] = []
        herramienta = self._herramienta_del_permiso(run_id, argv)
        if herramienta is None:
            args = {'argv': argv, 'purpose': proposito[:500]}
            inicio = self._msg('tool_start', {'run_id': run_id, 'name': SINTETICA,
                                              'arguments': _json_seguro(args) or {}, 'side': 'agent',
                                              'title': proposito[:200] or 'Permiso'})
            herramienta = Herramienta(inicio['id'], run_id, SINTETICA, inicio['payload']['arguments'], sintetica=True)
            self.herramientas[inicio['id']] = herramienta
            salida.append(inicio)
        scopes = ['once'] + (['session'] if campos.get('para_sesion') or isinstance(campos.get('sitio'), dict) else [])
        peticion = self._msg('approval_request', {
            'run_id': run_id, 'tool_call_id': herramienta.id, 'name': herramienta.nombre,
            'arguments': herramienta.argumentos, 'digest': digest(herramienta.argumentos),
            'reason': self._razon(campos), 'scopes': scopes,
            'expires': en_segundos(self.reloj(), self.caducidad),
            **self._alfalfa({k: campos[k] for k in PERMISO_EXTRA if campos.get(k)})})
        self.aprobaciones[peticion['id']] = Aprobacion(
            peticion['id'], str(campos.get('id')), run_id, herramienta.id, herramienta.nombre,
            peticion['payload']['digest'], scopes, self._instante(peticion['payload']['expires']),
            sitio=isinstance(campos.get('sitio'), dict))
        self.permisos[str(campos.get('id'))] = peticion['id']
        return salida + [peticion]

    @staticmethod
    def _instante(texto: str) -> datetime:
        return datetime.fromisoformat(texto.replace('Z', '+00:00'))

    def _razon(self, campos: dict) -> str:
        motivo = campos.get('motivo') or next(iter(campos.get('motivos') or []), None)
        if campos.get('mcp'):
            return 'external_tool'
        if campos.get('sin_sandbox') and motivo not in RAZON_DE:
            return 'outside_sandbox'
        if motivo in RAZON_DE:
            return RAZON_DE[motivo]
        propia = RAZON_PROPIA.get(motivo, str(motivo or ''))
        if 'alfalfa' in self.extensiones and re.fullmatch(r'[a-z][a-z0-9_]*', propia):
            return 'alfalfa.' + propia
        return 'policy'

    def aprobacion(self, request_id: str) -> Aprobacion | None:
        return self.aprobaciones.get(request_id)

    def _v2_permiso_resuelto(self, campos: dict) -> list[dict]:
        """Lo resolvió el cliente, la política o el cierre: PACAI ya lo sabe o no lo cuenta (el `expired` es aparte)."""
        peticion = self.permisos.pop(str(campos.get('id')), None)
        a = self.aprobaciones.get(peticion) if peticion else None
        if a is None:
            return []
        if a.estado == 'pending':
            a.estado = 'approved' if campos.get('aprobado') else 'denied'
        if campos.get('aprobado') and (campos.get('sesion') or campos.get('sitio') == 'sesion'):
            self.de_sesion.append((a.nombre, a.digest, a.id))
        h = self.herramientas.get(a.tool_call_id)
        if h is not None and h.sintetica:
            return self._cerrar(h.id, 'success' if campos.get('aprobado') else 'error')
        return []

    def caducar(self, request_id: str) -> list[dict]:
        """El agente declara caducada una petición sin respuesta (§4.5). Nunca antes de `expires`."""
        a = self.aprobaciones.get(request_id)
        if a is None or a.estado != 'pending':
            return []
        a.estado = 'expired'
        salida = [self._msg('approval_result', {'decision': 'expired'}, reply_to=request_id)]
        self.permisos.pop(a.v2id, None)
        h = self.herramientas.get(a.tool_call_id)
        if h is not None and h.sintetica:
            salida += self._cerrar(h.id, 'cancelled')
        return salida

    # -- navegador de la app = herramienta del cliente (§4.4) --------------
    def _v2_navegador(self, campos: dict) -> list[dict]:
        ident, accion = str(campos.get('id')), campos.get('accion')
        if accion == 'cancelar_relevo':
            tc = next((t for t, h in self.herramientas.items() if h.v2id == ident), None)
            return self._ext('browser_cancel', {'tool_call_id': tc}) if tc else []
        if NAVEGADOR not in self.herramientas_cliente or not self._activa(self.raiz):
            if self.al_rechazar_navegador is not None:
                self.al_rechazar_navegador(ident, 'El cliente PACAI no declaró la herramienta «' + NAVEGADOR
                                           + '» o no hay una ejecución activa.')
            return []
        args = _json_seguro({k: v for k, v in campos.items() if k not in ('version', 'tipo', 'id')}) or {}
        m = self._msg('tool_start', {'run_id': self.raiz, 'name': NAVEGADOR, 'arguments': args, 'side': 'client',
                                     'title': 'Navegador: ' + str(accion)[:100]})
        self.herramientas[m['id']] = Herramienta(m['id'], self.raiz, NAVEGADOR, args, lado='client', v2id=ident)
        return [m]

    def fin_de_herramienta_cliente(self, tc: str) -> Herramienta | None:
        """El cliente cerró su herramienta: se olvida y se devuelve para contestar a v2."""
        h = self.herramientas.get(tc)
        if h is None or h.lado != 'client':
            return None
        return self.herramientas.pop(tc)

    # -- núcleo restante ---------------------------------------------------
    def _v2_plan(self, campos: dict) -> list[dict]:
        pasos = [{'text': str(p.get('paso') or ''), 'status': p.get('estado') if p.get('estado') in
                  ('pending', 'in_progress', 'completed') else 'pending'}
                 for p in campos.get('pasos') or [] if isinstance(p, dict)]
        extra = {'explanation': campos['explicacion']} if campos.get('explicacion') else {}
        if self.trabajo and self._activa(self.raiz):
            return [self._msg('plan_updated', {'run_id': self.raiz, 'steps': pasos, **self._alfalfa(extra)})]
        return self._ext('plan', {'steps': pasos, **extra})

    def _v2_contexto(self, campos: dict) -> list[dict]:
        payload: dict[str, Any] = {}
        if self._activa(self.raiz):
            payload['run_id'] = self.raiz
        if isinstance(campos.get('tokens'), int):
            payload['context_tokens'] = campos['tokens']
        if isinstance(campos.get('limite'), int) and campos['limite'] > 0:
            payload['context_limit'] = campos['limite']
        extra = {k: v for k, v in campos.items() if k not in ('version', 'tokens', 'limite')}
        salida = [self._msg('usage', payload)] if len(payload) > (1 if 'run_id' in payload else 0) else []
        return salida + (self._ext('context', {k: v for k, v in campos.items() if k != 'version'}) if extra else [])

    def _v2_uso_sesion(self, campos: dict) -> list[dict]:
        payload: dict[str, Any] = {}
        if isinstance(campos.get('coste'), (int, float)) and not isinstance(campos.get('coste'), bool):
            payload['cost'] = {'amount': campos['coste'], 'currency': 'USD'}
        if isinstance(campos.get('tokens'), int):
            payload.update(self._alfalfa({'total_tokens': campos['tokens']}))
        return [self._msg('usage', payload)] if payload else []

    def _v2_error(self, campos: dict) -> list[dict]:
        texto = str(campos.get('texto') or 'Error')
        extra = {k: v for k, v in campos.items() if k not in ('version', 'texto')}
        return [self._msg('error', {'code': CODIGOS_DE_ERROR.get(texto, 'alfalfa.engine_error'), 'message': texto,
                                    **self._alfalfa(extra)})]

    def error(self, codigo: str, mensaje: str, ref: str | None = None) -> dict:
        payload = {'code': codigo, 'message': mensaje[:500]}
        if ref:
            payload['ref'] = ref
        return self._msg('error', payload)

    def _v2_fin(self, campos: dict) -> list[dict]:
        return self.cerrar_todo(campos)
