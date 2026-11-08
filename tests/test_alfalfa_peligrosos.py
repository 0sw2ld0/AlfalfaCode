"""Clasificación de órdenes peligrosas de AlfalfaCode (permisos/peligrosos.py).

Los casos marcados «Codex» se adaptaron de los tests de
codex-rs/shell-command/src/command_safety/is_dangerous_command.rs (OpenAI Codex,
Apache-2.0; ver alfalfa/seatbelt/NOTICE y PROCEDENCIA.md). Los marcados
«diferencia» documentan dónde AlfalfaCode es más conservador a propósito.
"""
import pytest

from alfalfa.permisos.peligrosos import (FORCED_RM, MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH, OTHER, SUDO,
                                           dangerous_command_match, extract_shell_script,
                                           parse_literal_commands, rm_args_include_force_option)


# ---------------------------------------------------------------- Codex

def test_codex_rm_rf_and_rm_f_are_dangerous():
    assert dangerous_command_match(['rm', '-rf', '/']) == FORCED_RM
    assert dangerous_command_match(['rm', '-f', '/']) == FORCED_RM


@pytest.mark.parametrize('command', [
    ['/bin/rm', '-fr', '/tmp/example'],
    ['rm', '-r', '-f', '/tmp/example'],
    ['rm', '--force', '/tmp/example'],
    ['rm', '/tmp/example', '-f'],
    ['env', 'TARGET=/tmp/example', 'rm', '-rf', '/tmp/example'],
])
def test_codex_forced_rm_variants_are_dangerous(command):
    assert dangerous_command_match(command) == FORCED_RM


def test_codex_sudo_rm_rf_is_dangerous_as_sudo():
    # Codex devuelve ForcedRm; aquí cualquier sudo es SUDO (ver diferencias).
    assert dangerous_command_match(['sudo', 'rm', '-rf', '/tmp/example']) == SUDO


@pytest.mark.parametrize('depth,expected', [
    (MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH, FORCED_RM),
    (MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH + 1, OTHER),
])
def test_codex_deeply_nested_command_wrappers_fail_closed(depth, expected):
    assert dangerous_command_match(['env'] * depth + ['rm', '-rf', '/tmp/example']) == expected


@pytest.mark.parametrize('script', [
    'printf x | rm -rf /tmp/example',
    'if test -d /tmp/example; then rm --force /tmp/example; fi',
    'rm -rf "$TARGET" >/dev/null',
    'for target in /tmp/a /tmp/b; do rm -r -f "$target"; done',
    'echo "$(rm -rf /tmp/example)"',
    "bash -c 'rm -rf /tmp/example'",
    "trap 'rm -rf /tmp/example' EXIT",
    "for a in '-C5a25KeRr' '--' '--json' '--bogus'; do HOME=$(mktemp -d) MDE_URL=http://127.0.0.1:1 "
    "MDE_TOKEN=x node cli/mde.cjs ls \"$a\" >/tmp/mde-review-out 2>/tmp/mde-review-err; code=$?; "
    "printf '%s\\t%s\\t%s\\n' \"$a\" \"$code\" \"$(tr '\\n' ' ' </tmp/mde-review-err)\"; rm -rf \"$HOME\"; done",
])
def test_codex_forced_rm_in_complex_shell_syntax_is_dangerous(script):
    assert dangerous_command_match(['bash', '-lc', script]) == FORCED_RM


@pytest.mark.parametrize('command', [
    ['rm', '-r', '/tmp/example'],
    ['rm', '--', '-f'],
    ['bash', '-lc', "echo 'rm -rf /tmp/example'"],
    ['bash', '-lc', 'cmd=rm; $cmd -rf /tmp/example'],
    ['env', 'TARGET=/tmp/example', 'rm', '-r', '/tmp/example'],
    ['bash', '-lc', "trap 'echo rm -rf /tmp/example' EXIT"],
])
def test_codex_non_forced_or_non_literal_rm_is_not_dangerous(command):
    assert dangerous_command_match(command) is None


# ---------------------------------------------------------------- operandos tras --

