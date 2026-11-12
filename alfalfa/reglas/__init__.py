"""Reglas de órdenes al estilo de execpolicy de Codex para AlfalfaCode.

Traducción y adaptación de codex-rs/execpolicy (rule.rs, policy.rs y
parser.rs; OpenAI Codex, Apache-2.0; ver NOTICE, LICENSE y PROCEDENCIA.md de
esta carpeta), sin Starlark: las reglas viven en `$ALFALFA_HOME/reglas.toml`.

    [[regla]]
    patron = ["git", "reset", "--hard"]      # prefijo; un elemento lista = alternativas
    decision = "prohibida"                   # permitida | preguntar | prohibida
    justificacion = "Destructiva: usa git stash."
    coincide = [["git", "reset", "--hard"]]  # ejemplos que deben coincidir…
    no_coincide = ["git reset --keep"]       # …y que no (texto o lista; se comprueban al cargar)

También se aceptan los nombres de Codex: pattern, decision (allow, prompt,
forbidden), justification, match y not_match.

- Coincide una regla si la orden empieza por su patrón. Si coinciden varias,
  gana la más estricta: prohibida > preguntar > permitida.
- Una ruta absoluta (`/usr/bin/git`) también coincide con la regla de su nombre.
- `bash -lc 'a && b | c'` (y sh/zsh) se evalúa por partes si sólo tiene palabras
  literales unidas por &&, ||, ; o |. Con redirecciones, variables, subshells o
  comodines no es analizable: con reglas que preguntan o prohíben, se pregunta.
- Envoltorios sencillos (`env VAR=x`, `VAR=x` delante, `command`, `nohup`,
  `time`, `nice`, `sudo`) se miran también sin envolver.
"""
from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ARCHIVO = 'reglas.toml'
PERMITIDA, PREGUNTAR, PROHIBIDA = 'permitida', 'preguntar', 'prohibida'
SEVERIDAD = {PERMITIDA: 0, PREGUNTAR: 1, PROHIBIDA: 2}
SINONIMOS = {'allow': PERMITIDA, 'prompt': PREGUNTAR, 'forbidden': PROHIBIDA,
             PERMITIDA: PERMITIDA, PREGUNTAR: PREGUNTAR, PROHIBIDA: PROHIBIDA}
CAMPOS = {'patron': 'pattern', 'decision': 'decision', 'justificacion': 'justification',
          'coincide': 'match', 'no_coincide': 'not_match', 'fuera_del_sandbox': 'bypass_sandbox'}
SHELLS = {'bash', 'sh', 'zsh'}
OPCIONES_SHELL = {'-c', '-lc', '-cl'}
OPERADORES = ('&&', '||', ';', '|')
# Fuera de comillas, cualquiera de éstos hace la orden no analizable.
_ESPECIALES = set('&|;<>()$`*?[]{}~!#\n\r')
MAX_REGLAS = 500
MAX_ARCHIVO = 512 * 1024


class ReglasInvalidas(ValueError):
    pass


@dataclass(frozen=True)
class Regla:
    patron: tuple  # tuplas de alternativas
    decision: str
    justificacion: str | None = None
    origen: str = ''
    # Como `allow` de Codex: la orden corre fuera del sandbox sin preguntar. Sólo con
    # `fuera_del_sandbox = true`, que escribe «No volver a preguntar» en la tarjeta.
    sin_sandbox: bool = False

    def coincide(self, argv):
        if len(argv) < len(self.patron):
            return None
        primero = argv[0]
        base = primero.rsplit('/', 1)[-1] if primero.startswith('/') else primero
        if primero not in self.patron[0] and base not in self.patron[0]:
            return None
        for alternativas, token in zip(self.patron[1:], argv[1:]):
            if token not in alternativas:
                return None
        return list(argv[:len(self.patron)])

    def texto(self):
        partes = [a[0] if len(a) == 1 else '[' + '|'.join(a) + ']' for a in self.patron]
        return ' '.join(partes)


