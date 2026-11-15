"""Heurística de denegación probable del sandbox (permisos/denegacion.py).

Adaptado de codex-rs/sandboxing/src/denial.rs (OpenAI Codex, Apache-2.0; ver
alfalfa/seatbelt/NOTICE y PROCEDENCIA.md).
"""
import os
import subprocess
import sys

import pytest

from alfalfa.permisos.denegacion import (
    QUICK_REJECT_EXIT_CODES,
    SANDBOX_DENIED_KEYWORDS,
    is_likely_executor_managed_sandbox_denied,
    is_likely_sandbox_denied,
)


def test_keywords_and_quick_reject_codes_match_codex():
    # Las de Codex, en su orden, sin «sandbox» suelta ni «permission denied» (EACCES no lo da
    # Seatbelt), más las de Launch Services de macOS (diferencias documentadas).
    assert SANDBOX_DENIED_KEYWORDS[:5] == ('operation not permitted', 'read-only file system',
                                           'seccomp', 'landlock', 'failed to write file')
    assert SANDBOX_DENIED_KEYWORDS[5:] == ('unable to find application named', 'no application knows how to open',
                                           'lsopenurlswithrole', 'klsnoexecutableerr',
                                           'cannot be opened for an unexpected reason')
    assert QUICK_REJECT_EXIT_CODES == (2, 126, 127)


def test_success_is_never_a_denial_even_with_keywords():
    assert not is_likely_sandbox_denied('seatbelt', 0, stderr='Operation not permitted')
    assert not is_likely_executor_managed_sandbox_denied(0, stdout='sandbox')


@pytest.mark.parametrize('sandbox', [None, 'none'])
def test_unsandboxed_execution_is_never_a_denial(sandbox):
    assert not is_likely_sandbox_denied(sandbox, 1, stderr='Operation not permitted')


@pytest.mark.parametrize('section', ['stdout', 'stderr', 'aggregated'])
@pytest.mark.parametrize('keyword', SANDBOX_DENIED_KEYWORDS)
def test_each_keyword_in_each_section_is_a_denial(section, keyword):
    text = 'error: ' + keyword.upper() + ' at /x'
    assert is_likely_sandbox_denied('seatbelt', 1, **{section: text})


def test_the_word_sandbox_in_ordinary_output_is_not_a_denial():
    # ruff citaba código de bajo_demanda.py y la tarjeta ofrecía repetir `ruff check .` sin sandbox.
    ruff = ('alfalfa/permisos/bajo_demanda.py:3:1: I001 Import block is un-sorted\n'
            '  | from ..seatbelt import SandboxUnavailable\n  | reasons = [REINTENTO]  # sin sandbox\n'
            'Found 4 errors.\n[*] 3 fixable with the `--fix` option.')
    assert not is_likely_sandbox_denied('seatbelt', 1, aggregated=ruff)
    assert not is_likely_sandbox_denied('seatbelt', 1, stdout="grep: 'sandbox': 12 coincidencias")


@pytest.mark.parametrize('text', [
    'Sandbox: swift-build(4711) deny(1) file-write-create /Users/x/.cache',
    'build\nsandbox: git(12) deny(1) file-write-data /repo/.git/config',
    'error: sandbox_apply: Operation not permitted',
    'kernel: deny(1) mach-lookup com.apple.coreservices',
])
def test_seatbelt_own_messages_are_denials(text):
    assert is_likely_sandbox_denied('seatbelt', 1, aggregated=text)


def test_ordinary_failure_is_not_a_denial():
    assert not is_likely_sandbox_denied('seatbelt', 1, stdout='1 failed, 3 passed', stderr='AssertionError',
                                        aggregated='1 failed, 3 passed\nAssertionError')


@pytest.mark.parametrize('code', [2, 126, 127])
def test_keywords_are_evaluated_before_quick_reject_codes(code):
    # Orden real de denial.rs: las palabras indicadoras ganan a 2, 126 y 127.
    assert is_likely_sandbox_denied('seatbelt', code, stderr='touch: x: Operation not permitted')
    assert not is_likely_sandbox_denied('seatbelt', code, stderr='command not found')


@pytest.mark.parametrize('code', [1, 65, 128 + 31, -9])
def test_other_codes_without_keywords_are_not_denials(code):
    # SIGSYS (128 + 31) sólo cuenta en seccomp de Linux; Seatbelt no lo produce.
    assert not is_likely_sandbox_denied('seatbelt', code, aggregated='fallo')


def test_missing_exit_code_is_not_a_denial():
    assert not is_likely_sandbox_denied('seatbelt', None, aggregated='Operation not permitted')


# ---------------------------------------------------------------- fallos que no son del sandbox
# Seatbelt contesta EPERM («Operation not permitted», Errno 1). «Permission denied» es EACCES: los
# permisos de siempre de Unix, la clave de ssh o un guion sin +x. Sin sandbox fallaría igual.
FALLOS_PROPIOS = [
    (1, "E   PermissionError: [Errno 13] Permission denied: '/var/log/app.log'\n1 failed, 12 passed in 0.31s"),
    (128, 'git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.'),
    (126, 'sh: ./scripts/desplegar.sh: Permission denied'),
    (1, 'npm ERR! code EACCES\nnpm ERR! Error: EACCES: permission denied, mkdir \'/usr/local/lib/node_modules\''),
    (1, 'cat: /etc/sudoers: Permission denied'),
]


@pytest.mark.parametrize('codigo,salida', FALLOS_PROPIOS)
def test_permission_denied_es_un_fallo_de_la_orden_no_del_sandbox(codigo, salida):
    assert not is_likely_sandbox_denied('seatbelt', codigo, aggregated=salida)


@pytest.mark.parametrize('salida', [
    'touch: .git/hooks/pre-commit: Operation not permitted',
    "PermissionError: [Errno 1] Operation not permitted: '/Users/yo/.zshrc'",
    'sh: /etc/hosts: Operation not permitted',
    "Error: EPERM: operation not permitted, open '/Users/yo/.npmrc'",
])
def test_operation_not_permitted_sigue_siendo_el_aviso_del_sandbox(salida):
    assert is_likely_sandbox_denied('seatbelt', 1, aggregated=salida)


@pytest.mark.skipif(sys.platform != 'darwin' or not os.access('/usr/bin/sandbox-exec', os.X_OK),
                    reason='requiere Seatbelt (macOS)')
def test_real_lo_que_niega_seatbelt_dice_operation_not_permitted(tmp_path):
    protegida = tmp_path / 'protegida'
    protegida.mkdir()
    perfil = ('(version 1)(allow default)(deny network-outbound)(deny file-write* (subpath "'
              + os.path.realpath(protegida) + '"))')
    ordenes = [['/usr/bin/touch', str(protegida / 'a')], ['/bin/mkdir', str(protegida / 'd')],
               ['/bin/sh', '-c', 'echo x > "$1"', 'sh', str(protegida / 'b')],
               [sys.executable, '-c', 'import sys; open(sys.argv[1], "w")', str(protegida / 'c')],
               [sys.executable, '-c', 'import socket; socket.create_connection(("127.0.0.1", 9), 2)']]
    for orden in ordenes:
        hecho = subprocess.run(['/usr/bin/sandbox-exec', '-p', perfil, *orden], capture_output=True, text=True,
                               timeout=30)
        salida = (hecho.stdout + hecho.stderr).lower()
        assert hecho.returncode != 0 and 'operation not permitted' in salida, (orden, salida)
        assert 'permission denied' not in salida
        assert is_likely_sandbox_denied('seatbelt', hecho.returncode, aggregated=hecho.stdout + hecho.stderr)
