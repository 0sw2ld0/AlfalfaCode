# Qué incluye

| Módulo | Hace |
|---|---|
| `proveedores/nativo/` | El bucle del agente (`NativeRuntime`) y sus herramientas |
| `terminal.py`, `stdio.py` | La orden `alfalfa` y el protocolo JSONL para apps |
| `navegador.py` | Las herramientas `browser_*` del navegador propio de la app de Mac |
| `permisos/`, `seatbelt/`, `reglas/` | Permisos bajo demanda, sandbox de macOS y reglas de `reglas.toml` |
| `mcp_cliente/`, `complementos/`, `hooks/` | Servidores MCP, complementos y hooks compatibles con Claude Code |
| `compactacion/`, `agents_md/`, `sesiones/` | Compactación del contexto, `AGENTS.md` y sesiones con id por turno |
| `importadas/` | Proyectos de OpenCode, Codex y Claude Code: MCP, agentes, órdenes «/» e instrucciones |
| `deshacer/` | Instantáneas por turno en un git paralelo, volver con el código y rehacer |
| `busqueda_sesiones/` | `buscar_sesiones` y `--buscar-sesiones`: índice FTS5 de las conversaciones guardadas |
| `apply_patch/`, `edicion_tolerante/` | Edición por parches y reemplazos tolerantes |
| `diagnosticos/` | Servidores de lenguaje y linters tras cada edición |
| `catalogo/`, `proveedores/compatible.py` | Catálogo de models.dev y proveedores compatibles con OpenAI |
| `proveedores/openai.py`, `proveedores/anthropic.py` | OpenAI (Responses API) y Claude (Messages API) con la clave del usuario |
| `proveedores/lista_api.py` | La lista real de modelos de OpenAI y Anthropic (`GET /v1/models`), con caché |