@dataclass
class Evaluacion:
    decision: str | None = None
    coincidencias: list = field(default_factory=list)  # [(regla, prefijo)]
    no_analizable: bool = False

    def regla(self):
        """La regla que decide (la primera de la severidad final)."""
        for regla, _ in self.coincidencias:
            if regla.decision == self.decision:
                return regla
        return None

    def como_dict(self):
        filas = [dict(prefijo=prefijo, decision=regla.decision, patron=regla.texto(),
                      **({'justificacion': regla.justificacion} if regla.justificacion else {}))
                 for regla, prefijo in self.coincidencias]
        datos = dict(coincidencias=filas)
        if self.decision:
            datos['decision'] = self.decision
        if self.no_analizable:
            datos['no_analizable'] = True
        return datos


def _palabras(script):
    """Partes de un script de shell sencillo, o None si no es analizable."""
    comillas, escape, actual, partes = None, False, '', []
    i = 0
    while i < len(script):
        c = script[i]
        if escape:
            actual += c
            escape = False
        elif comillas == "'":
            actual += c
            if c == "'":
                comillas = None
        elif comillas == '"':
            if c in '$`\\!':
                return None
            actual += c
            if c == '"':
                comillas = None
        elif c in '"\'':
            comillas = c
            actual += c
        elif c == '\\':
            escape = True
            actual += c
        else:
            operador = next((o for o in OPERADORES if script.startswith(o, i)), None)
            if operador:
                partes.append(actual)
                actual = ''
                i += len(operador)
                continue
            if c in _ESPECIALES:
                return None
            actual += c
        i += 1
    if comillas or escape:
        return None
    partes.append(actual)
    ordenes = []
    for parte in partes:
        try:
            argv = shlex.split(parte)
        except ValueError:
            return None
        if not argv:
            return None
        ordenes.append(argv)
    return ordenes


def _sin_envoltorio(argv):
    """`env A=1 git push` → `git push`; None si no hay envoltorio que quitar."""
    resto = list(argv)
    cambiado = False
    while resto:
        nombre = resto[0].rsplit('/', 1)[-1]
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', resto[0]):
            # `X=1 git push` en un script: la shell lo ejecuta como `env X=1 git push`.
            resto = resto[1:]
        elif nombre == 'env':
            resto = resto[1:]
            while resto and ('=' in resto[0] and not resto[0].startswith('=')):
                resto = resto[1:]
        elif nombre in ('command', 'nohup', 'time', 'sudo', 'nice') and len(resto) > 1 \
                and not resto[1].startswith('-'):
            resto = resto[1:]
        else:
            break
        cambiado = True
    return resto if cambiado and resto else None


def ordenes(argv):
    """(órdenes a evaluar, ¿no analizable?) para un argv de run_command."""
    lista = [list(argv)]
    nombre = argv[0].rsplit('/', 1)[-1] if argv else ''
    if nombre in SHELLS and len(argv) == 3 and argv[1] in OPCIONES_SHELL:
        partes = _palabras(argv[2])
        if partes is None:
            return lista, True
        lista += partes
    for orden in list(lista):
        desnudo = _sin_envoltorio(orden)
        if desnudo:
            lista.append(desnudo)
    return lista, False


def _segmentos(argv):
    """Las órdenes que de verdad se ejecutan: las de dentro de `bash -lc`, o argv tal cual.
    None si el script no es analizable."""
    nombre = argv[0].rsplit('/', 1)[-1] if argv else ''
    if nombre in SHELLS and len(argv) == 3 and argv[1] in OPCIONES_SHELL:
        return _palabras(argv[2])
    return [list(argv)] if argv else None


