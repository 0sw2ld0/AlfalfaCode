"""Complementos de AlfalfaCode: activación explícita y exposición diferida.

Sólo TerminalManager integra este cargador. Descubrir o listar no ejecuta
procesos, hooks, conectores ni solicitudes de red. Nada se descubre desde el
cwd, ~/.claude/plugins o marketplaces sin una raíz en complementos.json.
"""
from .cargador import SCHEMA, CargadorComplementos  # noqa: F401
