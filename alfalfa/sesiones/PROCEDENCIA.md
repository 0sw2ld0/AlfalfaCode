# Procedencia de las sesiones con id por turno

Este módulo adapta el modelo de sesiones de **OpenAI Codex**
(<https://github.com/openai/codex>), bajo Apache License 2.0. No copia código
literal: traduce sus estructuras y su comportamiento.

- Revisión consultada: `faeb379646bdbad016e11f7c9ca91113cad4bc9d`.
- Fuentes:
  - `codex-rs/protocol/src/protocol.rs`: `SessionMeta` (id, fecha, cwd, originador, proveedor, `forked_from_id`) y `GitInfo` (commit, rama, URL).
  - `codex-rs/protocol/src/sanitized_git_url.rs`: la URL del remoto se guarda sin credenciales y se descartan los ayudantes de remoto (`transporte::…`).
  - `codex-rs/core/src/thread_rollout_truncation.rs`: los turnos empiezan en cada mensaje del usuario y una bifurcación copia el historial truncado al final de un turno.
  - `codex-rs/app-server/README.md`: `thread/fork` con un id nuevo.
- `NOTICE` conserva las atribuciones de Codex. `LICENSE` contiene su aviso de uso Apache-2.0; la licencia íntegra distribuida con AlfalfaCode está en `../seatbelt/LICENSE`.

## Modificaciones (Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Los metadatos viven en `task.json` (campo `meta`), no en la primera línea de un JSONL de rollout; el historial sigue en `native/<id>/session.json` y la transcripción en `<id>.jsonl`.
2. Cada turno guarda `desde` y `hasta` (posiciones del historial) en `task.json`; las filas de la transcripción y los eventos stdio llevan `turno`.
3. El saneado de la URL es una versión reducida de `SanitizedGitUrl`: quita usuario y clave de las URL con esquema (salvo el usuario de ssh), la clave de la forma scp, la consulta y el fragmento.
4. Sin índice SQLite: los listados recorren las carpetas de sesión.
5. Una bifurcación no copia la carpeta de trabajo ni deshace cambios: sigue en la misma, como en Codex, y conserva el registro de evidencia de la sesión de origen.
