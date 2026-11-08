# Procedencia de apply_patch

`parser.py` y `__init__.py` traducen a Python `codex-rs/apply-patch` de **OpenAI
Codex** (<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuentes: `src/parser.rs` (gramática, marcadores y modo tolerante con heredoc), `src/streaming_parser.rs` (lectura línea a línea y sus errores), `src/file_update.rs` (`compute_replacements`) y `src/text_file.rs` (finales de línea).
- La búsqueda tolerante es `../edicion_tolerante/seek_sequence.py`, ya portada de `src/seek_sequence.rs`.
- Las pruebas usan los escenarios portables de `codex-rs/apply-patch/tests/fixtures/scenarios`, copiados sin cambios en `server/tests/fixtures/apply_patch_codex` con su `NOTICE`.
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Traducción de Rust a Python; mensajes de error en español.
2. Todo o nada: el resultado de todos los bloques se calcula en memoria antes de escribir. Codex aplica en orden y conserva lo ya escrito si falla un bloque posterior (escenario 015); aquí ese parche no escribe nada.
3. Sin `*** Environment ID:`: AlfalfaCode tiene una sola carpeta de trabajo.
4. Se rechazan dos bloques que cambian las mismas líneas de un archivo.
5. La escritura reutiliza las salvaguardas de `edit_file`: rutas dentro de la carpeta, sin enlaces ni archivos privados (`.git`, `.env`, claves), copia previa privada, registro en `changes` y comprobación de que nada cambió entre leer y escribir.
6. Se expone como herramienta de función con un campo `input`, no como herramienta *freeform* con gramática Lark.
