"""Cliente MCP de AlfalfaCode (SDK oficial `mcp`): exposición diferida, permisos y medición.

Sólo TerminalManager lo crea, y sólo si existe $ALFALFA_HOME/mcp.json; los
agentes del integrador y una terminal sin ese archivo no cambian.
"""
from .gestor import BUSCAR, McpManager  # noqa: F401
