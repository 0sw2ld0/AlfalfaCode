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

`{"tipo":"mantener_despierto","activo":false}` apaga (o, con `true`, enciende) ya la
aserción que impide que el Mac se duerma por inactividad mientras trabaja un turno; al
arrancar decide `--sin-mantener-despierto` o `ALFALFA_MANTENER_DESPIERTO=0` (por defecto,
encendida). Cada vez que el arnés la toma o la suelta de verdad emite
`{"version":2,"tipo":"despierto","activo":true|false}`; si macOS no la concede, no se
emite nada. Siempre se suelta antes de `turno_terminado`.

Si la instantánea para deshacer del turno tarda más de un segundo (o una herramienta que
cambia archivos tiene que esperarla), el arnés emite
`{"version":2,"tipo":"preparando","texto":"Preparando deshacer… (8306 archivos, 1,2 GB)"}`
y, al terminar, `{"version":2,"tipo":"preparando","texto":null}`. La app lo enseña en la
fila viva en lugar de «Pensando».

Novedad de la versión 2, `mensaje`: el texto del usuario se encola y entra en el
historial justo antes de la siguiente llamada al modelo, después de registrar los
resultados de las herramientas. No interrumpe la orden en curso ni responde a un
permiso pendiente; si llegan varios, se entregan en orden. Al entrar en el
historial se emite `evento` con `rol: "user"`. `texto` debe ser una cadena con
algún carácter no blanco y como máximo 20 000 caracteres (puntos de código);
lo contrario produce `error` y la sesión sigue utilizable.

Un mensaje que abriría un turno con imágenes (`imagenes`) para un modelo que no las ve,
sin visión delegada que las describa, no abre turno: llega `error` con
`motivo: "sin_vision"`, `rechazado` (`texto` tal como lo escribió el usuario, sin etiquetas
y vacío si sólo traía imágenes, e `imagenes`, para devolverlo al cuadro) y `con_vision`
(`modelo` y `nombre` de los que sí las ven; vacío si la lista de modelos no llegó en 3 s),
y la sesión espera el siguiente mensaje. Si llega con un turno en marcha cuyo modelo no
las ve, no entra en ese turno: espera en la cola y abre el siguiente, ya con el modelo que
se haya elegido entretanto, y ahí se comprueba:

```json
{"version":2,"tipo":"error","texto":"El modelo glm-5.3:cloud no ve imágenes, así que no se pueden adjuntar. Cambia a uno que sí las ve, como qwen3-vl:8b, o quita las imágenes.","motivo":"sin_vision","rechazado":{"texto":"¿Qué falla?","imagenes":["/ruta/captura.png"]},"con_vision":[{"modelo":"qwen3-vl:8b","nombre":"qwen3-vl:8b"}]}
```

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
sesión (con prefijo `openai/`, `anthropic/`, `alfalfa/` o el del compatible, `openrouter/…`, fuera
de Ollama) y `nombre` el visible.
Si un proveedor no responde, los demás se listan igual y su fallo va en `fallos`.
Del catálogo de models.dev cada fila puede traer `ventana`, `salida_max`, `precio`
(`entrada` y `salida` en USD por millón de tokens), `razona`, `esfuerzos`,
`herramientas` y `vision`. Toda fila lleva `vision`: true si ve imágenes, false si no y
null si no se sabe (sale de `/api/show` en Ollama, del propio proveedor, del servicio
Alfalfa o del catálogo). Los gratis ahora llegan aunque su proveedor no tenga
clave, con `gratis: true`, el `aviso` de datos, `nombre_proveedor` y, si falta la
clave, `falta_clave` (`variable`, `donde`, `texto`).
Las filas de OpenAI y Anthropic llevan `recomendado`: true para los recomendados
(los propios y los añadidos a mano en `proveedores.json`), que van primero, y false
para el resto de modelos de la cuenta («Más modelos» en la app).

