# Uso de AlfalfaCode

## Control desde aplicaciones: JSONL versión 2

`alfalfa --stdio "encargo"` y `alfalfa --stdio --reanudar ID` usan el mismo
TerminalManager y NativeRuntime que la terminal. Ejecutar desde el cwd original.
stdout contiene exclusivamente mensajes JSON UTF-8, uno por línea; diagnósticos
van a stderr. Cada salida incluye `version: 2` y `tipo`. Ejemplos:

```jsonl
{"version":2,"tipo":"sesion","id":"abc","cwd":"/repo","modelo":"deepseek-v4.1-flash:cloud","esfuerzo":"medium"}
{"version":2,"tipo":"evento","rol":"assistant","texto":"Archivo corregido","hora":1793900000.0}
{"version":2,"tipo":"permiso","id":"p1","argv":["rm","-rf","basura"],"proposito":"Borrar la carpeta desechable","motivo":"peligroso","motivos":["peligroso"],"regla":"rm con opción de fuerza","intento":"e96a56831f6d4758"}
{"version":2,"tipo":"permiso_resuelto","id":"p1","aprobado":true,"motivo":"Respuesta del cliente"}
{"version":2,"tipo":"error","texto":"Mensaje inválido o id de permiso desconocido o repetido."}
{"version":2,"tipo":"evento","rol":"user","texto":"Usa también el caso negativo","hora":1793900001.0}
{"version":2,"tipo":"contexto","tokens":28400,"limite":262144}
{"version":2,"tipo":"turno_terminado","estado":"completed","resumen":"Archivo corregido","codigo_salida":0,"tokens":100}
{"version":2,"tipo":"fin","estado":"interrupted","resumen":"Sesión interrumpida; puedes reanudarla.","codigo_salida":130,"tokens":100}
```

`contexto` se emite tras cada inferencia con los tokens del prompt y el límite de
contexto del modelo. Con el servicio Alfalfa se emite además `cuota` cuando cambia
el % usado del mes:

```jsonl
{"version":2,"tipo":"cuota","servicio":"alfalfa","cuotas":[{"proveedor":"openai","nombre":"GPT-6 Luna","usado_pct":12.5,"renueva":"2026-12-01"}]}
```

Los clientes deben ignorar los `tipo` y los campos que no conozcan.
`--solo-lectura` no ofrece al modelo `write_file`, `edit_file`, `run_command` ni
`run_checks` (el motor también las rechaza).

Entradas (una línea por mensaje):

```jsonl
{"tipo":"permiso","id":"p1","aprobado":true}
{"tipo":"permiso","id":"p2","aprobado":false}
{"tipo":"mensaje","texto":"Usa también el caso negativo"}
{"tipo":"cancelar"}
```

Novedad de la versión 2, `mensaje`: el texto del usuario se encola y entra en el
historial justo antes de la siguiente llamada al modelo, después de registrar los
resultados de las herramientas. No interrumpe la orden en curso ni responde a un
permiso pendiente; si llegan varios, se entregan en orden. Al entrar en el
historial se emite `evento` con `rol: "user"`. `texto` debe ser una cadena con
algún carácter no blanco y como máximo 20 000 caracteres (puntos de código);
lo contrario produce `error` y la sesión sigue utilizable.

Cada turno acaba con `turno_terminado` (mismos campos que `fin`) y el proceso
**sigue abierto**: un `mensaje` válido reanuda la sesión como un turno nuevo, con
el historial completo, tanto en una sesión nueva como en una de `--reanudar`.
`cancelar` o EOF sin turno en curso cierran con el estado y el código del último
turno; con un turno en curso lo interrumpen (130). Los mensajes aún no
entregados al cerrar se descartan. Cambio frente a v1: el proceso ya no termina
solo al completar el encargo; el cliente cierra stdin (o envía `cancelar`) cuando
no quiere más turnos.

Sólo el booleano JSON `true` concede; `1`, cadenas, campos ausentes y null
deniegan. IDs desconocidos o repetidos y JSON inválido producen `error` y
permiten continuar. Cada permiso tiene ID único y una resolución. Con la
política on-request (ver abajo) sólo llegan permisos de excepciones; `--auto`
no concede ninguno y todos esperan al cliente antes de ejecutar nada.

