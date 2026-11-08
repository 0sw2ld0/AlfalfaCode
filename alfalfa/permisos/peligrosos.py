"""Clasificación POSIX de órdenes peligrosas para la política on-request de AlfalfaCode.

Derivado de codex-rs/shell-command/src/command_safety/is_dangerous_command.rs
(OpenAI Codex, Apache-2.0; ver alfalfa/seatbelt/NOTICE, LICENSE y
PROCEDENCIA.md). Se conserva la estructura de dangerous_command_match: nombre
del ejecutable por su último componente, `rm` con fuerza antes de `--`,
envoltorios `env`, `sudo` y `trap`, límite de anidamiento de 8 y órdenes
literales dentro de `bash|sh|zsh -c`.

Diferencias intencionales con Codex (más conservadoras: sólo añaden preguntas):
- Toda invocación de `sudo` es peligrosa aunque la orden interior no lo sea;
  Codex sólo mira la orden interior.
- `env` reconoce además `-u NOMBRE`, `--unset`, `-0`, `-v`, `-C`/`--chdir` y
  `-S`/`--split-string`; cualquier otra opción se clasifica como peligrosa en
  lugar de tomarla por el nombre de la orden (Codex dejaba pasar
  `env -u X rm -rf y`).
- La shell se reconoce también con opciones agrupadas (`sh -ec`, `bash -xc`),
  opciones previas (`bash --norc -c`) y parámetros posicionales tras el guion;
  Codex exige exactamente `[shell, -c|-lc, guion]`.
- No hay tree-sitter: un analizador léxico propio extrae las órdenes
  literales. Lo que no sabe analizar (comillas sin cerrar, heredocs,
  `${…}` con sustituciones) se clasifica como peligroso en lugar de
  ignorarse, y un guion sintácticamente inválido (`if then rm -rf x`) se
  analiza igualmente; Codex descarta los guiones con errores.
- Una shell o `eval` dentro de un guion con argumentos dinámicos
  (`bash -c "$X"`, `env sh -c "$X"`) no se puede leer y se clasifica como
  peligrosa; `eval` con texto literal se analiza como un guion. Codex no
  trata `eval` y omite las palabras dinámicas.
- Las palabras dinámicas (`$X`, `$(…)`, globs) conservan su posición en la
  orden en lugar de omitirse: `env -u "$X" sudo ls` sigue viendo que `-u`
  consume `"$X"`. En los argumentos de `env`, una palabra dinámica sin
  comillas (puede partirse en varias o desaparecer) o una que pueda ser
  opción o nombre de la orden se clasifica como peligrosa; sólo pasa una
  asignación `NOMBRE="$X"`. También un `trap` con manejador dinámico.
- Una barra invertida fuera de comillas deja literal el carácter siguiente
  (`\\rm -rf x` es `rm -rf x`), y `function NOMBRE { … }` analiza el cuerpo;
  Codex trata ambas palabras como no literales y las omite.
- `security` (la orden del Llavero de macOS) siempre pregunta: con ella se
  leerían las claves de los proveedores que AlfalfaCode guarda allí.
- Sólo POSIX: no se portan las reglas de PowerShell/Windows ni el caso SIGSYS.
"""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

FORCED_RM = 'rm_forzado'
SUDO = 'sudo'
OTHER = 'otro'
MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH = 8

SHELLS = {'bash', 'sh', 'zsh'}
# Palabras reservadas que preceden a una orden simple dentro de un guion.
_PREFIX_WORDS = {'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', '{', '}', 'fi', 'done', 'esac', 'time'}
# Cabeceras cuyo resto, hasta el separador, son datos y no una orden.
_HEADER_WORDS = {'for', 'select', 'case', 'function', 'in'}
_ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\+?=')
# Como is_literal_word_or_number de Codex: lo que puede expandirse no es literal.
_EXPANDING = set('{}*?[]\\~^#$`')


class _Dynamic(str):
    """Palabra cuyo valor sólo se conoce al ejecutar; el texto guarda su parte literal.

    `splits` indica una expansión sin comillas, que puede partirse en varias
    palabras o desaparecer y mover así las posiciones que siguen.
    """

    splits = False


class _Unparseable(Exception):
    """El guion usa sintaxis que este analizador no sabe reducir a palabras literales."""


def executable_name_lookup_key(raw):
    name = raw.rsplit('/', 1)[-1]
    return name or None


def dangerous_command_match(command):
    """Regla peligrosa que cumple el argv (FORCED_RM, SUDO u OTHER), o None."""
    if not isinstance(command, (list, tuple)) or not all(isinstance(a, str) for a in command):
        return OTHER
    return _match_with_depth(list(command), 0)


def _match_with_depth(command, depth):
    if depth > MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH:
        return OTHER
    found = _match_for_exec(command, depth)
    if found:
        return found
    script = extract_shell_script(command)
    if script is None:
        return None
    if isinstance(script, _Dynamic):
        return OTHER
    try:
        commands = parse_literal_commands(script)
    except _Unparseable:
        return OTHER
    for literal in commands:
        if literal is None:
            return OTHER
        found = _match_with_depth(literal, depth + 1)
        if found:
            return found
    return None


