# Procedencia de las instrucciones AGENTS.md

`__init__.py` es una traducción a Python de `codex-rs/core/src/agents_md.rs`, de
**OpenAI Codex** (<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuente: <https://github.com/openai/codex/blob/faeb379646bdbad016e11f7c9ca91113cad4bc9d/codex-rs/core/src/agents_md.rs>.
- Constantes conservadas: `AGENTS.md`, `AGENTS.override.md`, el marcador `.git`, el tope de 32 KiB (`DEFAULT_PROJECT_DOC_MAX_BYTES`) y el separador `--- project-doc ---`.
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Traducción de Rust a Python sobre el sistema de archivos local, sin el sistema de archivos remoto ni los entornos múltiples de Codex.
2. Sin `project_root_markers` ni `project_doc_fallback_filenames` configurables: los marcadores son `.git` y los nombres, `AGENTS.override.md` y `AGENTS.md`.
3. Las instrucciones del usuario salen de `$ALFALFA_HOME` en vez de `$CODEX_HOME`, con el mismo tope.
4. Sin la noción de proyecto no confiable: AlfalfaCode ya trata las instrucciones del proyecto como datos que no cambian sus reglas, y así lo dice el texto que las presenta al modelo.
5. Mensajes y avisos en español.