@pytest.mark.parametrize('args,forced', [
    (['--', '-f'], False), (['--', '-rf', 'x'], False), (['x', '--', '--force'], False),
    (['-r', '--', '-f'], False), (['-f', '--', 'x'], True), (['-vf'], True), (['-rvf', 'x'], True),
    (['--force'], True), (['--forced'], False), (['--recursive', 'x'], False), (['-'], False),
])
def test_force_option_only_counts_before_double_dash(args, forced):
    assert rm_args_include_force_option(args) is forced


# ---------------------------------------------------------------- normales

@pytest.mark.parametrize('command', [
    ['ls', '-la'], ['git', 'status', '--short'], ['python3', '-m', 'pytest', '-q'], ['rm', 'archivo'],
    ['rm', '-r', 'carpeta'], ['grep', '-rf', 'patrones', '.'], ['echo', 'rm', '-rf', '/'],
    ['/usr/bin/git', 'diff'], ['bash', '-lc', 'ls -la | wc -l'], ['sh', '-c', 'cd src && make test'],
    ['bash', '-lc', 'ls 2>&1 >/dev/null'], ['env', 'A=1', 'ls'], ['env', '-i', 'PATH=/usr/bin', 'ls'],
])
def test_normal_commands_are_not_dangerous(command):
    assert dangerous_command_match(command) is None


# ---------------------------------------------------------------- sudo (CA-4)

@pytest.mark.parametrize('command', [
    ['sudo', 'ls'], ['/usr/bin/sudo', 'true'], ['sudo', '-n', 'whoami'], ['sudo'],
    ['env', 'sudo', 'ls'], ['env', 'A=1', '--', 'sudo', 'id'], ['bash', '-c', 'sudo ls'],
    ['sh', '-c', 'echo ok && sudo -v'], ['bash', '-lc', 'x=$(sudo cat /etc/hosts)'],
    ['zsh', '-c', "bash -c 'sudo ls'"], ['bash', '-lc', "trap 'sudo ls' EXIT"],
])
def test_every_sudo_invocation_asks_even_if_inner_command_is_harmless(command):
    assert dangerous_command_match(command) == SUDO


@pytest.mark.parametrize('command', [['echo', 'sudo'], ['bash', '-lc', "echo 'sudo ls'"],
                                     ['bash', '-lc', 'cmd=sudo; $cmd ls'], ['pseudo', 'ls']])
def test_sudo_as_data_is_not_an_invocation(command):
    assert dangerous_command_match(command) is None


# ---------------------------------------------------------------- envoltorios y límites

def test_nested_wrapper_combinations_and_their_limit():
    assert dangerous_command_match(['env', 'bash', '-c', 'env A=1 rm -fr x']) == FORCED_RM
    # El guion cuenta como un nivel más que sus envoltorios: 1 + 7 llega al límite, 1 + 8 lo pasa.
    limit = MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH
    assert dangerous_command_match(['sh', '-c', 'env ' * (limit - 1) + 'rm -rf x']) == FORCED_RM
    assert dangerous_command_match(['sh', '-c', 'env ' * limit + 'rm -rf x']) == OTHER
    # El escape `'\''` deja una comilla literal (Codex omite esa palabra): el guion
    # de la shell anidada se lee y sus órdenes se clasifican.
    assert dangerous_command_match(['sh', '-c', "sh -c 'echo '\\''x'\\''; ls'"]) is None
    assert dangerous_command_match(['sh', '-c', "sh -c 'echo '\\''x'\\''; rm -rf y'"]) == FORCED_RM
    assert dangerous_command_match(['bash', '-lc', 'bash -c "$CMD"']) == OTHER
    assert dangerous_command_match(['bash', '-lc', 'eval "$CMD"']) == OTHER
    assert dangerous_command_match(['eval', 'rm -rf x']) == FORCED_RM
    assert dangerous_command_match(['bash', '-lc', "eval 'ls -la'"]) is None
    assert dangerous_command_match(['bash', '-lc', 'echo "$HOME"']) is None
    # Con la misma profundidad en envoltorios inocuos no hay falso positivo.
    assert dangerous_command_match(['env'] * MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH + ['ls']) is None
    assert dangerous_command_match(['env'] * (MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH + 1) + ['ls']) == OTHER


