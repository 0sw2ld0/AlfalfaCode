"""Evidencia del arnés: registro de comprobaciones, operaciones y espacio de trabajo.

La lectura pública (sin tocar privados de `RunEvidence`) está en `lectura.py`.
"""
from .lectura import clasificacion, comprobacion_suficiente, comprobacion_vigente, salida_incompleta

__all__ = ['clasificacion', 'comprobacion_suficiente', 'comprobacion_vigente', 'salida_incompleta']
