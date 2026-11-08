# Procedencia de las reglas de órdenes

`__init__.py` traduce y adapta `codex-rs/execpolicy` de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuentes: `execpolicy/README.md`, `src/rule.rs` (`PrefixPattern::matches_prefix`, alternativas), `src/policy.rs` (la decisión final es la más estricta: `forbidden` > `prompt` > `allow`), `src/parser.rs` (`prefix_rule` con `pattern`, `decision`, `justification`, `match` y `not_match`, ejemplos comprobados al cargar) y `core/src/exec_policy.rs` (scripts `bash -lc` evaluados por partes).
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Sin Starlark: las reglas se leen de TOML (`$ALFALFA_HOME/reglas.toml`, tablas `[[regla]]`), con nombres en español (`patron`, `decision` = permitida/preguntar/prohibida, `justificacion`, `coincide`, `no_coincide`) y también los de Codex.
2. Sin `host_executable` ni `canonical_matching`: una ruta absoluta coincide siempre con la regla de su nombre.
3. Los scripts de shell se analizan con un lector propio de palabras literales unidas por `&&`, `||`, `;` o `|`, no con tree-sitter. Si no son analizables y hay reglas que preguntan o prohíben, se pregunta (Codex los trata como orden no reconocida).
4. Se quitan envoltorios sencillos (`env VAR=…`, `command`, `nohup`, `time`, `nice`, `sudo`) para evaluar también la orden de dentro.
5. `permitida` no salta el sandbox: sólo quita la pregunta de orden peligrosa. La red y la salida del sandbox siguen preguntando. En Codex, `allow` puede ejecutar sin sandbox.
6. Un archivo de reglas inválido impide arrancar la sesión, para no perder una prohibición en silencio.
7. `alfalfa --comprobar-orden ARGV` hace lo que `codex execpolicy check`.