@pytest.mark.parametrize('command,expected', [
    (['env', '-u', 'HOME', 'rm', '-rf', 'x'], FORCED_RM),
    (['env', '-uHOME', 'rm', '-rf', 'x'], FORCED_RM),
    (['env', '--unset=HOME', 'rm', '-f', 'x'], FORCED_RM),
    (['env', '-C', '/tmp', 'rm', '-rf', 'x'], FORCED_RM),
    (['env', '-S', 'rm -rf x'], FORCED_RM),
    (['env', '--split-string=sudo ls'], SUDO),
    (['env', '--weird', 'ls'], OTHER),
    (['env', '-i', '--', 'rm', '-rf', 'x'], FORCED_RM),
])
def test_env_options_never_hide_the_wrapped_command(command, expected):
    assert dangerous_command_match(command) == expected


@pytest.mark.parametrize('command,script', [
    (['bash', '-c', 'x'], 'x'), (['bash', '-lc', 'x'], 'x'), (['/bin/sh', '-c', 'x'], 'x'),
    (['sh', '-ec', 'x'], 'x'), (['bash', '--norc', '-c', 'x', 'arg0'], 'x'), (['zsh', '-o', 'errexit', '-c', 'x'], 'x'),
    (['bash', 'script.sh'], None), (['bash', '-c'], None), (['python3', '-c', 'x'], None), (['bash'], None),
])
def test_explicit_shell_invocations_are_recognized(command, script):
    assert extract_shell_script(command) == script


@pytest.mark.parametrize('script,expected', [
    ('ls; rm -rf x', FORCED_RM), ('true && rm -f x || echo no', FORCED_RM), ('(cd a; rm -rf b)', FORCED_RM),
    ('{ rm -rf x; }', FORCED_RM), ('cat <(rm -rf x)', FORCED_RM), ('echo `rm -rf x`', FORCED_RM),
    ('X=1 Y="2" rm -rf x', FORCED_RM), ('rm -rf *', FORCED_RM), ('# rm -rf x\nls', None),
    ("echo 'unbalanced", OTHER), ('cat <<EOF\nrm -rf x\nEOF', OTHER),
    ('echo "${X:-$(rm -rf y)}"', OTHER), ('echo "$(case a in a) rm -rf x;; esac)"', OTHER),
    ('case $x in a) rm -rf y;; esac', FORCED_RM),
])
def test_literal_commands_inside_explicit_shell(script, expected):
    assert dangerous_command_match(['bash', '-lc', script]) == expected


def test_literal_parser_keeps_dynamic_words_in_place_and_skips_assignments():
    commands = parse_literal_commands('A=1 B=$(x) ls -la *.py 2>/dev/null; "$cmd" -rf y; echo "hi $x"')
    assert commands == [['x'], ['ls', '-la', '*.py'], ['echo', 'hi \0']]
    assert [type(w).__name__ for w in commands[1]] == ['str', 'str', '_Dynamic']
    assert commands[1][2].splits and not commands[2][1].splits


# La cabecera `function NOMBRE` no se lleva la primera orden del cuerpo.
@pytest.mark.parametrize('script,expected', [
    ('function f { sudo ls; }; f', SUDO), ('function f { rm -rf basura; }; f', FORCED_RM),
    ('function f() { sudo ls; }; f', SUDO), ('function f\n{\nrm -rf basura\n}', FORCED_RM),
    ('f() { sudo ls; }; f', SUDO), ('function f { ls; }; f', None),
    ('for x in sudo; do echo "$x"; done', None), ('case sudo in a) ls;; esac', None),
])
def test_function_header_keeps_the_body(script, expected):
    assert dangerous_command_match(['bash', '-c', script]) == expected


