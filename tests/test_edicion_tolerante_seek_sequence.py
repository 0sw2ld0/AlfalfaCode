"""Equivalencia con las pruebas y límites de seek_sequence.rs de Codex."""

import pytest

from alfalfa.edicion_tolerante.seek_sequence import normalise, seek_sequence


def test_exact_match_finds_sequence():
    assert seek_sequence(['foo', 'bar', 'baz'], ['bar', 'baz'], 0, False) == 1


def test_rstrip_match_ignores_trailing_whitespace():
    assert seek_sequence(['foo ', 'bar\t\t'], ['foo', 'bar'], 0, False) == 0


def test_trim_match_ignores_leading_and_trailing_whitespace():
    assert seek_sequence([' foo ', ' bar\t'], ['foo', 'bar'], 0, False) == 0


def test_pattern_longer_than_input_returns_none():
    assert seek_sequence(['just one line'], ['too', 'many', 'lines'], 0, False) is None


def test_start_limits_search_without_matching_an_earlier_line():
    lines = ['target', 'other', 'target']
    assert seek_sequence(lines, ['target'], 1, False) == 2
    assert seek_sequence(lines, ['target'], 3, False) is None


def test_eof_starts_at_last_possible_window():
    lines = ['target', 'other', 'target', 'end']
    assert seek_sequence(lines, ['target', 'end'], 0, True) == 2
    assert seek_sequence(['target', 'other'], ['target'], 0, True) is None


def test_eof_never_moves_before_start_and_empty_pattern_is_noop():
    assert seek_sequence(['target', 'other'], ['target'], 1, True) is None
    assert seek_sequence(['a'], [], 0, False) == 0
    assert seek_sequence(['a'], [], 1, True) == 1
    assert seek_sequence(['a'], [], 9, False) == 9
    assert seek_sequence([], [], 0, False) == 0


def test_exact_level_has_priority_over_later_tolerant_levels():
    lines = [' exact ', 'exact', 'exact ']
    assert seek_sequence(lines, ['exact'], 0, False) == 1


def test_rstrip_has_priority_over_strip_and_unicode():
    lines = [' value ', 'value ', '“value”']
    assert seek_sequence(lines, ['value'], 0, False) == 1


@pytest.mark.parametrize('fancy', ['‐', '‑', '‒', '–', '—', '―', '−'])
def test_normalise_codex_dashes(fancy):
    assert normalise('a' + fancy + 'b') == 'a-b'


@pytest.mark.parametrize('fancy', ['‘', '’', '‚', '‛'])
def test_normalise_codex_single_quotes(fancy):
    assert normalise('a' + fancy + 'b') == "a'b"


@pytest.mark.parametrize('fancy', ['“', '”', '„', '‟'])
def test_normalise_codex_double_quotes(fancy):
    assert normalise('a' + fancy + 'b') == 'a"b'


@pytest.mark.parametrize('space', [
    '\u00a0', '\u2002', '\u2003', '\u2004', '\u2005', '\u2006', '\u2007',
    '\u2008', '\u2009', '\u200a', '\u202f', '\u205f', '\u3000',
])
def test_normalise_only_codex_spaces(space):
    assert normalise('left' + space + 'right') == 'left right'


def test_rust_whitespace_is_trimmed_before_unicode_normalisation():
    assert seek_sequence(['\u00a0target\u00a0'], ['target'], 0, False) == 0
    assert seek_sequence(['target\u00a0'], ['target'], 0, False) == 0
    assert seek_sequence(['\u200btarget'], ['target'], 0, False) is None
    assert normalise('é') == 'é'


@pytest.mark.parametrize('control', ['\u001c', '\u001d', '\u001e', '\u001f'])
def test_python_file_separators_are_not_rust_whitespace(control):
    assert seek_sequence([control + 'target'], ['target'], 0, False) is None
    assert seek_sequence(['target' + control], ['target'], 0, False) is None