def _match_for_exec(command, depth):
    cmd0 = executable_name_lookup_key(command[0]) if command else None
    if cmd0 == 'rm' and rm_args_include_force_option(command[1:]):
        return FORCED_RM
    if cmd0 == 'sudo':
        return SUDO
    if cmd0 == 'security':
        # Lee el Llavero, donde están las claves de los proveedores de AlfalfaCode.
        return OTHER
    if cmd0 == 'env':
        return _match_for_env(command, depth)
    if cmd0 == 'trap':
        return _match_for_trap(command, depth)
    if cmd0 == 'eval' and any(isinstance(a, _Dynamic) for a in command[1:]):
        return OTHER
    if cmd0 == 'eval' and len(command) > 1:
        return _match_with_depth(['sh', '-c', ' '.join(command[1:])], depth + 1)
    return None


def _match_for_env(command, depth):
    index = 1
    while index < len(command):
        argument = command[index]
        if isinstance(argument, _Dynamic):
            # Podría ser una opción, la orden o varias palabras: sólo una asignación con nombre literal es segura.
            if argument.splits or not _ASSIGNMENT.match(argument):
                return OTHER
            index += 1
            continue
        if argument == '--':
            index += 1
            break
        if argument in ('-i', '--ignore-environment', '-0', '--null', '-v', '--debug'):
            index += 1
            continue
        if argument in ('-u', '--unset', '-C', '--chdir'):
            value = command[index + 1] if index + 1 < len(command) else None
            if isinstance(value, _Dynamic) and value.splits:
                return OTHER
            index += 2
            continue
        if re.fullmatch(r'-u.+|--unset=.*|-C.+|--chdir=.*', argument):
            index += 1
            continue
        if argument in ('-S', '--split-string') or re.fullmatch(r'-S.+|--split-string=.*', argument):
            if argument in ('-S', '--split-string'):
                text, rest = (command[index + 1] if index + 1 < len(command) else ''), command[index + 2:]
            else:
                text = argument.split('=', 1)[1] if argument.startswith('--') else argument[2:]
                rest = command[index + 1:]
            try:
                words = shlex.split(text)
            except ValueError:
                return OTHER
            return _match_with_depth(['env'] + words + rest, depth + 1)
        name, sep, _ = argument.partition('=')
        if sep and name and not name.startswith('-'):
            index += 1
            continue
        if argument.startswith('-'):
            return OTHER
        break
    if index < len(command) and isinstance(command[index], _Dynamic):
        return OTHER
    return _match_with_depth(command[index:], depth + 1)


def _match_for_trap(command, depth):
    index = 1
    if index < len(command) and command[index] == '--':
        index += 1
    if index < len(command) and isinstance(command[index], _Dynamic):
        return OTHER
    if index >= len(command) or command[index].startswith('-'):
        return None
    return _match_with_depth(['sh', '-c', command[index]], depth + 1)


def rm_args_include_force_option(args):
    for arg in args:
        if arg == '--':
            return False
        if arg == '--force' or (arg.startswith('-') and not arg.startswith('--') and 'f' in arg[1:]):
            return True
    return False


def extract_shell_script(command):
    """Guion de `bash|sh|zsh … -c GUION …`, o None si no es una shell explícita con -c."""
    if not command:
        return None
    shell = PurePosixPath(command[0])
    if command[0] not in SHELLS and shell.stem not in SHELLS:
        return None
    index = 1
    while index < len(command):
        argument = command[index]
        if argument in ('-o', '+o', '-O', '+O'):
            index += 2
            continue
        if argument.startswith('--') and argument != '--':
            index += 1
            continue
        if re.fullmatch(r'[-+][A-Za-z]+', argument):
            if argument[0] == '-' and 'c' in argument[1:]:
                return command[index + 1] if index + 1 < len(command) else None
            index += 1
            continue
        return None
    return None


class _Word:
    __slots__ = ('text', 'literal', 'splits')

    def __init__(self):
        self.text, self.literal, self.splits = '', True, False

    def value(self):
        if self.literal:
            return self.text
        dynamic = _Dynamic(self.text)
        dynamic.splits = self.splits
        return dynamic