`turno_terminado`, `fin`, `historial` y cada fila de `sesiones` añaden `coste` (USD
estimados de la sesión con los precios del catálogo) cuando el modelo tiene precio;
`turno_terminado` trae también `tokens_turno` y `coste_turno`. Sin precio conocido
no aparece ningún coste: 0 sería «gratis», no «no se sabe».

El evento `sesion` trae también `tokens` (los de toda la sesión) y, con precio,
`coste`: al reanudar, el cliente enseña ya lo gastado sin esperar a que acabe el
primer turno.

Con caché de prompts (Anthropic, OpenAI), esos mismos eventos añaden `cache`:
`leidos` y `escritos` (tokens) y, con precio, `sin_cache`, `con_cache` y `ahorro`
(USD de la sesión; `ahorro` es negativo mientras escribir en la caché cueste más de lo
que ahorra leerla).

Diagnósticos de Swift: `sourcekit-lsp` es el único servidor de lenguaje que corre fuera
del sandbox, así que viene apagado, como el LSP de OpenCode. Los demás diagnósticos
(pyright, ruff, `typescript-language-server`, ESLint y SwiftLint, todos en el sandbox)
siguen igual. Se enciende con `--diagnosticos-swift`, `ALFALFA_DIAGNOSTICOS_SWIFT=1`,
`{"diagnosticos_swift": true}` en el `ajustes.json` del usuario o, en vivo,
`{"tipo":"diagnosticos_swift","activo":true}`. El `.alfalfa/ajustes.json` del proyecto sólo
puede apagarlo. Encendido, su permiso llega con `motivo` `diagnosticos_swift` y `sin_sandbox`:
es una función de AlfalfaCode, no una acción del agente, y negarlo sólo apaga los
diagnósticos de Swift el resto de la sesión, sin volver a preguntar. Apagado, la primera
edición de un `.swift` trae en `diagnosticos.sin_respuesta` una línea que recuerda
comprobarlo con `swift build` o `swift test`.

Contenido externo: después de leer la web (`read_web`, `web_search`), el navegador, un
servidor MCP, otra app, sesiones antiguas o un adjunto, la sesión queda marcada (también
al compactar o reanudar). Como en Claude Code y Codex, sólo lo arriesgado pide entonces
una aprobación extra (`motivo` o `motivos` con `contenido_externo`):

- sin preguntar: editar o crear archivos dentro de la carpeta (`write_file`, `edit_file`,
  `apply_patch`) y las órdenes que corren en el sandbox sin red y no son peligrosas;
- con el sí extra: lo que usa red o sale del sandbox (una sola tarjeta, con `red`,
  `salir_sandbox` o `reintento` y `contenido_externo`), lo que lee o toca secretos
  (`.env`, claves, variables como `*_TOKEN`) o rutas de fuera de la carpeta, las
  herramientas MCP y `spawn_agent`, `send_input`, `close_agent` y `app_action`.

Las órdenes peligrosas preguntan como siempre. El permiso trae `para_sesion` («Permitir en
esta sesión», con una clave propia: lo aprobado antes de leer contenido externo no vale) y,
en una edición, `diff` en formato unificado (`--- a/…`, `+++ b/…`) en vez de la llamada.

Presupuesto: `--presupuesto USD` y `--presupuesto-mes USD` (o `ALFALFA_PRESUPUESTO` y
`ALFALFA_PRESUPUESTO_MES`) ponen los topes de la sesión y del mes en este Mac.
`{"tipo":"presupuesto","sesion":5,"mes":null}` los cambia ya, también a mitad de turno
(null o 0 quitan el tope; sin la clave, ese tope no cambia). El arnés emite

```json
{"version":2,"tipo":"presupuesto","sesion":{"tope":5.0,"gastado":4.1},"mes":{"tope":null,"gastado":12.3,"mes":"2026-11"},"aviso":"sesion"}
```

