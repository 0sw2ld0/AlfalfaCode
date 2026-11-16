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
- Las reglas que preguntan o prohíben miran todas las formas de la orden: la ruta
  absoluta (`/usr/bin/git`) por su nombre, sin envoltorios (`env VAR=x`, `VAR=x`
  delante, `command`, `nohup`, `time`, `nice`, `sudo`), dentro de cualquier shell
  (`bash -ec '…'`, `dash -c '…' _`, `env bash -lc '…'`), sin las opciones globales
  de git (`git -C . reset`) y con opciones largas abreviadas (`--har` es `--hard`).
- Una regla `permitida` sólo casa la orden tal cual: el programa por su nombre
  (`git`, el del PATH) o por una ruta absoluta de las carpetas del sistema, sin
  envoltorios delante, y como mucho dentro de `bash|sh|zsh -c|-lc|-cl '…'`. Con
  `env PATH=…`, `sudo` o `tools/git`, la orden ya no es la que se permitió.
- `bash -lc 'a && b | c'` se evalúa por partes si sólo tiene palabras literales
  unidas por &&, ||, ; o |. Con redirecciones, variables, subshells o comodines
  no es analizable: con reglas que preguntan o prohíben, se pregunta.
- Lo que concede una regla `permitida` (lo escribe «No volver a preguntar» en la tarjeta):
  sin más, quita la pregunta del modo Manual y la de orden peligrosa dentro del sandbox;
  con `con_red = true`, además deja usar la red dentro del sandbox sin preguntar; con
  `fuera_del_sandbox = true`, la orden corre fuera del sandbox (y con red) sin preguntar.
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
          'coincide': 'match', 'no_coincide': 'not_match', 'fuera_del_sandbox': 'bypass_sandbox',
          'con_red': 'with_network'}
SHELLS = {'bash', 'sh', 'zsh'}
OPCIONES_SHELL = {'-c', '-lc', '-cl'}
# Shells cuyo guion se mira para las reglas que preguntan o prohíben, con cualquier forma de -c.
TODAS_LAS_SHELLS = SHELLS | {'dash', 'ksh', 'mksh', 'yash'}
# Donde vive el programa del sistema: una regla `permitida` sólo casa una ruta absoluta de aquí.
CARPETAS_DEL_SISTEMA = ('/usr/bin', '/bin', '/usr/sbin', '/sbin', '/usr/local/bin', '/opt/homebrew/bin')
# Opciones de git antes de la suborden que llevan su valor en el argumento siguiente.
GIT_CON_VALOR = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix', '--config-env'}
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
    # Con `con_red = true` (la tarjeta de red): la orden usa la red dentro del sandbox sin
    # preguntar. No la saca del sandbox ni quita ninguna otra pregunta.
    con_red: bool = False

    def coincide(self, argv):
        """El prefijo de `argv` que casa, o None. Una regla `permitida` casa la orden tal cual
        (ver la cabecera); las que preguntan o prohíben, también abreviada o por la ruta."""
        if len(argv) < len(self.patron):
            return None
        estricta = self.decision == PERMITIDA
        primero = argv[0]
        if '/' in primero:
            carpeta, _, base = primero.rpartition('/')
            if not primero.startswith('/') or (estricta and carpeta not in CARPETAS_DEL_SISTEMA):
                return None
        else:
            base = primero
        if base not in self.patron[0]:
            return None
        for alternativas, token in zip(self.patron[1:], argv[1:]):
            if token not in alternativas and (estricta or not _abreviada(token, alternativas)):
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


def _abreviada(token, alternativas):
    """`--har` por `--hard`: git, como getopt_long, acepta el principio de una opción larga."""
    nombre = token.split('=', 1)[0]
    return (nombre.startswith('--') and len(nombre) > 2
            and any(a.startswith('--') and a.split('=', 1)[0].startswith(nombre) for a in alternativas))


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


def _sin_opciones_de_git(argv):
    """`git -C . -c x=y reset --hard` → `git reset --hard`; None si no hay nada que quitar."""
    if not argv or argv[0].rsplit('/', 1)[-1] != 'git':
        return None
    i = 1
    while i < len(argv) and argv[i].startswith('-') and argv[i] != '--':
        i += 2 if argv[i] in GIT_CON_VALOR else 1
    return [argv[0]] + list(argv[i:]) if i > 1 and i < len(argv) else None


def _guion(argv):
    """El guion de una shell con -c en cualquier forma (`-ec`, `--norc -c`, posicionales detrás)."""
    if not argv or argv[0].rsplit('/', 1)[-1] not in TODAS_LAS_SHELLS:
        return None
    from ..permisos.peligrosos import extract_shell_script
    return extract_shell_script(['sh'] + list(argv[1:]))


def _es_shell_simple(argv):
    nombre = argv[0].rsplit('/', 1)[-1] if argv else ''
    return nombre in SHELLS and len(argv) == 3 and argv[1] in OPCIONES_SHELL