# Prefijos que no se guardan como «no volver a preguntar»: permitirían cualquier cosa.
# Los de Codex (BANNED_PREFIX_SUGGESTIONS en core/src/exec_policy.rs), comparados enteros.
PREFIJOS_PROHIBIDOS = {tuple(p) for p in (
    ['bash'], ['bash', '-c'], ['bash', '-lc'], ['/bin/bash'], ['/bin/bash', '-c'], ['/bin/bash', '-lc'],
    ['sh'], ['sh', '-c'], ['sh', '-lc'], ['/bin/sh'], ['/bin/sh', '-c'], ['/bin/sh', '-lc'],
    ['zsh'], ['zsh', '-c'], ['zsh', '-lc'], ['/bin/zsh'], ['/bin/zsh', '-c'], ['/bin/zsh', '-lc'],
    ['dash'], ['dash', '-c'], ['ksh'], ['ksh', '-c'], ['fish'], ['fish', '-c'],
    ['env'], ['git'], ['sudo'], ['rm'], ['osascript'], ['Rscript'],
    ['bun'], ['bun', '-e'], ['bun', 'run'], ['deno'], ['deno', 'eval'], ['julia'], ['julia', '-e'],
    ['lua'], ['lua', '-e'], ['node'], ['node', '-e'], ['nodejs'], ['nodejs', '-e'],
    ['npm', 'run'], ['pnpm', 'run'], ['yarn', 'run'], ['perl'], ['perl', '-e'], ['php'], ['php', '-r'],
    ['py'], ['py', '-3'], ['pypy'], ['pypy3'], ['python'], ['python', '-'], ['python', '-c'],
    ['python3'], ['python3', '-'], ['python3', '-c'], ['pythonw'], ['pyw'], ['ruby'], ['ruby', '-e'],
    ['swift'], ['xcrun'], ['open'], ['xargs'], ['find'], ['curl'], ['wget'])}
MAX_PREFIJO = 6


def prefijo_para_siempre(propuesto, argv):
    """El prefijo que la tarjeta puede ofrecer como «no volver a preguntar», o None.

    Como `derive_requested_execpolicy_amendment_from_prefix_rule` de Codex: el que propone el
    modelo (`prefix_rule`), si no está prohibido y cubre cada parte de la orden; si no propone
    ninguno, para git se ofrece la suborden (`git commit`), nunca `git` a secas."""
    partes = _segmentos(argv)
    if not partes:
        return None
    if propuesto is None and len(partes) == 1 and len(partes[0]) >= 2:
        orden = partes[0]
        nombre = orden[0].rsplit('/', 1)[-1]
        if nombre == 'git' and not orden[1].startswith('-'):
            propuesto = ['git', orden[1]]
    if (not isinstance(propuesto, list) or not 1 <= len(propuesto) <= MAX_PREFIJO
            or not all(isinstance(t, str) and t and t.strip() == t and not set(t) & _ESPECIALES for t in propuesto)):
        return None
    if tuple(propuesto) in PREFIJOS_PROHIBIDOS:
        return None
    regla = Regla(tuple((t,) for t in propuesto), PERMITIDA)
    for parte in partes:
        candidatas = [parte] + ([d] if (d := _sin_envoltorio(parte)) else [])
        if not any(regla.coincide(c) is not None for c in candidatas):
            return None
    return list(propuesto)


