# Fixture derivado de Runpod 1.7.4

Contenido derivado del complemento real `runpod` 1.7.4, instalado en
`~/.claude/plugins/cache/runpod/runpod/1.7.4` en el Mac de referencia.

- **Origen:** <https://github.com/runpod/runpod-plugins-official>.
- **Licencia declarada por el complemento:** Apache-2.0 (campo `license` de su
  manifiesto de Claude Code y `license: Apache-2.0` en sus SKILL.md).
- **Qué se conserva:** estructura de los dos manifiestos
  (`.claude-plugin/plugin.json` y `.codex-plugin/plugin.json`) con sus campos y
  valores reales, el `.mcp.json` real (servidor `runpod`, transporte `http`,
  URL `https://mcp.getrunpod.io/`), el comando `commands/migrate.md` y dos
  habilidades con su front matter real (`pod-doctor`, `runpod-mcp`), con el
  cuerpo recortado.
- **Qué se omite a propósito:** `assets/logo.png`, `skills/*/reference/`,
  `skills/*/evals/`, `skills/*/scripts/`, `gemini-extension.json`, `.in_use/`
  y todo el resto de las 15 habilidades. Ningún archivo del original contiene
  credenciales: la autenticación de Runpod vive en el CLI y en el servidor MCP
  remoto, no en el paquete.
- **Qué es doble:** las conexiones externas. Las pruebas nunca abren
  `https://mcp.getrunpod.io/`: el cliente MCP se sustituye y se comprueba la
  normalización de la configuración, no una ejecución real.
- **Modificaciones:** se recortaron los cuerpos de `SKILL.md` y `migrate.md`;
  los manifiestos y `.mcp.json` son copia literal salvo el orden de las claves
  de `.mcp.json` (sin cambios de valor). Esta nota es el aviso de atribución.
