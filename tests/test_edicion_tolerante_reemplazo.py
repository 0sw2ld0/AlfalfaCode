"""Adaptador tolerante: selección única y conservación del intervalo exterior."""

import pytest

from alfalfa.edicion_tolerante import AmbiguousMatchError, tolerant_edit


def test_exact_match_still_uses_literal_replacement():
    assert tolerant_edit('before target after', 'target', 'new') == 'before new after'


def test_no_match_and_empty_old_are_rejected():
    for old in ('', 'absent'):
        with pytest.raises(ValueError, match='exactamente una vez'):
            tolerant_edit('target\n', old, 'new')


def test_unicode_replacement_does_not_normalise_surrounding_bytes():
    before = b'prefix \xe2\x80\x94 untouched\r\nlet value = \xe2\x80\x9cold\xe2\x80\x9d;\r\nsuffix \xe2\x80\x98keep\xe2\x80\x99\r\n'
    text = before.decode()
    result = tolerant_edit(text, 'let value = "old";', 'let value = "new";').encode()
    assert result == (b'prefix \xe2\x80\x94 untouched\r\nlet value = "new";\r\n'
                      b'suffix \xe2\x80\x98keep\xe2\x80\x99\r\n')
    assert result.startswith(before.splitlines(keepends=True)[0])
    assert result.endswith(before.splitlines(keepends=True)[-1])


def test_multiline_replacement_preserves_original_crlf_outside_interval():
    text = 'prefix\r\n  first  \r\n\tsecond\t\r\nsuffix\n'
    result = tolerant_edit(text, 'first\nsecond', 'FIRST\r\nSECOND')
    assert result == 'prefix\r\nFIRST\r\nSECOND\r\nsuffix\n'


def test_omitted_final_break_is_not_consumed():
    text = 'prefix\r\n“target”\r\nsuffix\n'
    assert tolerant_edit(text, '"target"', '"new"') == (
        'prefix\r\n"new"\r\nsuffix\n')


def test_explicit_final_break_is_replaced():
    text = 'prefix\r\n“target”\r\nsuffix\n'
    assert tolerant_edit(text, '"target"\n', '"new"') == (
        'prefix\r\n"new"suffix\n')


def test_exact_line_level_wins_before_trimming():
    text = ' target \nexact\n'
    assert tolerant_edit(text, 'exact', 'new') == ' target \nnew\n'


def test_exact_line_level_can_edit_when_there_is_no_literal_substring():
    assert tolerant_edit('a\rtarget\r\nb\n', 'target\n', 'new\n') == 'a\rnew\nb\n'


def test_rstrip_level_wins_before_both_side_trim():
    text = ' target \ntarget \n'
    assert tolerant_edit(text, 'target\n', 'new\n') == ' target \nnew\n'


def test_strip_level_is_used_after_rstrip_has_no_candidate():
    text = 'target other\n target \n'
    assert tolerant_edit(text, 'target\n', 'new\n') == 'target other\nnew\n'


def test_ambiguous_exact_match_fails_without_fuzzy_fallback():
    text = 'exact\nother\nexact\n'
    with pytest.raises(AmbiguousMatchError, match='exacto es ambiguo: 2'):
        tolerant_edit(text, 'exact', 'new')


@pytest.mark.parametrize('text,old,count', [('aaa', 'aa', 2), ('aaaa', 'aa', 3)])
def test_overlapping_literal_matches_are_ambiguous(text, old, count):
    original = text
    with pytest.raises(AmbiguousMatchError, match='exacto es ambiguo: ' + str(count)):
        tolerant_edit(text, old, 'X')
    assert text == original


@pytest.mark.parametrize('text,old,level', [
    ('target \ntarget\t\n', 'target\n', 'finales'),
    (' target \n\ttarget\t\n', 'target\n', 'ambos extremos'),
    ('“target”\n„target‟\n', '"target"', 'Unicode'),
])
def test_every_fuzzy_level_rejects_all_ambiguous_candidates(text, old, level):
    with pytest.raises(AmbiguousMatchError, match=level):
        tolerant_edit(text, old, 'new')


def test_later_unique_candidate_does_not_hide_ambiguity_at_previous_level():
    text = 'target \ntarget\t\n“other”\n'
    with pytest.raises(AmbiguousMatchError, match='finales'):
        tolerant_edit(text, 'target\n', 'new\n')


def test_no_match_does_not_fall_back_to_substring_across_lines():
    with pytest.raises(ValueError, match='exactamente una vez'):
        tolerant_edit('prefix\ntarget\nsuffix\n', 'fix\ntarget\nsuf\n', 'new')
