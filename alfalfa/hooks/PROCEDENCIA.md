# Procedencia de los hooks

`motor.py`, `nombres.py` y `sesion.py` traducen y adaptan `codex-rs/hooks` y
`codex-rs/core/src/tools/hook_names.rs` de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuentes:
  - `hooks/src/engine/matcher.rs`: filtro vacío o `*`, lista exacta `[A-Za-z0-9_|]` o expresión regular.
  - `hooks/src/engine/output_parser.rs`: campos universales (`continue`, `stopReason`, `suppressOutput`, `systemMessage`), `hookSpecificOutput` por evento, `permissionDecision`, `updatedInput`, `additionalContext` y los diagnósticos de salidas no soportadas.
  - `hooks/src/events/{pre_tool_use,post_tool_use,user_prompt_submit,stop,session_start}.rs`: código 2 con motivo en stderr, texto plano como contexto y la última reescritura que gana.
  - `hooks/schema/generated/*.command.input.schema.json`: la entrada de cada evento, con `turn_id`.
  - `hooks/src/engine/discovery.rs`: `PLUGIN_ROOT` y `CLAUDE_PLUGIN_ROOT` para los hooks de un complemento.
  - `core/src/tools/hook_names.rs`: nombre canónico más alias de filtro (`apply_patch` con `Write` y `Edit`).
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Traducción de Rust a Python; mensajes y diagnósticos en español.
2. Sólo cinco eventos (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop) y sólo hooks `command`, siempre en serie; el resto se diagnostica.
3. Para Bash, Read, Edit y Write el hook recibe el nombre y la forma de entrada de Claude Code, no los nativos; los nativos viajan en `alfalfa_tool_name` y `alfalfa_tool_input`. Un `updatedInput` sólo se acepta si se traduce de forma exacta (una orden de shell con tuberías, variables o comodines se rechaza) y luego vuelve a validarse y a pedir permiso.
4. Fallo cerrado: un PreToolUse que no arranca, vence su plazo, sale con código distinto de 0 y 2 o devuelve una salida inválida bloquea la herramienta (Codex lo deja pasar).
5. `permissionDecision: allow` no concede permisos (ni salta el permiso MCP); `ask` se trata como salida no soportada y bloquea.
6. Cada hook corre con `/bin/sh` dentro del sandbox Seatbelt de AlfalfaCode, sin red y con un entorno mínimo (PATH, HOME, TMPDIR, LANG, CLAUDE_PROJECT_DIR, las variables del complemento y las declaradas por el usuario); nunca hereda la clave de Ollama. Salida acotada a 64 KB por flujo y contexto a 10 000 caracteres.
7. Stop puede pedir seguir como mucho tres veces por turno.
8. Fuentes: `$ALFALFA_HOME/hooks.json` y los complementos autorizados; nunca la carpeta de trabajo.
