"""Edición tolerante exclusiva de AlfalfaCode."""
from .seek_sequence import normalise, seek_sequence
from .reemplazo import AmbiguousMatchError, NoMatchError, tolerant_edit

__all__ = ['AmbiguousMatchError', 'NoMatchError', 'normalise', 'seek_sequence', 'tolerant_edit']
