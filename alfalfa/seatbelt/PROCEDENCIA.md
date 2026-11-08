# Procedencia del sandbox Seatbelt de AlfalfaCode

Parte de esta carpeta procede de **OpenAI Codex** (<https://github.com/openai/codex>),
licenciado bajo Apache License 2.0. Se conservan su `NOTICE` y su `LICENSE` sin cambios.

- Copia local de referencia: `referencias/codex` (remoto `https://github.com/openai/codex.git`).
- Revisión de referencia: `faeb379646bdbad016e11f7c9ca91113cad4bc9d` (commit del 6 nov 2026).

## Archivos copiados sin cambios

| Archivo aquí | Origen en Codex | SHA-256 |
|---|---|---|
| `seatbelt_base_policy.sbpl` | `codex-rs/sandboxing/src/seatbelt_base_policy.sbpl` | `5103332ddb8885ee5e1926de6c0ef23a61f4e55e31297506ae05fa4e0b24ba74` |
| `seatbelt_network_policy.sbpl` | `codex-rs/sandboxing/src/seatbelt_network_policy.sbpl` | `eb2bf67e7a697d954f05fbd563e5888402fde3a279ecf383832473d44831ad0a` |
| `seatbelt_preferences_policy.sbpl` | `codex-rs/sandboxing/src/seatbelt_preferences_policy.sbpl` | `bfbbe3f051112c7c24dc3a61e09b54cee7e0ea33a420ff7fa9aa4dace03d52aa` |
| `NOTICE` | `NOTICE` | `9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915` |
| `LICENSE` | `LICENSE` (Apache-2.0) | `d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc` |

Los comentarios originales de los `.sbpl` (incluidas las referencias a la política de
Chromium) se mantienen intactos.

## Archivos derivados

- `__init__.py`: reescritura en Python de partes de `codex-rs/sandboxing/src/seatbelt.rs`
  (`MACOS_PATH_TO_SEATBELT_EXECUTABLE`, `create_seatbelt_command_args_with_profile`,
  `build_seatbelt_access_policy`, `seatbelt_protected_metadata_name_regex`, la política de
  red completa de `dynamic_network_policy_for_network` y `MACOS_SEATBELT_TLS_TRUST_POLICY`).

## Adaptaciones (aviso de modificación, Apache-2.0 §4.b)

Modificado para AlfalfaCode en noviembre de 2026:

1. Política de sistema de archivos fija: lectura de todo el disco; escritura sólo en la
   carpeta de trabajo resuelta, `$TMPDIR` resuelto y `/private/tmp`. Un `$TMPDIR` que
   abarque el hogar del usuario, sea `/` o no exista se ignora.
2. `.git` de solo lectura en la raíz **y en subcarpetas** (`^<raíz>/(.*/)?\.git(/.*)?$`);
   Codex sólo protege la raíz. Se añade además una denegación final para que una raíz
   que contenga la carpeta de trabajo no la reabra. Como la expresión depende de la ruta,
   se deniega `file-write-unlink` (borrar o renombrar) sobre cada directorio antecesor de
   un `.git` anidado, hallado al construir la política, y sobre los antecesores de la
   carpeta de trabajo que estén dentro de otra raíz escribible. Un directorio ilegible
   durante el recorrido se sella entero: no se borra ni renombra ningún directorio de
   su subárbol. Si el árbol supera
   `SCAN_LIMIT` entradas, no se puede borrar ni renombrar ningún directorio de la carpeta.
3. Las raíces se resuelven con `realpath` en lugar de rechazar componentes enlazados.
4. Una comilla en la ruta se representa como `[^/]` en la expresión regular: dentro de
   `#"…"` Seatbelt no admite `\"` (el escape de Codex rompe la política, comprobado con
   `sandbox-exec`).
5. Sin proxy gestionado, sin sockets Unix y sin la protección del socket del demonio de
   Codex, que no existe en AlfalfaCode. La red queda cerrada salvo `network=True`.
6. Antes de cada orden se lanza la misma política con `/usr/bin/true` para distinguir un
   fallo del sandbox de un fallo de la orden.

## Política on-request

Se añadieron fuera de esta carpeta tres módulos más, derivados de la misma revisión de
Codex (`faeb379646bdbad016e11f7c9ca91113cad4bc9d`). Usan este mismo `NOTICE` y este mismo
`LICENSE`, y su cabecera remite aquí.

| Archivo | Origen en Codex | SHA-256 del origen |
|---|---|---|
| `alfalfa/permisos/peligrosos.py` | `codex-rs/shell-command/src/command_safety/is_dangerous_command.rs` (parte POSIX) | `8ab9471fe89877eed65dcae7f3abce282344c6f3a3e997d574b188ccb3dcbf23` |
| `alfalfa/permisos/denegacion.py` | `codex-rs/sandboxing/src/denial.rs` | `70ba7f805ebdc64b59147c52d9ff8a09184e6d82c4e5333309af746802892579` |
| `alfalfa/permisos/bajo_demanda.py` | Modelo `AskForApproval::OnRequest` y campos `sandbox_permissions`/`justification` del contrato de herramientas de Codex (reescritura, sin código copiado) | — |

Las pruebas `server/tests/test_alfalfa_peligrosos.py` y `server/tests/test_alfalfa_denegacion.py`
adaptan los casos de los `#[cfg(test)]` de esos dos archivos. Esa adaptación está marcada en
cada prueba.

Modificado para AlfalfaCode en noviembre de 2026 (aviso de modificación, Apache-2.0 §4.b).
Las diferencias intencionales están documentadas en la cabecera de cada módulo:

1. Toda invocación de `sudo` es peligrosa, aunque la orden interior no lo sea.
2. `env` reconoce `-u`, `--unset`, `-C`, `--chdir` y `-S`, y una opción desconocida se
   clasifica como peligrosa en lugar de tomarse por la orden.
3. La shell explícita se reconoce también con opciones agrupadas o previas y con
   posicionales. Se analiza un guion con errores de sintaxis. Como no hay tree-sitter, lo
   que el analizador propio no sabe leer (heredoc, comillas sin cerrar, `${…}` con
   sustituciones, `case` dentro de `$(…)`, o una shell o `eval` con texto dinámico) se
   clasifica como peligroso. `eval` con texto literal se analiza. Las palabras dinámicas
   conservan su posición en lugar de omitirse, y en los argumentos de `env` una dinámica
   sin comillas o que pueda ser opción u orden es peligrosa. Una barra invertida fuera de
   comillas deja literal el carácter siguiente, y `function NOMBRE { … }` analiza el cuerpo.
4. Sólo POSIX: no se portan PowerShell/Windows ni el caso SIGSYS de seccomp.
5. En `denial.rs`, un código de salida ausente no es una denegación. NativeRuntime mezcla
   stderr en stdout, y la terminal pasa esa salida como agregada.