Campos del evento `permiso` (opcionales; un cliente que no los conozca los
ignora y el permiso sigue siendo válido): `motivo` (`peligroso`, `red`,
`salir_sandbox` o `reintento`), `motivos` (todos los de esa única decisión, el
principal primero), `justificacion` (la que declaró el modelo), `regla` (la
regla peligrosa que coincidió), `intento` (identificador de este intento),
`reintento_de` (el intento fallido que se repetiría), `red: true`,
`sin_sandbox` (por qué se saldría del sandbox) y `fallo_sandbox` (error de
Seatbelt cuando no arrancó). Los resultados de herramientas añaden `sandbox`
(`seatbelt` o `none`), `intento`, `permisos`, `red` y, tras un bloqueo,
`denegacion_probable`, `reintento` o `intento_previo`. Cancelación, Ctrl-C y EOF niegan
pendientes, interrumpen el motor, terminan los grupos de procesos y guardan el
estado. `fin` aparece exactamente una vez, al final: códigos 0 completado,
1 fallo/bloqueo, 130 interrupción. Aceptar una orden no significa que haya acabado.

Consultas independientes:

```sh
alfalfa --sesiones --json
alfalfa --modelos --json
```

```jsonl
{"version":2,"tipo":"sesiones","sesiones":[{"id":"abc","encargo":"Corregir","cwd":"/repo","estado":"interrupted","modelo":"deepseek-v4.1-flash:cloud","esfuerzo":"medium","fecha":1793900000.0,"tokens":100}]}
{"version":2,"tipo":"modelos","modelos":[{"name":"deepseek-v4.1-flash:cloud","proveedor":"ollama","nombre":"deepseek-v4.1-flash:cloud"},{"name":"alfalfa/gpt-6-luna","proveedor":"alfalfa","nombre":"GPT-6 Luna - Alfalfa"}]}
{"version":2,"tipo":"error","texto":"No se pudo consultar Ollama o las sesiones de AlfalfaCode."}
```

`modelos` junta los de cada proveedor configurado: `name` es el que se guarda en la
sesión (con prefijo `openai/` o `alfalfa/` fuera de Ollama) y `nombre` el visible.
Si un proveedor no responde, los demás se listan igual y su fallo va en `fallos`.

Proveedores (las claves y el código se leen por la entrada estándar, nunca en los
argumentos):

```sh
alfalfa --proveedores --json
printf '%s\n' "$CLAVE" | alfalfa --guardar-clave openai --json
alfalfa --fijar-url alfalfa https://servicio.example.com --json
printf '%s\n' CUY-XXXX-XXXX-XXXX | alfalfa --activar-codigo --json
alfalfa --cuota --json
```

```jsonl
{"version":2,"tipo":"proveedores","por_defecto":"ollama","proveedores":[{"id":"ollama","nombre":"Ollama","configurado":true,"url":"http://127.0.0.1:11434","clave":false},{"id":"openai","nombre":"OpenAI","configurado":false,"clave":false},{"id":"alfalfa","nombre":"Alfalfa","configurado":false,"url":null,"clave":false}]}
{"version":2,"tipo":"activado","servicio":"alfalfa","nombre":"Ana","caduca":null,"modelos":["gpt-6-luna"],"cuotas":[]}
```

El código de cortesía queda atado a este Mac: `--activar-codigo` crea una clave en
el Secure Enclave (`alfalfa/proveedores/firmante.swift`, compilado con `swiftc` la
primera vez) y cada petición al servicio va firmada con ella.

Las sesiones se ordenan por fecha descendente; una sesión ilegible se identifica
con `estado: "corrupta"` y campos desconocidos nulos. Ollama inaccesible produce
error JSON y código 1. Estas consultas no emiten `fin` (no ejecutan sesiones).

`tests/test_alfalfa_stdio.py` cubre mensajes, permisos, las cuatro rutas de
interrupción (orden/espera × cancelar/EOF) y, en v2, mensajes durante una orden
en curso, tras terminar un turno, vacíos y de más de 20 000 caracteres.

Desde una carpeta de trabajo:

```sh
/ruta/al/repositorio/scripts/alfalfa "Corrige el cálculo de la suma"
alfalfa --modelo deepseek-v4.1-flash:cloud --esfuerzo low "encargo"
alfalfa --auto "Consulta el estado del proyecto"
alfalfa --reanudar ID
```