def ordenes(argv):
    """(órdenes a evaluar, ¿no analizable?) para un argv de run_command.

    Cada orden va con un indicador: True si es la orden tal cual (o una parte de
    `bash -lc` simple) y la puede decidir una regla `permitida`; False si es otra forma de
    leerla (sin envoltorio, sin opciones de git, dentro de otra shell), que sólo cuenta para
    las reglas que preguntan o prohíben."""
    lista, opaca, i = [(list(argv), True)], False, 0
    while i < len(lista) and len(lista) < 64:
        orden, literal = lista[i]
        i += 1
        guion = _guion(orden)
        if guion is not None:
            partes = _palabras(guion) if isinstance(guion, str) else None
            if partes is None:
                opaca = True
            else:
                tal_cual = literal and _es_shell_simple(orden)
                lista += [(parte, tal_cual) for parte in partes]
        for otra in (_sin_envoltorio(orden), _sin_opciones_de_git(orden)):
            if otra and (otra, False) not in lista and (otra, True) not in lista:
                lista.append((otra, False))
    return lista, opaca


def _sin_cambios_de_carpeta(partes):
    """Las partes sin los `cd <carpeta>` sueltos, que no ejecutan nada: `bash -lc 'cd App && swift build'`
    lo cubre la regla «swift build». Antes `cd App` no lo cubría ninguna y se preguntaba siempre. Si no
    queda ninguna otra orden, nada (un `cd` solo no lo decide una regla)."""
    if not partes:
        return partes
    otras = [p for p in partes if not (len(p) == 2 and p[0] == 'cd' and not p[1].startswith('-'))]
    return otras or None


def _segmentos(argv):
    """Las órdenes que de verdad se ejecutan: las de dentro de `bash -lc`, o argv tal cual.
    None si el script no es analizable."""
    nombre = argv[0].rsplit('/', 1)[-1] if argv else ''
    if nombre in SHELLS and len(argv) == 3 and argv[1] in OPCIONES_SHELL:
        return _palabras(argv[2])
    return [list(argv)] if argv else None


# Programas que nunca empiezan un «no volver a preguntar», sigan las palabras que sigan: con
# cualquier prefijo suyo (`python3 -B`, `env -i`, `find .`, `rm -r`) se puede ejecutar o borrar
# cualquier cosa fuera del sandbox. Se comparan por nombre, sin la ruta.
PROGRAMAS_PROHIBIDOS = re.compile(
    r'(?:ba|z|da|k|mk|ya|c|tc|fi)?sh|env|sudo|doas|su|nice|nohup|command|time|timeout|xargs|find|rm|'
    r'exec|eval|python[0-9.]*|pythonw|pyw?|pypy[0-9.]*|node(?:js)?|bun|bunx|deno|npx|uvx|pipx|perl[0-9.]*|'
    r'ruby|php|lua(?:jit)?|julia|Rscript|osascript|swift|xcrun|open|curl|wget|awk|gawk|nawk|ssh|scp')
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
    ['swift'], ['xcrun'], ['open'], ['xargs'], ['find'], ['curl'], ['wget'],
    # Como `npx` y `uvx`: bajan un paquete cualquiera y lo ejecutan.
    ['npm', 'exec'], ['npm', 'x'], ['pnpm', 'exec'], ['pnpm', 'dlx'], ['yarn', 'exec'], ['yarn', 'dlx'],
    ['uv', 'tool'], ['uv', 'run'])}
MAX_PREFIJO = 6
# Programas cuya segunda palabra es una suborden (`npm install`, `make test`): en las tarjetas de
# dentro del sandbox (modo y red), si el modelo no propone prefijo, se ofrece programa + suborden,
# como ya se hacía con git. Fuera del sandbox sólo git, como antes.
CON_SUBORDEN = frozenset({'npm', 'pnpm', 'yarn', 'pip', 'pip3', 'uv', 'cargo', 'go', 'make', 'gem', 'bundle',
                          'poetry', 'composer', 'mvn', 'gradle', 'dotnet', 'mix'})
_SUBORDEN = re.compile(r'[a-z][a-z0-9-]*')


