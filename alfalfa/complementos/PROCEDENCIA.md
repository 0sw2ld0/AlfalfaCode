# Procedencia del cargador de complementos

`cargador.py` traduce a Python reglas de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`
  (copia local en `referencias/codex/codex-rs`).
- `NOTICE` conserva las atribuciones de Codex; `LICENSE` reproduce su aviso de
  uso Apache-2.0 y remite a la licencia íntegra del proyecto en
  `../seatbelt/LICENSE`.

## Fuentes contrastadas (CA-3)

| Regla local | Archivo de Codex | Qué se tomó |
|---|---|---|
| Orden de manifiestos y detección del portable | `utils/plugins/src/plugin_namespace.rs` (`find_plugin_manifest_path`), `exec-server-protocol/src/protocol.rs` (`DISCOVERABLE_PLUGIN_MANIFEST_PATHS`) | `plugin.json` raíz sólo si su `$schema` es de agent-plugins; después `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`; sólo el primero existente |
| Estado del `$schema` | `plugin_namespace.rs` (`agent_plugin_schema_status`) | soportado / no soportado (prefijo agent-plugins) / ajeno |
| Campos del manifiesto portable | `core-plugins/src/agent_plugin_manifest.rs` (`AGENT_PLUGIN_FIELDS`) | lista cerrada de campos de Agent Plugins 1.0.0 |
| Validación de rutas declaradas | `core-plugins/src/manifest.rs` (`resolve_manifest_path`) | prefijo `./`, sin `..`, sin rutas absolutas, resultado dentro de la raíz |
| Valores predeterminados de capacidades | `core-plugins/src/loader.rs` (`DEFAULT_SKILLS_DIR_NAME`, `DEFAULT_MCP_CONFIG_FILE`, `DEFAULT_APP_CONFIG_FILE`) | `skills/`, `.mcp.json`; `mcp.json` para el portable; `.app.json` nunca se interpreta |
| Descubrimiento de habilidades | `core-plugins/src/loader.rs` (`SkillDiscoveryMode::Recursive`) | recorrido recursivo de `SKILL.md` sin seguir enlaces de directorio |
| Namespace del complemento | `core-plugins/src/loader.rs` (`plugin_namespace = manifest.name`) | identidad estable por nombre; el espacio de nombres de comandos y habilidades se deriva de ella |
| Variables del servidor MCP | `codex-mcp/src/agent_plugin_config.rs` (`PLUGIN_ROOT`) | sólo `${PLUGIN_ROOT}`; `cwd` predeterminado a la raíz del complemento |
| Origen del catálogo de marketplaces | `core-plugins/src/marketplace.rs` (`MARKETPLACE_MANIFEST_RELATIVE_PATHS`) | **no** se lee ningún marketplace: sólo raíces de `complementos.json` |

No se copió código Rust: se reimplementaron las reglas en Python, con nombres y
mensajes propios. La única estructura literal compartida es la lista ordenada de
rutas de manifiesto y la lista cerrada de campos del esquema publicado
(`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`).

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Traducción de las reglas de precedencia, validación de rutas y valores
   predeterminados a un cargador Python pasivo.
2. Diferencias deliberadas respecto de Codex: no se cargan marketplaces ni se
   escanea `~/.claude/plugins`; sólo se leen raíces declaradas por el usuario
   en `$ALFALFA_HOME/complementos.json`. Un `$schema` no soportado en
   `plugin.json` raíz detiene la carga con diagnóstico en vez de degradar al
   manifiesto de cliente, para no adivinar el formato.
3. Los hooks, `.app.json` y los conectores se enumeran y se ignoran: no se
   ejecutan ni se registran (CA-12).
4. La confianza, las herramientas directas, los filtros y los límites de
   esquema sólo pueden venir de la configuración del usuario; ningún campo del
   complemento los concede (CA-10).