def guardar_para_siempre(home, prefijo, justificacion):
    """Añade a `$ALFALFA_HOME/reglas.toml` la regla de «No volver a preguntar», como
    `append_amendment_and_update` de Codex con `~/.codex/rules/default.rules`.

    Comprueba antes que el archivo resultante se lee bien: si el actual es inválido o la
    regla lo rompería, no escribe nada (ReglasInvalidas). Devuelve las reglas nuevas."""
    import json
    import os
    import tempfile
    ruta = Path(home) / ARCHIVO
    if ruta.is_symlink() or (ruta.exists() and not ruta.is_file()):
        raise ReglasInvalidas(ARCHIVO + ' debe ser un archivo regular, sin enlaces.')
    actual = ruta.read_text(encoding='utf-8') if ruta.exists() else ''
    bloque = ('\n[[regla]]\npatron = ' + json.dumps(list(prefijo), ensure_ascii=False)
              + '\ndecision = "permitida"\nfuera_del_sandbox = true\njustificacion = '
              + json.dumps(justificacion, ensure_ascii=False) + '\n')
    nuevo = actual + ('' if not actual or actual.endswith('\n') else '\n') + bloque
    if len(nuevo.encode('utf-8')) > MAX_ARCHIVO:
        raise ReglasInvalidas(ARCHIVO + ' superaría los 512 KB.')
    try:
        reglas = leer(tomllib.loads(nuevo))
    except tomllib.TOMLDecodeError as exc:
        raise ReglasInvalidas(ARCHIVO + ' no es TOML válido: ' + str(exc)[:200]) from None
    Path(home).mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=str(Path(home)), prefix='.reglas-', suffix='.toml')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as archivo:
            archivo.write(nuevo)
        os.chmod(temporal, 0o600)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return reglas


class Reglas:
    def __init__(self, reglas=()):
        self.reglas = list(reglas)

    def __bool__(self):
        return bool(self.reglas)

    def estrictas(self):
        return any(r.decision != PERMITIDA for r in self.reglas)

    def evaluar_una(self, argv):
        return [(r, p) for r in self.reglas if (p := r.coincide(argv)) is not None]

    def fuera_del_sandbox(self, argv):
        """¿Corre `argv` fuera del sandbox sin preguntar? Como `bypass_sandbox` de Codex
        (core/src/exec_policy.rs): sólo si ninguna regla es más estricta que `permitida`
        y cada parte de la orden la permite una regla con `fuera_del_sandbox`."""
        if self.evaluar(argv).decision != PERMITIDA:
            return False
        partes = _segmentos(argv)
        if not partes:
            return False
        permitidas = [r for r in self.reglas if r.decision == PERMITIDA and r.sin_sandbox]
        for parte in partes:
            candidatas = [parte] + ([d] if (d := _sin_envoltorio(parte)) else [])
            if not any(r.coincide(c) is not None for r in permitidas for c in candidatas):
                return False
        return True

    def evaluar(self, argv):
        """Evaluacion de una orden: la más estricta de todas sus partes."""
        evaluacion = Evaluacion()
        lista, opaca = ordenes(argv)
        for orden in lista:
            evaluacion.coincidencias += self.evaluar_una(orden)
        if opaca and self.estrictas():
            evaluacion.no_analizable = True
        decisiones = [r.decision for r, _ in evaluacion.coincidencias]
        if evaluacion.no_analizable:
            decisiones.append(PREGUNTAR)
        if decisiones:
            evaluacion.decision = max(decisiones, key=SEVERIDAD.get)
        return evaluacion


def _patron(valor, donde):
    if not isinstance(valor, list) or not valor:
        raise ReglasInvalidas(donde + ': patron debe ser una lista no vacía.')
    patron = []
    for elemento in valor:
        alternativas = elemento if isinstance(elemento, list) else [elemento]
        if not alternativas or not all(isinstance(a, str) and a for a in alternativas):
            raise ReglasInvalidas(donde + ': cada elemento de patron es texto o una lista de textos.')
        patron.append(tuple(alternativas))
    return tuple(patron)


def _ejemplo(valor, donde):
    if isinstance(valor, str):
        try:
            argv = shlex.split(valor)
        except ValueError as exc:
            raise ReglasInvalidas(donde + ': ejemplo con comillas sin cerrar: ' + str(exc)) from None
    elif isinstance(valor, list) and all(isinstance(a, str) for a in valor):
        argv = list(valor)
    else:
        raise ReglasInvalidas(donde + ': cada ejemplo es texto o una lista de textos.')
    if not argv:
        raise ReglasInvalidas(donde + ': ejemplo vacío.')
    return argv