class _Parser:
    """Analizador léxico de guiones POSIX que sólo extrae órdenes literales.

    No prueba que nada sea seguro: sirve para encontrar órdenes peligrosas
    escritas literalmente, también dentro de $(…), `…`, <(…) y comillas dobles.
    """

    def __init__(self, source):
        self.src, self.pos, self.commands = source, 0, []
        # Sustituciones $(…)/<(…) abiertas: un `case` dentro cerraría la cuenta
        # de paréntesis con su patrón «a)».
        self.nesting = 0

    def peek(self, offset=0):
        index = self.pos + offset
        return self.src[index] if index < len(self.src) else ''

    def parse(self, closer=''):
        words, redirect, depth = [], False, 0
        word = None

        def finish_word():
            nonlocal word, redirect
            if word is not None:
                if redirect:
                    redirect = False
                else:
                    words.append(word)
            word = None

        def finish_command():
            finish_word()
            self._emit(words)
            words.clear()

        while True:
            ch = self.peek()
            if ch == '':
                if closer:
                    raise _Unparseable('Sustitución sin cerrar.')
                finish_command()
                return
            if closer and ch == closer and depth == 0:
                self.pos += 1
                finish_command()
                return
            if closer == ')' and ch in '()':
                depth += 1 if ch == '(' else -1
            if ch in ' \t':
                finish_word()
                self.pos += 1
            elif ch == '\n' or ch in ';|()' or (ch == '&' and self.peek(1) != '>'):
                finish_command()
                self.pos += 1
            elif ch == '#' and word is None:
                while self.peek() not in ('', '\n'):
                    self.pos += 1
            elif ch in '<>' or (ch == '&' and self.peek(1) == '>'):
                if ch in '<>' and self.peek(1) == '(':
                    # Sustitución de proceso: su interior es otro guion.
                    self.pos += 2
                    self._nested(')')
                    word = word or _Word()
                    word.literal, word.splits = False, True
                    continue
                if word is not None and word.literal and word.text.isdigit():
                    word = None  # descriptor del redireccionamiento (2>)
                finish_word()
                if self.src.startswith('<<', self.pos) and not self.src.startswith('<<<', self.pos):
                    raise _Unparseable('Heredoc.')
                while self.peek() in ('<', '>', '&', '|') and self.peek() != '':
                    self.pos += 1
                redirect = True
            elif ch == '\\' and self.peek(1) in ('\n', ''):
                # Continuación de línea: la shell la quita antes de separar palabras,
                # así que une la palabra en curso pero no empieza una vacía.
                self.pos += 2
            else:
                word = word or _Word()
                self._word_part(word)

    def _emit(self, words):
        index = 0
        while index < len(words):
            first = words[index]
            # «{» y «}» no son literales para _EXPANDING, pero aquí son palabras reservadas.
            if first.text == 'function' and first.literal:
                # `function NOMBRE { cuerpo; }`: la cabecera es sólo el nombre.
                index += 2
                continue
            if first.text in _HEADER_WORDS:
                if first.text == 'case' and self.nesting:
                    raise _Unparseable('case dentro de una sustitución.')
                return
            if first.text in _PREFIX_WORDS:
                index += 1
                continue
            prefix = first.text if first.literal else first.text.split('\0', 1)[0]
            if _ASSIGNMENT.match(prefix):
                index += 1
                continue
            break
        rest = words[index:]
        if not rest or not rest[0].literal:
            return
        # Las palabras dinámicas conservan su posición: omitirlas movería las que siguen.
        command = [w.value() for w in rest]
        if executable_name_lookup_key(rest[0].text) in SHELLS | {'eval'} and not all(w.literal for w in rest):
            # Una shell o eval con texto dinámico ejecuta algo que no se puede leer.
            command = None
        self.commands.append(command)

    def _word_part(self, word):
        ch = self.peek()
        if ch == "'":
            end = self.src.find("'", self.pos + 1)
            if end < 0:
                raise _Unparseable('Comilla simple sin cerrar.')
            word.text += self.src[self.pos + 1:end]
            self.pos = end + 1
        elif ch == '"':
            self.pos += 1
            while True:
                ch = self.peek()
                if ch == '':
                    raise _Unparseable('Comilla doble sin cerrar.')
                if ch == '"':
                    self.pos += 1
                    return
                if ch == '\\' and self.peek(1) in '$`"\\\n':
                    word.text += '' if self.peek(1) == '\n' else self.peek(1)
                    self.pos += 2
                elif ch in '$`':
                    self._expansion(word)
                else:
                    word.text += ch
                    self.pos += 1
        elif ch == '\\':
            # Fuera de comillas, la barra deja literal el carácter siguiente.
            word.text += self.peek(1)
            self.pos += 2
        elif ch in '$`':
            self._expansion(word)
            word.splits = True
        else:
            if ch in _EXPANDING or (ch == '=' and word.text == '' and word.literal):
                word.literal, word.splits = False, True
            word.text += ch
            self.pos += 1

    def _nested(self, closer):
        self.nesting += 1
        try:
            self.parse(closer)
        finally:
            self.nesting -= 1

    def _expansion(self, word):
        word.literal = False
        word.text += '\0'
        if self.peek() == '`':
            self.pos += 1
            self.parse('`')
        elif self.peek(1) == '(':
            self.pos += 2
            self._nested(')')
        elif self.peek(1) == '{':
            end = self.src.find('}', self.pos)
            if end < 0:
                raise _Unparseable('Expansión sin cerrar.')
            inner = self.src[self.pos + 2:end]
            if '$' in inner or '`' in inner or '{' in inner:
                raise _Unparseable('Expansión con sustituciones.')
            self.pos = end + 1
        else:
            self.pos += 1
            while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
                self.pos += 1


def parse_literal_commands(script):
    """Órdenes con nombre literal; sus argumentos dinámicos quedan como _Dynamic en su posición."""
    parser = _Parser(script)
    parser.parse()
    return parser.commands