al abrir la sesión y al acabar cada turno si hay un tope, gasto este mes o caché; al
cambiar los topes; con `aviso` al pasar del 80 % (el texto del aviso no llega además
como `evento`: queda en la transcripción), y con `agotado` cuando un tope detiene una
petición. El turno termina con `estado: "blocked"` y el motivo en `resumen`, que es el
único texto del corte. También lleva `cache` si la hay.

Proveedores (las claves y el código se leen por la entrada estándar, nunca en los
argumentos):

```sh
alfalfa --proveedores --json
printf '%s\n' "$CLAVE" | alfalfa --guardar-clave openai --json
alfalfa --fijar-url alfalfa https://servicio.example.com --json
printf '%s\n' CUY-XXXX-XXXX-XXXX | alfalfa --activar-codigo --json
alfalfa --cuota --json
alfalfa --primera-vez --json
alfalfa --conectar openrouter --json
alfalfa --conectar openrouter --solo-clave --json
```

```jsonl
{"version":2,"tipo":"proveedores","por_defecto":"ollama","proveedores":[{"id":"ollama","nombre":"Ollama","configurado":true,"url":"http://127.0.0.1:11434","clave":false},{"id":"openai","nombre":"OpenAI","configurado":false,"clave":false},{"id":"alfalfa","nombre":"Alfalfa","configurado":false,"url":null,"clave":false}]}
{"version":2,"tipo":"activado","servicio":"alfalfa","nombre":"Ana","caduca":null,"modelos":["gpt-6-luna"],"cuotas":[]}
```

La primera vez, sin ningún proveedor (ni claves, ni proveedor elegido, ni Ollama en el
Mac), `--primera-vez` da los gratis con los que empezar. `--conectar openrouter` hace
el OAuth de OpenRouter en el navegador:

1. Emite `conectando` con la URL que abrió, por si el navegador no se abre.
2. Al autorizar, guarda la clave en el Llavero sin emitirla.
3. Sólo si no había ningún proveedor (la bienvenida), deja OpenRouter por defecto con
   su gratis recomendado. Si ya había uno, o con `--solo-clave` (la hoja de la clave
   de la app), no cambia ni el proveedor ni el modelo por defecto, y `modelo` va `null`.
4. Emite `conectado`, con los mismos campos que `proveedores`.

