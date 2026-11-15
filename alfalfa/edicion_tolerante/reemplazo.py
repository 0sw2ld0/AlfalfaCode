"""Adaptador de seek_sequence para edit_file; no reescribe texto ajeno al bloque."""

from .seek_sequence import _candidates, normalise, rstrip, strip


class NoMatchError(ValueError):
    """El bloque antiguo no aparece por ninguno de los cuatro niveles."""


class AmbiguousMatchError(ValueError):
    """El primer nivel aplicable contiene más de un intervalo posible."""


def _without_line_end(line):
    return line[:-2] if line.endswith('\r\n') else line.rstrip('\r\n')


def _fuzzy_span(text, old):
    """Intervalo de contenido, sin consumir un terminador ausente de old."""
    original_lines = text.splitlines(keepends=True)
    pattern_lines = old.splitlines(keepends=True)
    pattern = [_without_line_end(line) for line in pattern_lines]
    lines = [_without_line_end(line) for line in original_lines]
    for level, transform in (
            ('comparación exacta de líneas', lambda line: line),
            ('eliminación de espacios finales', rstrip),
            ('eliminación de espacios en ambos extremos', strip),
            ('normalización Unicode', normalise)):
        matches = _candidates(lines, pattern, 0, False, transform)
        if len(matches) > 1:
            raise AmbiguousMatchError(
                'El bloque es ambiguo en el nivel «' + level + '»: '
                + str(len(matches)) + ' coincidencias. Añade contexto para que sea único; '
                'el archivo no se modificó.')
        if matches:
            start_line = matches[0]
            end_line = start_line + len(pattern)
            start = sum(map(len, original_lines[:start_line]))
            end = sum(map(len, original_lines[:end_line]))
            pattern_has_final_break = old.endswith(('\r', '\n'))
            last_line = original_lines[end_line - 1]
            if not pattern_has_final_break:
                end -= len(last_line) - len(_without_line_end(last_line))
            return start, end
    raise NoMatchError('El bloque debe coincidir exactamente una vez.')


def _literal_occurrences(text, old):
    """Cuenta también solapamientos; ninguno puede ocultar una ambigüedad."""
    occurrences = 0
    start = 0
    while (position := text.find(old, start)) != -1:
        occurrences += 1
        start = position + 1
    return occurrences


def _con_finales_del_archivo(text, new):
    """`new` en CRLF si todo el archivo usa CRLF y `new` trae LF sueltos.

    Casi ningún modelo escribe `\r`: en un archivo CRLF, el bloque editado quedaba en LF y el
    resto en CRLF, y la herramienta lo daba por bueno. Un archivo ya mezclado se deja como está.
    """
    crlf = text.count('\r\n')
    if not crlf or crlf != text.count('\n') or '\n' not in new:
        return new
    return new.replace('\r\n', '\n').replace('\n', '\r\n')


def tolerant_edit(text, old, new):
    """Sustituye un bloque único y conserva todo lo exterior byte por byte."""
    new = _con_finales_del_archivo(text, new)
    if not old:
        raise ValueError('El bloque debe coincidir exactamente una vez.')
    occurrences = _literal_occurrences(text, old)
    if occurrences > 1:
        raise AmbiguousMatchError(
            'El bloque exacto es ambiguo: ' + str(occurrences)
            + ' coincidencias. Añade contexto para que sea único; el archivo no se modificó.')
    if occurrences == 1:
        return text.replace(old, new, 1)
    start, end = _fuzzy_span(text, old)
    return text[:start] + new + text[end:]