def prefijo_para_siempre(propuesto, argv, dentro_del_sandbox=False):
    """El prefijo que la tarjeta puede ofrecer como «no volver a preguntar», o None.

    Como `derive_requested_execpolicy_amendment_from_prefix_rule` de Codex: el que propone el
    modelo (`prefix_rule`), si no está prohibido y cubre cada parte de la orden; si no propone
    ninguno, para git se ofrece la suborden (`git commit`), nunca `git` a secas. En una tarjeta
    de dentro del sandbox (`dentro_del_sandbox`: la del modo o la de red), también la de los
    programas de CON_SUBORDEN (`npm install`, `make test`)."""
    partes = _sin_cambios_de_carpeta(_segmentos(argv))
    if not partes:
        return None
    if propuesto is None and len(partes) == 1 and len(partes[0]) >= 2:
        orden = partes[0]
        nombre = orden[0].rsplit('/', 1)[-1]
        if nombre == 'git' and not orden[1].startswith('-'):
            propuesto = ['git', orden[1]]
        elif dentro_del_sandbox and orden[0] in CON_SUBORDEN and _SUBORDEN.fullmatch(orden[1]):
            propuesto = [orden[0], orden[1]]
    if (not isinstance(propuesto, list) or not 1 <= len(propuesto) <= MAX_PREFIJO
            or not all(isinstance(t, str) and t and t.strip() == t and not set(t) & _ESPECIALES for t in propuesto)):
        return None
    if tuple(propuesto) in PREFIJOS_PROHIBIDOS or PROGRAMAS_PROHIBIDOS.fullmatch(propuesto[0].rsplit('/', 1)[-1]):
        return None
    if len(propuesto) > 1 and propuesto[-1].startswith('-'):
        # `git -c`, `make -f`: la palabra siguiente, que puede ser cualquiera, decide qué se ejecuta.
        return None
    regla = Regla(tuple((t,) for t in propuesto), PERMITIDA)
    if not all(regla.coincide(parte) is not None for parte in partes):
        return None
    return list(propuesto)


def guardar_para_siempre(home, prefijo, justificacion, dentro_del_sandbox=False, con_red=False):
    """Añade a `$ALFALFA_HOME/reglas.toml` la regla de «No volver a preguntar», como
    `append_amendment_and_update` de Codex con `~/.codex/rules/default.rules`.

    Comprueba antes que el archivo resultante se lee bien: si el actual es inválido o la
    regla lo rompería, no escribe nada (ReglasInvalidas). Devuelve las reglas nuevas.
    `dentro_del_sandbox`: la regla sólo quita la pregunta del modo Manual; la orden sigue
    corriendo en el sandbox, sin red. Con `con_red` también, pero con la red abierta."""
    if con_red and not dentro_del_sandbox:
        raise ReglasInvalidas('con_red sólo vale para una regla de dentro del sandbox.')
    import json
    import os
    import tempfile
    ruta = Path(home) / ARCHIVO
    if ruta.is_symlink() or (ruta.exists() and not ruta.is_file()):
        raise ReglasInvalidas(ARCHIVO + ' debe ser un archivo regular, sin enlaces.')
    actual = ruta.read_text(encoding='utf-8') if ruta.exists() else ''
    bloque = ('\n[[regla]]\npatron = ' + json.dumps(list(prefijo), ensure_ascii=False)
              + '\ndecision = "permitida"\n' + ('' if dentro_del_sandbox else 'fuera_del_sandbox = true\n')
              + ('con_red = true\n' if con_red else '')
              + 'justificacion = '
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
        return self.cubre_toda(argv, sin_sandbox=True)

    def con_red(self, argv):
        """¿Usa `argv` la red dentro del sandbox sin preguntar? Sólo si nada es más estricto que
        `permitida` y cada parte la permite una regla con `con_red` (o con `fuera_del_sandbox`,
        que ya lleva la red)."""
        return self.cubre_toda(argv, con_red=True)

    def cubre_toda(self, argv, sin_sandbox=False, con_red=False):
        """¿Permiten reglas `permitida` (con `fuera_del_sandbox` o `con_red` si se pide) cada parte
        de la orden tal cual, sin ninguna regla más estricta? Una que sólo cubre un trozo
        (`git status; rm -rf src`) no decide por la línea entera."""
        if self.evaluar(argv).decision != PERMITIDA:
            return False
        partes = _sin_cambios_de_carpeta(_segmentos(argv))
        if not partes:
            return False
        permitidas = [r for r in self.reglas if r.decision == PERMITIDA and (r.sin_sandbox or not sin_sandbox)
                      and (r.con_red or r.sin_sandbox or not con_red)]
        return all(any(r.coincide(parte) is not None for r in permitidas) for parte in partes)

    def evaluar(self, argv):
        """Evaluacion de una orden: la más estricta de todas sus partes."""
        evaluacion = Evaluacion()
        lista, opaca = ordenes(argv)
        for orden, tal_cual in lista:
            evaluacion.coincidencias += [(r, p) for r, p in self.evaluar_una(orden)
                                         if tal_cual or r.decision != PERMITIDA]
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
        con_red = normal.get('with_network', False)
        if not isinstance(con_red, bool):
            raise ReglasInvalidas(donde + ': con_red debe ser true o false.')
        if con_red and decision != PERMITIDA:
            raise ReglasInvalidas(donde + ': con_red sólo vale con decision = "permitida".')
        if con_red and sin_sandbox:
            raise ReglasInvalidas(donde + ': con_red sobra con fuera_del_sandbox, que ya lleva la red.')
        regla = Regla(_patron(normal['pattern'], donde), decision,
                      justificacion.strip() if justificacion else None, donde, sin_sandbox, con_red)
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