```jsonl
{"version":2,"tipo":"primera_vez","sin_proveedor":true,"aviso":"Gratis: puede usar tus datos para entrenar","proveedores":[{"id":"openrouter","nombre":"OpenRouter","conectar":true,"donde":"https://openrouter.ai/settings/keys","variable":"OPENROUTER_API_KEY","recomendado":"openrouter/nvidia/nemotron-3.5-super-vl:free","modelos":[{"name":"openrouter/nvidia/nemotron-3.5-super-vl:free","nombre":"Nemotron 3.5 Super VL (free)","ventana":1000000,"vision":true}]}]}
{"version":2,"tipo":"conectando","proveedor":"openrouter","url":"https://openrouter.ai/auth?callback_url=http%3A%2F%2Flocalhost%3A51423%2Falfalfa%2Fopenrouter&…"}
{"version":2,"tipo":"conectado","proveedor":"openrouter","modelo":"openrouter/nvidia/nemotron-3.5-super-vl:free","por_defecto":"openrouter","proveedores":[…]}
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
tras un `git pull` sin reinstalar), los agentes no reciben las herramientas web. La app
de Mac la instala sola al abrirse; en la terminal, `alfalfa --instalar-buscador`. Con `ALFALFA_CUYSEARCH_URL` se usa
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

## Contenido externo y secretos

En el motor nativo (Ollama y las API), los resultados de archivos, web, MCP y
subagentes llegan al modelo con su origen y la marca «datos, no instrucciones».

Sólo lo que llega de fuera del proyecto activa además la protección: la web,
el navegador, los resultados de servidores MCP, la pantalla de otras apps,
sesiones antiguas y los archivos adjuntos. Leer, listar o buscar en el propio
proyecto (incluido su AGENTS.md) y ejecutar órdenes en él no la activan, como
en Codex y Claude Code. Un subagente sólo la activa si él mismo leyó contenido
externo. La marca no intenta detectar frases maliciosas: después de esas
lecturas, cada orden, edición o acción con efectos pide una aprobación puntual
adicional. La sesión conserva esta procedencia al reanudar, compactar y delegar.

La tarjeta «Contenido externo» explica qué lectura motivó la pregunta. Aprobarla
sólo autoriza esa llamada: siguen vigentes las reglas del usuario, el sandbox,
el permiso de red y las rutas protegidas. No se puede guardar esta aprobación
para toda la sesión. En «Auto», las órdenes que el arnés reconoce como
inofensivas (`git status`, `pwd`, `--version`…) no la necesitan. «Omitir
permisos» respeta tu elección y no pregunta, pero deja escrito en la
conversación qué lectura externa había. Memoria y habilidades incorporan el
motivo en su tarjeta habitual, con el diff completo y siempre con pregunta.

Antes de cada petición al modelo, incluida visión, recuperación y compactación,
se tapan los formatos conocidos de claves y tokens (también JWT y claves privadas),
los campos sensibles y las credenciales ya presentes en memoria o en el entorno.
Además se usan los valores literales de `.env` y `.env.*` del proyecto, también
los de subcarpetas, sin enviarlos al modelo ni guardarlos en la procedencia.
La revisión omite dependencias, carpetas privadas y enlaces simbólicos; no lee el
Llavero. Se limita a 5.000 carpetas, 100 archivos `.env` y 256 KB por archivo; si
no puede revisar un `.env` regular o supera esos topes, detiene la petición.
Un secreto sin formato reconocible y que no está en esas fuentes puede pasar.
Los archivos y el historial local conservan su contenido; se sanea una copia al
salir al proveedor, manteniendo el modelo, los identificadores y las huellas.

## Revisión con otros ojos

La tarjeta al terminar un turno ofrece «Revisar cambios». Elige otro modelo de
los disponibles y pulsa el botón para lanzar un subagente de una sola respuesta.
No se inicia automáticamente ni reutiliza el modelo que hizo el turno (cambiar
sólo de pasarela o de Ollama local a cloud tampoco cuenta como otro modelo).

El revisor recibe el diff entre las instantáneas del principio y final del turno,
marcado como datos y sin secretos reconocibles. Las ediciones que hagas después
no entran en esa revisión. No recibe la conversación completa ni herramientas:
no puede ejecutar órdenes, abrir otros archivos ni aplicar sus propuestas.
Los binarios, enlaces y archivos excluidos se indican como no revisados; un diff
de más de 40 archivos o 60.000 caracteres se rechaza antes de pedir inferencia.
También se comprueba que quepa completo en el contexto del modelo elegido.

Los hallazgos aparecen en la misma tarjeta con prioridad, ruta, línea y detalle.
«Corregir hallazgos en este hilo» envía un encargo al agente principal, que ya
tiene el informe en su contexto como datos externos. Las correcciones conservan
los permisos habituales. El informe y el estado se guardan con el cierre.

La revisión comparte el presupuesto de sesión y mes: reserva entrada y salida
máxima, cuenta el uso del otro modelo y no arranca si no cabe en los topes.
Puedes cancelarla o enviar un nuevo mensaje para detenerla. Si se interrumpe una
petición ya enviada, o el proveedor responde sin métricas de uso, se cuenta un coste máximo conservador,
que la tarjeta señala como estimado; así cancelar no permite saltarse el tope.

## Cola de encargos

En «Nuevo encargo» o en la cabecera de una sesión, «Cola de encargos» permite
preparar hasta 20 tareas completas y ordenarlas antes de guardar. La cola queda
lista y empieza sólo al pulsar «Iniciar cola». Cada tarea abre una sesión propia;
no se mezcla con los mensajes que se dejan mientras el agente está respondiendo.

- **Una tras otra:** las tareas trabajan en la carpeta elegida. Cada una ve los
  cambios que dejó la anterior, pero tiene su conversación independiente.
- **En paralelo en worktrees:** de 1 a 4 tareas a la vez, cada una en una rama y
  carpeta distinta, desde el mismo HEAD. Necesita la raíz de un repositorio Git
  limpio; no copia archivos ignorados ni cambios sin confirmar. No ejecuta hooks
  ni filtros de Git al preparar las carpetas.

La tarjeta muestra el estado y la actividad de cada tarea, permite abrir su
sesión y ofrece el informe cuando termina. El resumen conjunto queda al final
de la conversación, con las rutas y ramas de los resultados. Los worktrees
permanecen en el directorio hermano de `$ALFALFA_HOME`, con sufijo `-worktrees`
(`~/.alfalfa-worktrees` por defecto). La cola no los borra, fusiona ni publica.

Las tareas heredan modelo, esfuerzo y modo de permisos de la sesión que preparó la cola,
comparten su presupuesto de sesión y mes y siguen mostrando los permisos que
necesitan. Un fallo o bloqueo pausa el lanzamiento de tareas nuevas; las que ya
están en marcha pueden terminar. «Pausar después de los activos» hace lo mismo
sin interrumpir trabajo. «Detener ahora», cerrar la app o escribir un mensaje
nuevo interrumpen los activos y conservan los pendientes. «Reanudar cola» continúa
las sesiones interrumpidas o bloqueadas, comprobando las operaciones inciertas,
y salta las tareas que ya terminaron. Al reabrir, nunca arranca sola.

En la CLI, guarda un JSON como este y ejecuta `alfalfa --cola-encargos cola.json`:

```json
{"modo": "serie", "encargos": ["Corrige el cálculo de la suma.", "Añade pruebas de los casos límite."]}
```

Para tareas independientes puedes usar `"modo": "paralelo", "paralelos": 2`.
La CLI muestra el ID de la sesión de la cola al empezar. Con
`alfalfa --reanudar ID --cola-encargos` continúas una cola guardada. Ctrl-C
la detiene conservando el estado para reanudarla.

## Capturas antes y después

Al probar una interfaz, abre su estado inicial antes de editar. Si durante el turno
usas el navegador y cambias archivos web, el cierre conserva una captura inicial y
otra final de la misma página y tamaño. Recarga la página para comprobar los cambios:
el cierre sólo toma la imagen de lo que quedó visible, sin recargar ni navegar por su cuenta.
Funciona con los motores que ofrecen `browser_screenshot`, con sus permisos habituales.

Para una app propia de Mac, pide `app_state` con captura antes de editar y después
de reconstruirla. Se reutilizan esas imágenes de la misma app y ventana; al cerrar
no se abre ni se captura ninguna app adicional. Los recortes con zoom no se comparan.
La ruta del ejecutable identifica la app aunque la vuelvas a abrir con otro proceso;
si no se conoce esa ruta, la comparación se limita al mismo proceso.

La tarjeta de cierre muestra «Antes» y «Después»; puedes ampliarlas con un clic.
También quedan en el historial del turno. Si falta una imagen, cambió la página o
la ventana, o hubo una edición posterior a la última captura de Mac, aparece
«Comparación incompleta». Sin uso de la interfaz o sin cambios de código pertinentes
no se añade esta tarjeta. Las instantáneas del código deben estar activadas para
situar las imágenes en el turno; no se reutilizan imágenes de turnos anteriores.

Las capturas son archivos privados locales de la sesión. Guardarlas no hace llamadas
al modelo ni añade permisos; las capturas automáticas no abren diálogos nuevos.
Comparten el tope de 50 capturas guardadas del navegador en esa carpeta: una imagen
antigua puede dejar de estar disponible. Son datos visuales, no una aprobación de
las pruebas ni una comprobación de que la app se reconstruyó correctamente.

La [demo reproducible](demos/interfaz-a13/README.md) cambia un botón de rojo a verde
con WebKit y muestra ambas imágenes en la tarjeta.

## Reglas de equipo

Guarda `.alfalfa/equipo.toml` en la raíz del proyecto y súbelo al repositorio
para compartir la política. AlfalfaCode lo busca en la raíz del worktree Git,
también si abres una subcarpeta. En una carpeta sin Git, lo busca en la carpeta
de trabajo. Se usa la misma política en la app de Mac y en la CLI.

```toml
modelos = ["qwen3:8b", "openai/gpt-5.4-mini"]

