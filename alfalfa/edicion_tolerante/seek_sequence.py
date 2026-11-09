"""Port a Python de codex-rs/apply-patch/src/seek_sequence.rs de OpenAI Codex.

Copyright 2025 OpenAI. Licencia Apache-2.0; véanse LICENSE y NOTICE junto a
este archivo. Fuente: https://github.com/openai/codex/blob/faeb379646bdbad016e11f7c9ca91113cad4bc9d/codex-rs/apply-patch/src/seek_sequence.rs

Modificado para AlfalfaCode en noviembre de 2026: traducción de Rust a
Python y función auxiliar para enumerar candidatos sin cambiar la prioridad.
La API seek_sequence conserva la primera coincidencia, igual que la referencia.
"""


# Unicode White_Space: el predicado de char::is_whitespace que usan trim y
# trim_end en Rust. Se explicita para no depender del repertorio de Python.
WHITE_SPACE = ''.join(map(chr, (
    *range(0x0009, 0x000E), 0x0020, 0x0085,
    0x00A0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029,
    0x202F, 0x205F, 0x3000,
)))


def rstrip(value):
    return value.rstrip(WHITE_SPACE)


def strip(value):
    return value.strip(WHITE_SPACE)


def normalise(value):
    """Recorta extremos y equipara sólo la puntuación y espacios de Codex."""
    translation = {
        0x2010: '-', 0x2011: '-', 0x2012: '-', 0x2013: '-',
        0x2014: '-', 0x2015: '-', 0x2212: '-',
        0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'",
        0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"',
        0x00A0: ' ', 0x2002: ' ', 0x2003: ' ', 0x2004: ' ',
        0x2005: ' ', 0x2006: ' ', 0x2007: ' ', 0x2008: ' ',
        0x2009: ' ', 0x200A: ' ', 0x202F: ' ', 0x205F: ' ',
        0x3000: ' ',
    }
    return strip(value).translate(translation)


def _candidates(lines, pattern, start, eof, transform):
    """Índices de una pasada; el orden de pasadas lo decide seek_sequence."""
    if not pattern:
        return [start] if start <= len(lines) else []
    if len(pattern) > len(lines) or start > len(lines) - len(pattern):
        return []
    search_start = max(len(lines) - len(pattern), start) if eof else start
    transformed_pattern = [transform(line) for line in pattern]
    return [index for index in range(search_start, len(lines) - len(pattern) + 1)
            if [transform(line) for line in lines[index:index + len(pattern)]]
            == transformed_pattern]


def seek_sequence(lines, pattern, start, eof):
    """Devuelve el primer índice que coincide, o None si no hay coincidencia.

    Como en Rust: patrón vacío devuelve start; uno mayor que la entrada, None.
    Con eof se busca primero desde el final posible sin retroceder antes de
    start. Las cuatro pasadas son exacta, rstrip, strip y normalise.
    """
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    for transform in (lambda line: line, rstrip, strip, normalise):
        matches = _candidates(lines, pattern, start, eof, transform)
        if matches:
            return matches[0]
    return None