def leer(datos, origen=ARCHIVO):
    """Reglas validadas; ReglasInvalidas si alguna o algún ejemplo no cumple."""
    if not isinstance(datos, dict):
        raise ReglasInvalidas(origen + ': se esperaba una tabla.')
    sobran = set(datos) - {'regla', 'prefix_rule'}
    if sobran:
        raise ReglasInvalidas(origen + ': claves desconocidas: ' + ', '.join(sorted(sobran)) + '.')
    filas = list(datos.get('regla', [])) + list(datos.get('prefix_rule', []))
    if len(filas) > MAX_REGLAS:
        raise ReglasInvalidas(origen + ': más de ' + str(MAX_REGLAS) + ' reglas.')
    reglas = []
    for numero, fila in enumerate(filas, 1):
        donde = origen + ', regla ' + str(numero)
        if not isinstance(fila, dict):
            raise ReglasInvalidas(donde + ': cada regla es una tabla [[regla]].')
        normal = {}
        for clave, valor in fila.items():
            propia = CAMPOS.get(clave, clave if clave in CAMPOS.values() else None)
            if propia is None:
                raise ReglasInvalidas(donde + ': campo desconocido ' + str(clave) + '.')
            if propia in normal:
                raise ReglasInvalidas(donde + ': ' + propia + ' repetido con otro nombre.')
            normal[propia] = valor
        if 'pattern' not in normal:
            raise ReglasInvalidas(donde + ': falta patron.')
        decision = SINONIMOS.get(normal.get('decision', PERMITIDA))
        if decision is None:
            raise ReglasInvalidas(donde + ': decision debe ser permitida, preguntar o prohibida.')
        justificacion = normal.get('justification')
        if justificacion is not None and (not isinstance(justificacion, str) or not justificacion.strip()):
            raise ReglasInvalidas(donde + ': justificacion debe ser texto no vacío.')
        sin_sandbox = normal.get('bypass_sandbox', False)
        if not isinstance(sin_sandbox, bool):
            raise ReglasInvalidas(donde + ': fuera_del_sandbox debe ser true o false.')
        if sin_sandbox and decision != PERMITIDA:
            raise ReglasInvalidas(donde + ': fuera_del_sandbox sólo vale con decision = "permitida".')
        regla = Regla(_patron(normal['pattern'], donde), decision,
                      justificacion.strip() if justificacion else None, donde, sin_sandbox)
        # Como execpolicy: los ejemplos son pruebas que se pasan al cargar.
        for ejemplo in normal.get('match', []) or []:
            argv = _ejemplo(ejemplo, donde)
            if regla.coincide(argv) is None:
                raise ReglasInvalidas(donde + ': el ejemplo «' + shlex.join(argv)
                                      + '» debería coincidir y no coincide.')
        for ejemplo in normal.get('not_match', []) or []:
            argv = _ejemplo(ejemplo, donde)
            if regla.coincide(argv) is not None:
                raise ReglasInvalidas(donde + ': el ejemplo «' + shlex.join(argv)
                                      + '» no debería coincidir y coincide.')
        reglas.append(regla)
    return Reglas(reglas)


def desde_hogar(home):
    """Las reglas de `$ALFALFA_HOME/reglas.toml`; Reglas() si no hay archivo."""
    ruta = Path(home) / ARCHIVO
    if not ruta.exists() and not ruta.is_symlink():
        return Reglas()
    if ruta.is_symlink() or not ruta.is_file() or ruta.stat().st_size > MAX_ARCHIVO:
        raise ReglasInvalidas(ARCHIVO + ' debe ser un archivo regular de hasta 512 KB, sin enlaces.')
    try:
        datos = tomllib.loads(ruta.read_text(encoding='utf-8'))
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise ReglasInvalidas(ARCHIVO + ' no es TOML válido: ' + str(exc)[:200]) from None
    return leer(datos)
