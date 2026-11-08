"""Edición tolerante exclusiva de AlfalfaCode."""
from .reemplazo import AmbiguousMatchError, NoMatchError, tolerant_edit
from .seek_sequence import normalise, seek_sequence

__all__ = ['AmbiguousMatchError', 'NoMatchError', 'normalise', 'seek_sequence', 'tolerant_edit']