[permisos]
modos = ["manual", "ediciones", "plan"]

[presupuesto]
sesion = 2.50
mes = 20

[[regla]]
patron = ["git", "push"]
decision = "preguntar"

[[regla]]
patron = ["git", "reset", "--hard"]
decision = "prohibida"
```

- **Modelos.** La lista compara el identificador completo y su proveedor.
  `qwen3:8b` y `ollama/qwen3:8b` nombran el mismo modelo. Si tu elección o tu modelo
  habitual queda fuera, la sesión muestra las opciones permitidas para que elijas.
  También se valida el modelo de los subagentes,
  la revisión de código y las peticiones auxiliares, como el repaso de aprendizaje.
  Con una lista de modelos o un tope de equipo, las imágenes usan la visión del
  modelo de la sesión, si la admite.
- **Permisos.** Puedes limitar los modos a `auto`, `manual`, `ediciones`, `plan` u
  `omitir`. Si abres sin elegir un modo y el habitual queda fuera, se usa el
  primer modo permitido que sea más estricto. Si todos relajan los permisos,
  la sesión requiere una elección explícita. Las reglas de prefijo para `run_command` y `run_checks`
  admiten `preguntar` y `prohibida`. Se combinan con tus reglas personales y
  gana la más estricta. Una regla de equipo que pregunta requiere una respuesta
  incluso en «Omitir permisos». El sandbox, las reglas de contenido externo y
  las demás comprobaciones de permisos siguen aplicándose.
  Consulta la decisión de las reglas con `alfalfa --comprobar-orden <orden>`.
- **Topes.** Los importes son dólares positivos. Se aplica el menor de cada
  tope de equipo y tu tope personal. El mensual usa el gasto de este Mac que ya
  muestra AlfalfaCode. Los modelos de pago sin precio conocido se rechazan si
  hay un tope de equipo. El corte usa el presupuesto existente, que estima la
  entrada antes de pedirla y contabiliza la respuesta con las métricas del
  proveedor. El gasto se calcula en cada instalación.

Cada campo es opcional. Si falta el archivo, se usan tus ajustes. Si el archivo
es inválido, se indica el error al abrir la sesión. La política se carga al
abrir o reanudar y se conserva durante esa sesión. Los subagentes y los encargos
en cola heredan la política de la sesión que los lanza. Para aplicar una edición
del archivo, vuelve a abrir la sesión. Si cambió la política, caducan los
permisos guardados para esa sesión.

La app y la CLI indican dónde consultar `equipo.jsonl`, dentro de la carpeta
privada de la sesión. Cada fila indica la sesión, el agente, el turno y la
huella de la política. El registro enlaza el inicio y resultado de cada
herramienta con sus decisiones de permiso. Una respuesta de la terminal o del
cliente de la app se atribuye a la cuenta local que ejecuta AlfalfaCode. Las
aprobaciones automáticas indican su origen y las reutilizaciones enlazan con
el permiso de sesión guardado. Las órdenes incluyen el identificador de su
registro de ejecución para consultar el detalle cuando haga falta. El JSONL
omite argumentos, mensajes, salidas y contenido de archivos. Se guarda
localmente y queda fuera del repositorio.
