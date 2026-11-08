"""Heurística de denegación probable del sandbox (permisos/denegacion.py).

Adaptado de codex-rs/sandboxing/src/denial.rs (OpenAI Codex, Apache-2.0; ver
alfalfa/seatbelt/NOTICE y PROCEDENCIA.md).
"""
import pytest

from alfalfa.permisos.denegacion import (QUICK_REJECT_EXIT_CODES, SANDBOX_DENIED_KEYWORDS,
                                           is_likely_executor_managed_sandbox_denied, is_likely_sandbox_denied)


def test_keywords_and_quick_reject_codes_match_codex():
    # Las de Codex, en su orden, más las de Launch Services de macOS (diferencia documentada).
    assert SANDBOX_DENIED_KEYWORDS[:7] == ('operation not permitted', 'permission denied', 'read-only file system',
                                           'seccomp', 'sandbox', 'landlock', 'failed to write file')
    assert SANDBOX_DENIED_KEYWORDS[7:] == ('unable to find application named', 'no application knows how to open',
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
