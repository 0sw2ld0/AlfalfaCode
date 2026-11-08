# Estilo del código

AlfalfaCode sigue una guía en sus dos partes, y la comprueba una herramienta en cada una:
SwiftLint en la app de Mac y ruff en el motor de Python. Esta página resume las reglas,
explica qué deuda queda y propone cómo partir los dos archivos más grandes del motor.

```bash
cd alfalfa-mac && swiftlint     # app de Mac
ruff check .                    # motor de Python, desde la raíz
```

Las dos órdenes salen limpias. Hay que dejarlas así antes de cada commit.

## App de Mac (Swift)

La configuración está en `alfalfa-mac/.swiftlint.yml`. Parte de la
[guía de RedMadRobot](https://github.com/RedMadRobot/RMR-swift-style-guide) y de su
`swiftlint.yml`, adaptadas a SwiftUI.

- **Líneas de 120 columnas.** Los comentarios también cuentan.
- **Archivos cortos.** Avisa a las 400 líneas por archivo, a las 250 por tipo y a las
  60 por función (la guía dice 40, pero las vistas de SwiftUI encadenan modificadores).
- **Un archivo por vista o por un grupo pequeño de vistas.** Por ejemplo,
  `Sources/AlfalfaCode/Session/` reparte la pantalla de sesión en 14 archivos.
- **Partes de cada tipo en el orden de la guía,** separadas por `// MARK: -`:
  1. Tipos
  2. Constantes
  3. Propiedades (`body` va aquí)
  4. Propiedades privadas (las vistas auxiliares, como `private var header: some View`)
  5. Inicializadores
  6. Métodos públicos
  7. Métodos privados

  `type_contents_order` comprueba este orden. Una regla propia avisa si a `MARK:` le
  falta el guion.
- **Una extensión por protocolo.** En la cabecera del tipo solo van la conformidad
  principal (`View`, el tipo crudo de un enum o la superclase) y las que no traen
  código propio (`Equatable`, `Hashable`, `Sendable`, `Identifiable`, `Codable`,
  `CaseIterable`). Cualquier otro protocolo va en su propia
  `extension Tipo: Protocolo`, encabezada por `// MARK: - Protocolo`. Así están
  `NSApplicationDelegate` y `LocalizedError`.
- **Los textos visibles van en español.** Los nombres del código siguen siendo los de
  siempre.

Se apagan dos reglas de la guía:
- `implicit_return`: la guía tampoco la exige.
- `optional_data_string_conversion`: la app usa `String(decoding:as:)` a propósito. Esa
  forma lee la salida de los procesos aunque traiga bytes que no son UTF-8, mientras que
  `String(bytes:encoding:)` perdería la línea entera.

### La deuda: `.swiftlint-baseline.json`

Los archivos que ya eran grandes antes de esta guía tienen 376 avisos, que están
apuntados en `alfalfa-mac/.swiftlint-baseline.json`. SwiftLint los reconoce por su
texto, no por el número de línea, así que solo enseña los avisos nuevos. Casi todos son
de orden de las partes (196) y de líneas largas (164). Están en AppModel (90), Timeline
(47), SidePanel (35), LiveSession (29), Protocol (29) y SlashCommands (16). El resto son
las pruebas, con JSON del protocolo en una sola línea, y unos pocos archivos pequeños.

No se corrigieron a mano para no chocar con las ramas abiertas, la de proveedores y la
del icono. Cuando se parta uno de esos archivos, se corrigen sus avisos y se vuelve a
escribir la lista:

```bash
cd alfalfa-mac && swiftlint --write-baseline .swiftlint-baseline.json
```

La lista solo debe encoger. Si un cambio la hace crecer, es que trae avisos nuevos, y
esos se corrigen.

Los siguientes archivos para partir, por tamaño:
- `AppModel.swift` (677 líneas): carga, proveedores, actualización, carpetas, sesiones y
  chats de Ollama, cada parte en una extensión de su propio archivo.
- `SidePanel.swift` (569): contexto, subagentes, procesos, entorno y cambios, una
  sección por archivo.
- `Protocol.swift` (855): los tipos del protocolo en un archivo y la decodificación de
  `AlfalfaMessage` en otro.

## Motor (Python)

ruff se configura en `pyproject.toml`, con líneas de 120 columnas y las reglas E
(pycodestyle), F (pyflakes) e I (orden de los import). Casi nada necesitó excepción:
- `runtime.py` vuelve a importar a media página la clasificación de fallos, para
  quien la busque allí (`# noqa: E402, F401`).
- `tests/conftest.py` importa pytest después de limpiar el entorno (`# noqa: E402`).

ruff encontró un fallo real. Si el ayudante de control de apps contestaba algo que no
era JSON, salía un `NameError` en vez del mensaje con su stderr. Ya está arreglado y
tiene su prueba.

## Propuesta: partir `runtime.py` y `permisos/ordenes.py`

Los dos pasan de 2.000 líneas. Esto es solo la propuesta: no se ha movido nada todavía.

### Reglas para partir sin cambiar el comportamiento

1. **El módulo viejo sigue exportando lo de siempre.** `runtime.py` y `ordenes.py` se
   quedan como fachada: importan de los módulos nuevos y vuelven a exportar los mismos
   nombres. Unos 30 archivos de pruebas hacen
   `from alfalfa.proveedores.nativo.runtime import NativeRuntime`, y `terminal.py`
   importa `SIN_EFECTOS`. Nada de eso cambia.
2. **Cuidado con los parches de las pruebas.** Un `monkeypatch.setattr(modulo, 'nombre')`
   solo hace efecto en el módulo donde vive la función que usa ese nombre. Hoy hay dos
   parches así:
   - `monkeypatch.setattr(ordenes, '_repo_del_integrador', …)` en `test_ajustes.py`;
   - `monkeypatch.setattr(NativeRuntime, 'request', …)`.

   Cada uno se cambia en el mismo commit que mueve la función a la que apunta.
3. **Un commit por cada trozo que se mueve,** con las pruebas en verde después de cada
   uno. Se copia el código tal cual: mover y reescribir no van en el mismo commit.

### `alfalfa/proveedores/nativo/runtime.py` (2.401 líneas)

Casi todo es una sola clase, `NativeRuntime` (2.062 líneas y unos 80 métodos). Se
propone partirla en *mixins*, clases sin estado propio que agrupan métodos. Así
`NativeRuntime` conserva su nombre, su `__init__` y los mismos atributos, y cada mixin
vive en su archivo dentro de `alfalfa/proveedores/nativo/runtime/`:

| Archivo nuevo | Qué se lleva | Líneas aprox. |
|---|---|---|
| `entorno.py` | `is_cloud`, `perfil`, `limite_num_predict`, `manual_del_harness`, `entorno`, `podar_sesiones_nativas` (funciones sueltas de arriba del archivo) | 250 |
| `fallos.py` | `_cadena_de_fallo`, `_es_fallo_tls`, `_texto_de_fallo`, `_es_desconexion_sin_respuesta`, más los métodos `_texto_de_error`, `_provider_error_text`, `_provider_failure` y `_retry_*`, `_log_transport_*` | 200 |
| `peticion.py` | `endpoint`, `_cliente`, `_proveedor_actual`, `request` (125 líneas) | 180 |
| `contexto.py` | `context`, `_context_messages`, `_estado_breve`, `_archivos_propios`, `user_message`, `_desglose`, `context_estimate`, `_record_context_details`, `_con_imagenes`, `_conversation_response_cap`, `_record_response_usage` | 300 |
| `compactacion.py` | `compact`, `_compact_codex`, `compact_now`, `_compaction_event`, `_codex_compact_due`, `_ventana` | 230 |
| `traspaso.py` | `_legacy_handoff_fingerprint`, `_inherit_handoff_uncertain_operations`, `_matching_…`, `_record_handoff_*` | 270 |
| `vision.py` | `vision`, `_vision`, `_puede_ver`, `_vision_pregunta`, `_vision_propia`, `_vision_delegada` | 90 |
| `herramientas.py` | `approve`, `pending` (89 líneas), `_call_with_hooks`, `_hooks_*`, `_tool_identity`, `_reject_completed_recovery_tool`, `reject_conversation_tool` | 280 |
| `revision.py` | `_review_closing_context`, `_close_review`, `_validate_review_result` | 130 |
| `bucle.py` | `run` (362 líneas), `_redirigible`, `system_instructions`, `default_engine_text` | 460 |

`runtime.py` queda así:

```python
class NativeRuntime(BucleMixin, HerramientasMixin, ContextoMixin, CompactacionMixin,
                    PeticionMixin, FallosMixin, TraspasoMixin, VisionMixin, RevisionMixin):
    def __init__(...): ...   # igual que hoy
    def save(self): ...
```

`run` es el método más largo de todo el motor, con 362 líneas. Se moverá entero a
`bucle.py` y, en un paso aparte, se partirá en pasos con nombre: preparar el turno,
pedir al modelo, aplicar las herramientas y cerrar el turno. Antes de tocarlo hacen falta
pruebas que cubran las salidas del bucle: redirigir, presupuesto agotado y revisión de
cierre.

### `alfalfa/permisos/ordenes.py` (2.097 líneas)

Son funciones sueltas alrededor de `safe_command` y de `_analizar` (369 líneas), que
decide una orden ya troceada. Se propone convertirlo en un paquete,
`alfalfa/permisos/ordenes/`, con un archivo por tema:

| Archivo nuevo | Qué se lleva | Líneas aprox. |
|---|---|---|
| `tablas.py` | constantes: `LECTURA`, `SUBCOMANDOS`, `GIT_ESCRITURA`, `BANDERAS_PROHIBIDAS`, `HERRAMIENTAS`, `CACHES`… | 140 |
| `shell.py` | `desenvolver`, `partir_en_y` (105), `_sustitucion_real`, `_llaves_que_expanden`, `_quitar_entorno` | 230 |
| `espacio.py` | rutas y worktree: `_fuera_del_espacio`, `fuera_del_espacio`, `_es_cache`, `_bajo_cache`, `_es_evidencia`, `_carpeta_de_evidencia`, `_salida_a_evidencia`, `_temporal_del_ticket`, `_sin_seguimiento`, `_es_propio`, `_ruta_instalada`, `_en_carpeta_del_sistema` | 230 |
| `referencias.py` | `carpetas_de_referencia`, `_lectura_de_referencia`, `rutas_citadas`, `_con_espacios`, `carpeta_de_lectura`, `ruta_leida` | 130 |
| `confianza.py` | `programa_de_confianza`, `_repo_del_integrador`, `_venv_del_integrador`, `_herramienta_del_proyecto`, `_ramas_de_avance` | 110 |
| `casos.py` | los casos especiales, cada uno con su `(veredicto, motivo)` o `None`: `_xcode`, `_resultados_de_xcode`, `_sdk_de_xcode`, `_borrado_de_cache`, `_copia_dentro`, `_copia_de_referencia`, `_extraccion_a_cache`, `_leer_robots`, `_captura_sin_ventana`, `_ejecutable_construido`, `_blender_sin_ventana`, `_salida_de_compilacion_temporal`, `_sed_que_solo_imprime`, `_xxd_solo_muestra`, `rg_ejecuta` | 420 |
| `procesos.py` | `_kill`, `_ps`, `_directorio_de`, `_ejecutable_de`, `_programa_ajeno`, `_diagnostico`, `_vm_de_ensayo`, `_ssh_de_vm_de_ensayo`, `_orden_de` | 170 |
| `python.py` | `python_que_solo_lee`, `_modo_de_lectura`, `python_en_linea_seguro` | 80 |
| `analisis.py` | `_analizar`, `safe_command`, `safe_file_change`, `_rutas`, `_destino_de_renombrado` | 520 |

`ordenes/__init__.py` vuelve a exportar todo lo que se usa desde fuera:
- para `runtime.py`: `safe_command`, `python_en_linea_seguro` y `rutas_citadas`;
- para los integradores: `fuera_del_espacio`, que cita `ajustes.py`;
- para las pruebas: `rg_ejecuta`, `carpetas_de_referencia`, `_repo_del_integrador` y
  `_venv_del_integrador`.

`_analizar` es una cadena de casi 60 `if programa == …`. Después de moverla, el paso
natural es una tabla, `{programa: función que decide}`, con una función por programa
(git, python, node, mkdir, colima…). El orden de los `if` importa, porque varios casos
se solapan. Por eso la tabla solo debe recoger los casos que no se pisan, y antes de
tocarla conviene una prueba que pase la lista de órdenes de `tests/` por `safe_command`
y guarde los veredictos.

### Orden recomendado

1. `ordenes.py`. Son funciones sin estado, así que moverlas es lo más seguro.
2. `runtime.py`, con las funciones sueltas primero y los mixins después, uno por commit.
3. Partir `run` y `_analizar` en piezas con nombre, cada uno con sus pruebas nuevas.