Requiere el `.venv` Python del repositorio con las dependencias de `server`,
y Ollama iniciado con un modelo que declare herramientas y contexto de al menos
8192 tokens. Los modelos Cloud requieren iniciar sesión con el flujo oficial de
Ollama y disponer de acceso. No instala componentes. Lee `ALFALFA_OLLAMA_URL`
(por defecto `http://127.0.0.1:11434`) y `OLLAMA_API_KEY`, si el servicio la
requiere, o la clave guardada en el Llavero con `--guardar-clave ollama`. Con un modelo con visión, `inspect_image`
mira las imágenes con el mismo modelo.

`web_search` y `read_web` buscan y leen la web con la librería
[CuySearch](https://github.com/0sw2ld0/CuySearch), desde tu conexión. Lo hace el
proceso de AlfalfaCode: el sandbox de las órdenes sigue sin red, y `read_web`
rechaza localhost y las redes privadas. El buscador se elige en Ajustes ▸ General
de la app, con `alfalfa --buscador MODO` o con `ALFALFA_BUSCADOR`:

- `auto` (por defecto): DuckDuckGo, Bing, Brave, Mojeek y Wikipedia, en ese orden;
- `ollama`: Ollama Web Search con tu clave de Ollama y, si falla, los anteriores;
- `searxng`: tu SearXNG (por defecto `http://127.0.0.1:8888`, el de CuySearch
  Manager; otro con `alfalfa --fijar-searxng URL`) y, si falla, los anteriores;
- `apagado`: los agentes no reciben las herramientas web.

Si un motor bloquea o falla, se pasa al siguiente. Si falta la librería (por ejemplo,
tras un `git pull` sin reinstalar), los agentes no reciben las herramientas web:
`alfalfa --instalar-buscador` o «Instalar ahora» en Ajustes la instala. Con `ALFALFA_CUYSEARCH_URL` se usa
en su lugar un servicio CuySearch remoto (`ALFALFA_CUYSEARCH_KEY` es su clave).

`--modelo` selecciona el modelo (por defecto `deepseek-v4.1-flash:cloud`).
`--esfuerzo` acepta `low`, `medium` (predeterminado), `high`, `xhigh` y `max`.
Al reanudar se conservan modelo y esfuerzo salvo que se indiquen otros.
El lanzador conserva la carpeta actual y utiliza el `.venv` del repositorio;
en worktrees utiliza el del repositorio compartido.

## Permisos on-request

`run_command` y `run_checks` siguen la política on-request de Codex
(`alfalfa/permisos/bajo_demanda.py`), que sólo activa `TerminalManager`
y por tanto `alfalfa`, `alfalfa --stdio` y Alfalfa Mac por igual. Los agentes de
un integrador que use `NativeRuntime` directamente no la usan. Cada orden corre en el sandbox Seatbelt: escribe sólo en la
carpeta de trabajo, `$TMPDIR` y `/private/tmp`, `.git` es de solo lectura y no
hay red. **Una orden normal se ejecuta sin preguntar.** Cada intento tiene como
máximo una decisión del usuario, que reúne todos sus motivos; lo que concede
es el alcance del más amplio (reintento > salir del sandbox > red > peligroso),
y así lo dicen la pregunta de la terminal y la tarjeta de Alfalfa Mac. Hay
cuatro:

| Motivo | Cuándo | Qué concede aprobar |
|---|---|---|
| `peligroso` | `rm` con `-f`/`--force` (también `-rf`, `-fr` o `-vf`, siempre antes de `--`), cualquier `sudo`, y las mismas órdenes tras `env`, `trap`, `eval` o dentro de `bash`/`sh`/`zsh -c`. También lo que no se puede analizar: más de 8 envoltorios anidados, heredocs, comillas sin cerrar o una shell con guion dinámico, y una palabra dinámica (`$X`, glob) sin comillas o en posición de opción u orden entre los argumentos de `env`. | Ejecutarla **dentro** del sandbox, sin red. No abre la red ni permite salir. |
| `red` | El modelo declara `needs_network: true`. | Esa orden corre con la variante de red de Seatbelt. Las restricciones de escritura y de `.git` no cambian. |
| `salir_sandbox` | El modelo pide `sandbox_permissions: "require_escalated"` con una `justification` de 10 a 500 caracteres. Sin justificación válida se rechaza antes de preguntar. También cuando Seatbelt falta o no arranca. | Ejecutar esa orden **sin sandbox**, con los permisos del usuario y con red. |
| `reintento` | Un intento en el sandbox falló y parece una denegación: código distinto de 0 y una palabra indicadora (`operation not permitted`, `permission denied`, `read-only file system`, `sandbox`…) en la salida. Se revisan las palabras antes que los códigos 2, 126 y 127, como en `denial.rs`. Un fallo ordinario, un plazo vencido o una salida excesiva no lo provocan. | Repetir **una vez** y sin sandbox exactamente el mismo argv. El intento fallido queda registrado con su resultado; el nuevo lleva `reintento_de` y nunca vuelve a reintentarse. |

La clasificación de órdenes peligrosas es un port POSIX de
`is_dangerous_command.rs` (`permisos/peligrosos.py`), y la de denegaciones, de
`denial.rs` (`permisos/denegacion.py`). Ambos módulos documentan sus diferencias
intencionales con Codex, que siempre son más conservadoras: todo `sudo` es
peligroso, `env -u` no oculta la orden y lo que no se puede analizar pregunta.

En la terminal cada solicitud muestra el motivo, la orden, el propósito y, si
los hay, la regla, la justificación y el intento fallido. Después pregunta
`[s/N]`, y sólo `s`, `si` o `sí` conceden ese intento. Sin entrada **y salida**
de terminal interactivas se deniega de inmediato, aunque stdin contenga una
respuesta afirmativa. En `--stdio` el motivo viaja en el evento `permiso`, y
Alfalfa Mac lo muestra en la tarjeta de permiso junto con el alcance de la
aprobación. Rechazar, cancelar, cerrar stdin (EOF), responder algo que no sea
el booleano `true` o usar un id desconocido o repetido nunca ejecuta la orden.
Las peticiones para salir del sandbox se deniegan solas a los 10 minutos.

`--auto` conserva su lista cerrada de lecturas (`pwd`, `git status`, `git diff
--no-ext-diff --no-textconv` con opciones de consulta, `rg --files` y usos
limitados de `cat`, `head`, `wc` y `ls`), pero ya no le hace falta: esas
órdenes corren sin preguntar. **`--auto` nunca concede una excepción.** Además,
los procesos se lanzan con `core.fsmonitor` desactivado para que una lectura de
git no ejecute hooks.

Si `/usr/bin/sandbox-exec` no existe, el sistema no es macOS o la sonda
`/usr/bin/true` no arranca con la política, no hay alternativa silenciosa. La
orden pide `salir_sandbox` con el error de Seatbelt, y negarla no lanza nada.

`--solo-lectura` no cambia: no ofrece al modelo `write_file`, `edit_file`,
`run_command` ni `run_checks`, y el motor las rechaza aunque lleguen con los
campos on-request.

**Las ediciones mediante herramientas de archivos dentro de la carpeta no piden
confirmación. `--auto` no limita esas ediciones.** Las instrucciones prohíben
modificar fuera de la carpeta y hacer commit o push por iniciativa propia.
Las herramientas de archivos rechazan rutas externas, privadas y enlaces.

Las sesiones se guardan en `~/.alfalfa/sesiones/ID` o
`$ALFALFA_HOME/sesiones/ID`, con directorios `0700` y registros `0600`.
Contienen encargo, archivos anteriores a las ediciones y salidas de herramientas:
protégelos como datos del proyecto. El arnés necesita escribir allí además de
en la carpeta de trabajo.

Ctrl-C o SIGTERM interrumpen, guardan el estado y devuelven `130`.
Desde la carpeta original, `alfalfa --reanudar ID` continúa el diario existente.
Una operación interrumpida de resultado incierto se entrega al mecanismo de
recuperación y no se repite automáticamente; las órdenes inciertas requieren
inspección manual. Una sesión sólo puede tener un proceso activo. Código `0`
significa finalización correcta; fallos, rechazos o bloqueos devuelven `1`;
errores de argumentos devuelven `2`.

Para invocarlo por nombre, puedes crear tú un enlace en un directorio que **ya**
esté en tu PATH, por ejemplo `ln -s /ruta/al/repositorio/scripts/alfalfa
~/.local/bin/alfalfa`. El lanzador resuelve enlaces simbólicos. Este cambio no
crea el enlace, modifica el PATH ni instala nada fuera del repositorio.
