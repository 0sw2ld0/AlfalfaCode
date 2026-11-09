# Procedencia de la edición tolerante

`seek_sequence.py` es una traducción a Python de
`codex-rs/apply-patch/src/seek_sequence.rs`, de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuente: <https://github.com/openai/codex/blob/faeb379646bdbad016e11f7c9ca91113cad4bc9d/codex-rs/apply-patch/src/seek_sequence.rs>.
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Traducción del algoritmo y de `normalise` de Rust a Python.
2. `_candidates` enumera las coincidencias de cada pasada; `seek_sequence` sigue devolviendo la primera, como la referencia.
3. `reemplazo.py` es código nuevo del adaptador: exige unicidad en el primer nivel aplicable, informa de ambigüedad y sustituye sólo el intervalo localizado del texto original.
4. Se implementa el repertorio Unicode `White_Space` usado por `trim`/`trim_end` de Rust, sin ampliar la tabla explícita de sustituciones de `normalise`.