# Las palabras dinámicas no desplazan las posiciones que interpreta env.
@pytest.mark.parametrize('script,expected', [
    ('name=HOME; env -u "$name" sudo ls', SUDO),
    ('name=HOME; env -u "$name" rm -rf basura', FORCED_RM),
    ('env -C "$PWD" rm -rf basura', FORCED_RM),
    ('env -u "$name" ls', None),
    ('env A="$x" ls', None), ('env A="$x" sudo ls', SUDO),
    ('env -u $name ls', OTHER),          # sin comillas: «A sudo» daría env -u A sudo ls
    ('env A=$x ls', OTHER), ('env $x ls', OTHER), ('env "$x" ls', OTHER),
    ('env "$opt" ls', OTHER), ('env -- "$cmd" -rf y', OTHER), ('env -S"$x"', OTHER),
    ('env sh -c "$x"', OTHER), ('env eval "$x"', OTHER), ('trap "$h" EXIT', OTHER),
    ('rm "$f" -rf x', FORCED_RM), ('rm -- "$f" -rf', None), ('rm "$f"', None), ('echo "$HOME"', None),
])
def test_dynamic_words_keep_their_position(script, expected):
    assert dangerous_command_match(['sh', '-c', script]) == expected


@pytest.mark.parametrize('script,expected', [
    ('\\rm -rf basura', FORCED_RM), ('\\sudo ls', SUDO), ('r\\m -rf basura', FORCED_RM),
    ('rm \\-rf basura', FORCED_RM), ('rm -- \\-rf', None), ('su\\\ndo ls', SUDO),
    ('echo "\\$(rm -rf x)"', None), ('"\\sudo" ls', None),
    # La continuación de línea no crea una palabra vacía delante de la orden.
    ('\\\n sudo ls', SUDO), ('\\\nsudo ls', SUDO), ('\\\n\trm -rf x', FORCED_RM),
    ('ls; \\\n sudo ls', SUDO), ('echo a \\\n | sudo tee f', SUDO),
    ('env \\\n sudo ls', SUDO), ('rm \\\n -rf x', FORCED_RM), ('ls \\', None),
])
def test_backslash_quotes_the_next_character(script, expected):
    assert dangerous_command_match(['bash', '-c', script]) == expected


@pytest.mark.parametrize('script,expected', [
    ('\\\n sudo ls', [['sudo', 'ls']]),
    ('ls \\\n  -l', [['ls', '-l']]),
    ('l\\\ns -l', [['ls', '-l']]),
    ('\\\n', []),
])
def test_line_continuation_does_not_add_empty_words(script, expected):
    assert parse_literal_commands(script) == expected


# ---------------------------------------------------------------- diferencias con Codex

def test_documented_differences_with_codex():
    # Codex: sólo la orden interior de sudo. Aquí, siempre sudo.
    assert dangerous_command_match(['sudo', 'ls']) == SUDO
    # Codex descarta guiones con errores de sintaxis; aquí se analizan igual.
    assert dangerous_command_match(['bash', '-lc', 'if then rm -rf /tmp/example']) == FORCED_RM
    # Codex exige [shell, -c, guion]; aquí también con opciones y posicionales.
    assert dangerous_command_match(['sh', '-ec', 'rm -rf x', 'sh']) == FORCED_RM
    # Codex toma `-u` por el nombre de la orden; aquí se salta su valor.
    assert dangerous_command_match(['env', '-u', 'X', 'rm', '-rf', 'y']) == FORCED_RM
    # Lo que no se sabe analizar pregunta en lugar de pasar.
    assert dangerous_command_match(['bash', '-c', 'cat <<EOF\nx\nEOF']) == OTHER


def test_malformed_argv_fails_closed():
    assert dangerous_command_match('rm -rf x') == OTHER
    assert dangerous_command_match(['rm', 3]) == OTHER
    assert dangerous_command_match([]) is None


def test_ported_modules_and_tests_keep_apache_attribution():
    from pathlib import Path
    server = Path(__file__).resolve().parents[1]
    procedencia = (server / 'alfalfa/seatbelt/PROCEDENCIA.md').read_text()
    assert (server / 'alfalfa/seatbelt/NOTICE').is_file() and (server / 'alfalfa/seatbelt/LICENSE').is_file()
    for path in ('alfalfa/permisos/peligrosos.py', 'alfalfa/permisos/denegacion.py',
                 'alfalfa/permisos/bajo_demanda.py', 'tests/test_alfalfa_peligrosos.py',
                 'tests/test_alfalfa_denegacion.py', 'tests/test_alfalfa_bajo_demanda.py'):
        head = (server / path).read_text()[:1500]
        assert 'Apache-2.0' in head and 'seatbelt/NOTICE' in head, path
        if path.startswith('alfalfa/'):
            assert path in procedencia, path
