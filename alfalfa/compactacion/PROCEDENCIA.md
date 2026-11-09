# Procedencia de la compactación del contexto

Este módulo adapta la compactación de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuentes:
  - `codex-rs/core/src/compact.rs` (`COMPACT_USER_MESSAGE_MAX_TOKENS`, `build_compacted_history_with_limit`, `is_summary_message`).
  - `codex-rs/prompts/templates/compact/prompt.md` y `summary_prefix.md`.
  - `codex-rs/protocol/src/openai_models.rs` (`auto_compact_token_limit`: 9/10 de la ventana o el límite configurado si es menor).
  - `codex-rs/utils/string/src/truncate.rs` (`approx_token_count` y el recorte por el medio).
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. `prompt.md` y `summary_prefix.md` están traducidos al español; el contenido y la estructura son los del original.
2. El algoritmo se traduce de Rust a Python sobre mensajes de chat de Ollama (`role`/`content`) en vez de `ResponseItem`; las instrucciones iniciales son el primer mensaje `system`.
3. Los mensajes del usuario que se conservan son los que el arnés registró como mensajes reales del usuario (el encargo y los mensajes del cliente), nunca los avisos internos del arnés ni un resumen anterior.
4. El historial completo se conserva en `native/<id>/messages.jsonl` (al que sólo se añade, como el rollout de Codex; `session.json` guarda dónde acaba); el punto de control guarda el resumen y los mensajes elegidos, y el contexto se reconstruye a partir de ellos.
5. El recorte por el medio cuenta bytes UTF-8 y respeta los límites de carácter descartando bytes parciales.
6. `prompt.md` pide el resumen por apartados, como la compactación de Claude Code: encargo actual, restricciones, lo hecho, errores, pedidos anteriores (atendidos o no), lo pendiente, en qué estaba y un siguiente paso que sólo continúa el encargo actual. `summary_prefix.md` añade que no se retoman pedidos ya atendidos. Con el prompt original, un modelo pequeño leía los mensajes antiguos del usuario como encargos nuevos y los repetía (hizo commits que nadie había pedido).
7. Los mensajes anteriores del usuario van juntos en un solo mensaje marcado como historia ya atendida; sólo el último va como mensaje propio del usuario. Antes de él se vuelve a dar el contexto del arnés (como `insert_initial_context_before_last_real_user_or_summary` de Codex): la lista de archivos que cambió el propio agente, para que no tome por suyos los cambios ajenos.
8. Si ni así cabe, antes de quitar los mensajes más antiguos se quitan los informes de estado del arnés que ya quedaron superados.
9. Propio de AlfalfaCode, sin equivalente en Codex: con un modelo que cobra más toda la petición pasado un umbral de tokens (GPT-6 Luna a 272K, Claude Haiku 5.5 a 100K), el modo «Ahorro» de la sesión compacta también al 90 % de ese umbral, avisa al 80 % y, si una sola petición lo pasaría, recorta antes las salidas de herramientas más grandes. El modo «Contexto completo» usa la ventana entera y avisa del coste.
