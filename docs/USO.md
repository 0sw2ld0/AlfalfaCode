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
`sin_sandbox` (por qué se saldría del sandbox), `fallo_sandbox` (error de
Seatbelt cuando no arrancó), `prefijo` (se puede contestar `"siempre":true`: «No
volver a preguntar por órdenes que empiecen por …») y `prefijo_alcance` (lo que
concede esa regla: `sin_sandbox`, `red` o `sandbox`; ver «No volver a preguntar»
más abajo). Los resultados de herramientas añaden `sandbox`
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

Fuera de macOS (un servidor Linux) no hay Secure Enclave ni Llavero. `--activar-codigo`
crea una clave P-256 en `~/.alfalfa/firma/clave.pem` (carpeta 0700, archivo 0600) y firma
con el mismo formato, así que el servicio no distingue. Después pide el código en la
variable `ALFALFA_CODIGO`: es el `CUY-…` sin lo que va tras la arroba, porque la dirección
del servicio ya queda en `proveedores.json`.

```bash
printf '%s\n' 'CUY-XXXX-XXXX-XXXX@servicio.example.com' | alfalfa --activar-codigo
 export ALFALFA_CODIGO='CUY-XXXX-XXXX-XXXX'
alfalfa --cuota
```

Esa clave es un archivo, no un chip: quien la copie puede usar el código hasta que lo anules
en el servicio. Por eso va en su propia carpeta, para que el sandbox se la tape a las
órdenes del agente. Para un servicio desatendido de systemd, la clave puede ir cifrada con
`systemd-creds encrypt` y llegar con `LoadCredentialEncrypted=alfalfa-firma:…`. En ese caso
`ALFALFA_FIRMA_CLAVE=%d/alfalfa-firma` le dice al motor dónde la dejó systemd, y el motor
solo la lee.

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

## Control con PACAI: `alfalfa --pacai`

`alfalfa --pacai` habla [PACAI](#qué-es-pacai) 0.3 por stdio (JSONL) en lugar de `--stdio` v2. Es el mismo motor
(TerminalManager y NativeRuntime): cambia sólo el idioma. La app de Mac abre así sus sesiones (con
`ProtocoloMotor = stdio` en sus `UserDefaults` vuelve a v2). `--stdio` v2 sigue igual y no se retira: Chiyo lo usa,
y la app también para sus consultas de una pasada (`--json`) y para los Ajustes de sitios del navegador.

### Qué es PACAI

PACAI (*Protocol for Agent Communication And Interfaces*) es el protocolo común entre un **agente** y un **cliente**
(app, móvil, widget, terminal…). AlfalfaCode es su primera implementación: **PACAI-Core + perfil `work` + extensión
`alfalfa`**, sobre el transporte stdio. Quedan fuera por ahora la reanudación en red, WebSocket y SSE, y los perfiles
`voice`, `shared_state` y `expression`.

### Cómo se usa

```bash
alfalfa --pacai                      # sesión nueva; el primer encargo llega como `input`
alfalfa --pacai --reanudar ID        # abre una sesión guardada y espera un `input`
alfalfa --pacai --navegador          # el cliente ofrece su navegador (herramienta del cliente `browser`)
alfalfa --pacai --caducidad-aprobacion 3600   # las aprobaciones caducan a la hora (por defecto, no caducan)
```

`--pacai` no admite un encargo en la orden, ni `--stdio`, `--bifurcar`, adjuntos o un archivo de `--cola-encargos`:
la entrada llega siempre como mensajes. `--cola-encargos` sin archivo sí vale: abre la sesión de una cola, que el
cliente prepara e inicia con sus mensajes. stdout lleva **sólo** mensajes PACAI, uno por línea; los registros van a stderr.
Cerrar la entrada (EOF) equivale a `close` del cliente y **niega** los permisos pendientes.

```jsonl
{"pacai":"0.3","id":"c_1","type":"hello","session":"s_0123456789abcdef","sender":"client","seq":1,"time":"2026-11-15T10:00:00Z","payload":{"versions":["0.3"],"profiles":["work"],"extensions":["alfalfa"]}}
{"pacai":"0.3","id":"m_a1","type":"hello","session":"s_0123456789abcdef","sender":"agent","seq":1,"time":"…","payload":{"versions":["0.3"],"profiles":["work"],"extensions":["alfalfa"],"implementation":{"name":"alfalfa","version":"0.99.24"}}}
{"pacai":"0.3","id":"m_a2","type":"session_started","reply_to":"c_1","session":"s_0123456789abcdef","sender":"agent","seq":2,"time":"…","payload":{"version":"0.3","profiles":["work"],"extensions":["alfalfa"],"agent":{"name":"alfalfa","version":"0.99.24"}}}
{"pacai":"0.3","id":"c_2","type":"input","session":"s_0123456789abcdef","sender":"client","seq":2,"time":"…","payload":{"blocks":[{"type":"text","text":"Corrige el test de login"}]}}
{"pacai":"0.3","id":"m_a3","type":"run_started","…":"…","payload":{"input_id":"c_2"}}
{"pacai":"0.3","id":"m_a4","type":"tool_start","…":"…","payload":{"run_id":"m_a3","name":"run_command","arguments":{"argv":["pytest","-q"],"purpose":"Probar"},"side":"agent","title":"Ejecutar pytest -q"}}
{"pacai":"0.3","id":"m_a5","type":"approval_request","…":"…","payload":{"run_id":"m_a3","tool_call_id":"m_a4","name":"run_command","arguments":{"…":"…"},"digest":"sha256:…","reason":"network","scopes":["once"]}}
{"pacai":"0.3","id":"c_3","type":"approval_result","reply_to":"m_a5","…":"…","payload":{"decision":"approved","scope":"once"}}
```

La sesión negocia con el `hello` del cliente: el agente adopta su `session`, contesta con su propio `hello` y con
`session_started` (con `reply_to`), y los perfiles y extensiones activos son la intersección. Un cliente que sólo
declare `versions` recibe el núcleo. `--pacai` sólo habla la 0.3: un cliente que no la ofrezca (uno de la 0.2, por
ejemplo) recibe `error` `version_incompatible`, se cierra y lo que encadenó tras su `hello` se descarta sin respuesta.

El cliente **puede encadenar** mensajes justo tras su `hello`, sin esperar a `session_started`: se procesan en orden
con las reglas de la sesión negociada (lo de un perfil o extensión que no quedó activo recibe `not_negotiated`).
Lo que llega **antes** del `hello` es `invalid_message`.

### Correspondencia con `--stdio` v2

| `--stdio` v2 | PACAI 0.3 |
|---|---|
| `sesion` | `session_started` (`agent`); lo demás de la sesión, `alfalfa.session` |
| entrada `mensaje` | `input` `new_run` (con una ejecución en curso **espera** a que acabe); `imagenes` y `archivos`, bloques `file` con `url` `file://` (`media_type` `image/*` → imagen) |
| entrada `mensaje` con un turno en marcha | `input` `steer` con el `run_id` raíz y `payload.alfalfa.redirect: false` (entra en la siguiente inferencia del turno, sin cortar la respuesta) |
| entrada `redirigir`, `enviar_ahora` | `input` `steer` con el `run_id` de la ejecución (mensaje y redirigir a la vez); `alfalfa.redirect` (redirigir sin texto) y `alfalfa.send_now` (sacar una entrada de la cola) |
| encargo de la orden (sin eco) / `--reanudar` sin encargo | `input` con `payload.alfalfa.echo: false` / `input` sin bloques con `payload.alfalfa.continue: true` |
| entrada `cancelar` / EOF | `close` del cliente / EOF (niegan lo pendiente) |
| entrada `interrumpir` | `interrupt` con `run_id` (la raíz o una subejecución) |
| primer evento de un turno | `run_started` (`payload.input_id`); su `id` es el `run_id` |
| `turno_terminado` | `run_finished` (`completed`→`success`, `blocked`, `failed`, `cortado`/`interrupted`→`interrupted`); antes, `alfalfa.closing` con el `turno_terminado` entero (`status` exacto, `cut` para `cortado`) |
| `evento` `agent` | `message_start` / `message_delta` / `message_end` (`role` `assistant`; el `turno`, en `payload.alfalfa.turn`) |
| `evento` `system` | `message_*` con `role` `system` en una ejecución; fuera de ella (o en cierre), `alfalfa.notice` con el evento entero. El aviso de lo importado sin autorizar lleva sus datos (`importadas`: clave, huella, tipo, nombre, herramienta y estado de cada pendiente) en `payload.alfalfa.imported` del `message_start` |
| `evento` `user` | el núcleo no lo devuelve; `alfalfa.user_message` (`text`, `input_id`, `turn`) dice cuándo entró en la conversación |
| `evento` `tool` (`herramienta.fase` `inicio`/`fin`) | `tool_start` / `tool_end` (`status` `success` o `error` según la clasificación del motor); `payload.alfalfa` lleva `herramienta` entera (`tool`), el `turno` y, en el inicio, el texto de v2 (`text`). Sin ejecución abierta (o un fin sin su inicio abierto), `alfalfa.notice` |
| `permiso` | `approval_request` (`digest` JCS de los argumentos, `reason`, `scopes` y, si hay plazo, `expires`); el motivo principal de v2 va en `payload.alfalfa.reason`, y los argumentos de una herramienta MCP, tal cual en `payload.alfalfa.mcp.arguments` |
| entrada `permiso` | `approval_result` (`approved` o `denied`, `scope` `once` o `session`); «No volver a preguntar» y el sitio para todo el proyecto, `payload.alfalfa.scope` `always` o `project` |
| `permiso_resuelto` | no sale si lo resolvió el cliente; si lo negó la interrupción o el fin de la ejecución, `approval_result` `cancelled` del agente con el motivo de v2 en `reason`; con plazo y sin respuesta, `expired` |
| `contexto`, `uso_sesion` | `usage` (`context_tokens`, `context_limit`, `cost`); el `contexto` entero, `alfalfa.context` |
| `plan` | `plan_updated` (perfil `work`) |
| cambios del turno (diff de la instantánea o, sin ella, de git) | `diff_updated` (perfil `work`) |
| `subagentes`, `subagente_evento` | subejecuciones: `run_started` con `parent_run_id`, el `tool_call_id` del `spawn_agent` o `send_input` que lo lanzó y el id del subagente en `payload.alfalfa.agent`; sus mensajes, herramientas y aprobaciones con su propio `run_id`. Lo que no cabe en una subejecución abierta, `alfalfa.subagent_event` |
| `navegador` / `navegador_respuesta` | `tool_start` con `side: "client"` (herramienta `browser`) / `tool_end` del cliente |
| `error` | `error` (`code` `alfalfa.engine_error`, o `message_too_large`, `invalid_message`…) |
| `fin` | `close` (el motivo en `reason`; el `fin` entero, en `payload.alfalfa`) |
| `presupuesto`, `procesos`, `despierto`, `cuota`, `preparando`, `revision_codigo`, `encargos`, `aprendizaje_*`, `quitado_de_cola`, `sesion_cambiada`… | extensión `alfalfa.*` (`alfalfa.budget`, `alfalfa.processes`, `alfalfa.awake`, `alfalfa.quota`, `alfalfa.preparing`, `alfalfa.code_review`, `alfalfa.jobs`, `alfalfa.learning_proposed`…) |

La extensión habla como el núcleo: tipos **y campos** en inglés y `snake_case`. Cada campo de v2 pasa a su nombre
inglés a cualquier profundidad (`texto`→`text`, `estado`→`status`, `coste_sesion`→`session_cost`,
`desglose`→`breakdown`…; la tabla completa está en `alfalfa/pacai/nombres.py`), y lo mismo el bloque
`payload.alfalfa` que llevan `approval_request` (`purpose`, `justification`, `reasons`, `unsandboxed`…),
`plan_updated` (`explanation`), `usage` (`turn_tokens`, `total_tokens`), `error` (`reason`, `rejected`) y `close`
(`status`, `exit_code`, `tokens`, `cost`). También los **valores enumerados**, los que una máquina compara, van en
inglés y `snake_case`, p. ej. `{"type":"alfalfa.budget","payload":{"session":{"spent":1.2,"cap":5},"warning":"session"}}`.
Los textos para personas (mensajes, resúmenes, errores legibles) y los nombres propios (herramientas, modelos,
tipos de subagente como `explorador`, ids) no se traducen. Los más usados:

| Campo | Valores |
|---|---|
| `status` | subagentes: `working`, `completed`, `failed`, `closed`, `interrupted`; cola de encargos: `ready`, `running`, `pausing`, `paused`, `completed`, `cancelled` (cada encargo: `pending`, `running`, `cancelled`, `interrupted`…; su `cleanup`: `removed`, `kept`, `branch_kept`); revisión de código: `available`, `reviewing`, `completed`, `failed`, `no_budget`…; compactación: `in_progress`, `done`, `failed` |
| `mode`, `permissions` | permisos: `auto`, `manual`, `accept_edits`, `plan`, `bypass_permissions`; contexto: `saving`, `full`; cola: `serial`, `parallel` |
| `reason`, `reasons` | permisos: `dangerous`, `network`, `outside_sandbox`, `sandbox_retry`, `rule`, `mode`, `edit`, `browser`, `external_content`, `learning`, `image_generation`, `mcp`, `app`, `swift_diagnostics`; otros: `no_vision`, `unknown`, `delivered`… |
| `warning`, `exhausted` | `session`, `month` |
| `prefix_scope` | lo que concede «No volver a preguntar» (`prefix`) en un `approval_request`: `outside_sandbox`, `network`, `sandbox` |
| `kind`, `store`, `scope`, `origin`, `action`, `step`, `phase` | `memory`/`skill`; `user`/`project`/`environment`; `folder`, `all`…; `agent`, `external`…; `create`, `restore`…; `undo`/`redo`; `start`/`end` |

La tabla completa (y la de las claves) está en `alfalfa/pacai/nombres.py`; una prueba recorre el código y falla si
aparece un valor enumerado sin traducir. El `reason` propio de un `approval_request` también va en inglés
(`alfalfa.edit`, `alfalfa.browser`, `alfalfa.swift_diagnostics`…). Un cliente ignora los tipos y campos que no conozca. Las extensiones sólo se envían si el cliente declaró `alfalfa`
en su `hello`. `--stdio` v2 no cambia.

### Órdenes de AlfalfaCode (extensión `alfalfa`)

Lo que v2 hacía con entradas propias, PACAI lo hace con mensajes `alfalfa.*` del cliente, con los campos y los valores
en inglés (el agente los devuelve a v2 antes de dárselos al motor). Un valor enumerado desconocido (también el de
v2, como `ediciones`) recibe `error` `invalid_message` con los valores que valen, y no llega al motor:

| Mensaje (campos) | Hace lo de v2 |
|---|---|
| `alfalfa.set_settings` (`model`, `effort`, `mode`: `auto`, `manual`, `accept_edits`, `plan` o `bypass_permissions`) | `ajustes` (`modelo`, `esfuerzo`, `modo`) |
| `alfalfa.compact` (`instructions`), `alfalfa.continue_in_new_session` | `compactar`, `continuar_en_nueva` |
| `alfalfa.set_context_mode` (`mode`: `saving` o `full`) | `contexto` (`ahorro`, `completo`) |
| `alfalfa.set_budget` (`session`, `month`, en dólares) | `presupuesto` (`sesion`, `mes`) |
| `alfalfa.set_keep_awake`, `alfalfa.set_diagnostics`, `alfalfa.set_swift_diagnostics`, `alfalfa.set_signing` (`active`) | `mantener_despierto`, `diagnosticos`, `diagnosticos_swift`, `firma` (`activo`) |
| `alfalfa.resolve_learning` (`id`, `apply`), `alfalfa.review_code` (`turn`, `model`), `alfalfa.cancel_code_review` | `aprendizaje`, `revisar_codigo`, `cancelar_revision_codigo` |
| `alfalfa.export_browser_test` (`id`, `path`), `alfalfa.stop_process` (`id`) | `exportar_prueba_navegador`, `detener_proceso` |
| `alfalfa.jobs_command` (`action`: `prepare`, `start`, `pause`, `stop`, `cancel` o `clean`; `jobs`; `mode`: `serial` o `parallel`; `parallel`) | `encargos` (`accion`, `encargos`, `modo`, `paralelos`) |
| `alfalfa.dequeue`, `alfalfa.send_now` (`id`) | `quitar_de_cola`, `enviar_ahora` de una entrada que aún espera |
| `alfalfa.redirect` | `redirigir`: el modelo lee ya lo que se le mandó con `steer` |

Lo que puede pedir permisos fuera de un turno (guardar una prueba del navegador, iniciar una cola de encargos) va
como un `input` con un solo bloque `structured` cuyo `schema` es la orden (`alfalfa.export_browser_test` o
`alfalfa.jobs_command`, con `data` como su payload). Si el motor pone el trabajo en marcha, ese `input` abre una
ejecución raíz que dura lo que él: sus permisos son `approval_request` de esa ejecución. Un `input` que llega
mientras corre no espera: el motor para el trabajo y abre el turno, como en v2.

### Reglas que PACAI exige

- **Una sola ejecución raíz activa.** Un `input` `new_run` con una en curso se encola y su `run_started` llega al
  cerrarse la anterior (en v2 un `mensaje` entraba en el turno en curso: eso es ahora `steer`).
- **Toda ejecución se cierra con `run_finished`**, y no antes de que se cierren sus herramientas, sus aprobaciones y
  sus subejecuciones. Una herramienta cortada por una interrupción se cierra con `tool_end` `cancelled`.
- **Subagentes.** Cada invocación es una subejecución con el `tool_call_id` de la herramienta que la lanzó
  (`spawn_agent`, o `send_input` si lo relanza; sin él si el cliente le escribió con `steer`). Con
  `background: true`, `spawn_agent` cierra `success` en el acto y el subagente sigue; la raíz lo espera antes de
  su `run_finished` (el turno no se cierra con subagentes trabajando). Interrumpir un subagente cuya herramienta
  sigue abierta cierra esa herramienta con `cancelled` y la madre sigue.
- **Aprobaciones.** El `digest` es `sha256` de los argumentos canonizados (JCS, RFC 8785) y cubre los mismos
  argumentos que el `tool_start`. **Por defecto no caducan**: no llevan `expires` y esperan al usuario, como en
  Claude Code, ACP y Codex. `--caducidad-aprobacion SEGUNDOS` o `ALFALFA_CADUCIDAD_APROBACION=SEGUNDOS` les ponen
  plazo (más de 0 y hasta un año); pasado, el agente declara `expired` y no ejecuta. Sin `expires` nunca hay
  `expired`. Si la ejecución termina o se interrumpe (también por `close` o EOF) con una petición sin contestar,
  el agente la resuelve con `approval_result` `cancelled` antes del `tool_end` `cancelled` de su herramienta.
  Una respuesta tardía, o un valor que no sea exactamente `approved` o `denied`, no vale. «Aprobar para la sesión»
  (`scope: "session"`) sólo cubre el mismo nombre y los mismos argumentos; cada repetición es un `tool_start` con
  `approval_id`. Lo que la política resuelve sola (lecturas permitidas) no se pregunta.
- **Herramientas del cliente.** El navegador de la app (`--navegador`) es la herramienta `browser` que el cliente
  declara en `hello.client_tools`: el agente emite `tool_start` con `side: "client"` y el cliente contesta con
  `tool_end` y `reply_to`. Sus argumentos son la petición del navegador de la app en inglés: `action` (`open`,
  `read`, `click`, `type`, `scroll`, `screenshot`, `eval`, `handoff`, `wait`, `key`, `hover`, `state`, `upload`,
  `download`, `console`, `network`, `viewport`, `cancel_site`, `resume_site`), `target`, `text`, `submit`, `clear`,
  `mode` (`text` o `tree`), `direction` (`up`, `down`, `left`, `right`), `granted` (`form`, `password`,
  `download`), `sites`, `timeout_ms`… El cuerpo de su respuesta va en un bloque `structured`: `ok`, `result`
  (`url`, `tree`, `truncated`, `screenshot`, `consumed_visit`…), `error` y `permission` (`type`: `site`, `form`,
  `password` o `download`; `started`, `url`, `token`, `detail`). El agente lo devuelve a los nombres de v2 para el
  motor; la tabla está en `alfalfa/pacai/nombres.py` (`NAVEGADOR_CAMPOS`, `NAVEGADOR_VALORES`). Sin esa
  declaración, el motor recibe un «no».
  Si la ejecución termina o se interrumpe con la herramienta abierta, el agente la cancela con `tool_end`
  `cancelled` sin `reply_to` (el motor recibe un «no»); lo que el cliente mande después se ignora sin error.
- **Ejecución en cierre.** Tras un `cancelled` del agente (una aprobación o una herramienta del cliente), esa
  ejecución no empieza nada nuevo: ni mensajes, ni herramientas, ni aprobaciones, ni subagentes. Sólo cierra lo
  abierto, informa (`usage`, `plan_updated`, `diff_updated`) y termina; lo que diga aún el motor sale como
  `alfalfa.notice`.
- **Mensajes del cliente.** Se validan contra el esquema; un `seq` repetido o con huecos, un `id` repetido con otro
  contenido, un `approval_result` sin petición pendiente o una extensión no negociada reciben su `error`
  (`invalid_message`, `seq_unrecoverable`, `unknown_approval`, `not_negotiated`…) y la sesión sigue, salvo
  `seq_unrecoverable`.

### Límites de esta primera versión

- Los `input` admiten bloques `text`, `file` con `url` `file://` y ruta absoluta (no `data`) y un bloque
  `structured` de un trabajo (arriba); lo demás se rechaza con `invalid_message`.
- Los `arguments` de un `tool_start` (y de su `approval_request`) son los argumentos JSON de la llamada del
  modelo, completos, con cada secreto tachado siempre como `"[redacted]"` (el `digest` es el de lo que viaja); lo
  legible va en `title` (la descripción de la app). Si pasan de una cuarta parte de `max_message_bytes` (256 KiB por
  defecto), los textos más largos se recortan (acaban en `… [recortado]`) y el `tool_start` lleva `truncated: true`.
  Una llamada truncada no se puede aprobar: si necesita permiso, se niega y termina en `tool_end` `error`. Los
  argumentos de una herramienta del cliente nunca se tachan ni se acortan. `--stdio` v2 no cambia: su evento sigue
  llevando el texto `Inicio <herramienta> · …`.
- Un permiso que llega fuera de una ejecución se niega: PACAI exige atribuirlo a una ejecución activa. Por eso
  guardar una prueba del navegador y las colas de encargos se piden con un `input` (arriba).
- Un `hello` con `resume_after_seq` recibe `retention_expired`: este proceso no retiene una sesión anterior. Para
  seguir una sesión guardada se usa `alfalfa --pacai --reanudar ID`.
- `diff_updated` sale al cerrar una ejecución que cambió archivos, y es el **acumulado** de la ejecución (todo desde
  su `run_started`; si hubiera varios, cada uno sustituye al anterior). Con la instantánea de deshacer, es su diff. Sin
  ella (`ALFALFA_INSTANTANEAS=0`, o un turno que la instantánea no cubre) y en un repo git, lo calcula el propio
  AlfalfaCode con git, no el agente (ni pasa por el sandbox ni pide permisos): al empezar la ejecución fija el punto
  de partida con un índice temporal (`GIT_INDEX_FILE`, copiado del tuyo sólo para ir rápido) y `git write-tree`, y
  al terminar lo compara con `git diff-tree`. No toca tu índice, tu rama ni tus archivos; sólo deja objetos sueltos
  en `.git/objects` que `git gc` limpia. Cuenta sólo la carpeta de la sesión, respeta `.gitignore`, deja fuera los
  archivos nuevos de más de 1 MiB, de un binario sólo dice que cambió, y con un parche de más de 200.000
  caracteres manda la lista de archivos sin `patch`. Fuera de un repo git, sin instantánea no hay diff.

### Comprobarlo

El verificador de PACAI (`conformance/verificar.py`) acepta una transcripción con los mensajes de los dos lados:

```bash
python3 conformance/verificar.py transcripcion.jsonl
```

La suite de AlfalfaCode comprueba lo mismo con el esquema y las reglas de sesión del protocolo
(`tests/test_pacai_*.py`, con los ejemplos de conformidad de `tests/fixtures/pacai`).

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

### No volver a preguntar

Algunas tarjetas ofrecen además «No volver a preguntar por órdenes que empiecen
por …» (`prefijo` en el evento `permiso`, `a` en la terminal). Elegirlo guarda una
regla `permitida` en `~/.alfalfa/reglas.toml` (o `$ALFALFA_HOME/reglas.toml`) con
ese prefijo, que vale para todas las sesiones. Lo que concede depende de la tarjeta
(`prefijo_alcance`):

| Tarjeta | Regla que guarda | Qué deja hacer sin preguntar |
|---|---|---|
| `salir_sandbox` o `reintento` | `fuera_del_sandbox = true` (`sin_sandbox`) | Ejecutar esas órdenes **sin sandbox**, con red. |
| `red` | `con_red = true` (`red`) | Usar la red **dentro** del sandbox. La escritura sigue limitada y no permite salir del sandbox: si la orden pide `require_escalated`, se pregunta. |
| `modo` (Manual y Aceptar ediciones) | sin más (`sandbox`) | Quitar la pregunta del modo. La orden sigue en el sandbox y sin red. |

El prefijo lo propone el modelo (`prefix_rule`). Si no lo propone, para git se
ofrece la suborden (`git push`). En las tarjetas de red y de modo, también la de
`npm`, `pnpm`, `yarn`, `pip`, `pip3`, `uv`, `cargo`, `go`, `make`, `gem`, `bundle`,
`poetry`, `composer`, `mvn`, `gradle`, `dotnet` y `mix` (`npm install`, `make test`).
La regla casa el prefijo tal cual: el programa por su nombre o por una ruta del
sistema, nunca `tools/npm`, `./npm` ni detrás de `env`, `sudo` u otro envoltorio.
Nunca se ofrece en una orden peligrosa, tras leer contenido externo ni si una regla
que pregunta o prohíbe casa la orden, y nunca prefijos que lo permitirían todo
(`bash -lc`, `python3`, `git`, `npm run`, `npm exec`, `pnpm dlx`, `uv run`…). Esas
reglas tampoco valen después: una orden peligrosa, una regla que pregunta o prohíbe
y el contenido externo siguen preguntando aunque la regla case.

La tarjeta de edición del modo Manual (`edicion`) no lo ofrece: una regla de
`reglas.toml` vale en todas las carpetas y casa órdenes, no archivos. Para no
preguntar por las ediciones está el modo «Aceptar ediciones», y «Permitir en esta
sesión» para esos archivos.

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
se crean en el directorio hermano de `$ALFALFA_HOME`, con sufijo `-worktrees`
(`~/.alfalfa-worktrees` por defecto), en ramas `alfalfa/cola-<cola>-<encargo>`.
La cola nunca fusiona ni publica nada.

**Limpieza de los worktrees.** La cola quita un worktree y su rama sólo si no
guardan trabajo que no esté ya en otro sitio. Tienen que cumplirse todas estas
condiciones:

- el encargo terminó y la cola ya no lo va a reanudar: en una cola terminada o
  cancelada, todos; en una pausada, sólo los terminados (al reanudar se saltan);
- el worktree no tiene cambios sin confirmar ni archivos nuevos sin seguimiento
  (los ignorados por `.gitignore`, como compilaciones o `node_modules`, se borran
  con la carpeta), sigue en su rama y no está bloqueado con `git worktree lock`;
- la rama no tiene commits propios: todos están ya en el punto del que salió, en
  la rama base (la que tenía el proyecto al empezar la cola) o en la rama actual
  del proyecto.

Si algo no se cumple, se conserva y la cola dice por qué («tiene 3 commits sin
integrar en main», «tiene 2 cambios sin confirmar»…). Nunca usa `--force` ni
`git branch -D`: Git vuelve a comprobarlo al quitar y, si se niega, también se
conserva. Si borraste a mano la carpeta de un worktree, se quita sólo su registro
en Git (como `git worktree prune`, pero sin tocar otros worktrees tuyos) y la rama
sigue la misma regla. El repositorio del proyecto no cambia: ni su rama, ni su
HEAD, ni su índice.

La limpieza se hace al terminar o detener la cola, al cancelarla, al reabrir la
sesión de la cola (por si integraste ramas desde entonces) y con la orden
explícita. El resumen final y un aviso en la conversación dicen qué se quitó y
qué se conserva; en
`--stdio` v2 cada encargo lleva `limpieza` (`estado`: `quitado`, `conservado` o
`rama_conservada`; `motivo`). La orden explícita es `{"tipo": "encargos",
"accion": "limpiar"}` por `--stdio` (`alfalfa.jobs_command` con `action: "clean"`
por PACAI) o, en la CLI, `alfalfa --reanudar ID --limpiar-worktrees`, que no
ejecuta ningún encargo.

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

## Linux

El motor (`alfalfa`) también corre en Linux, con los mismos modos que en el Mac: un
encargo desde la terminal (por SSH, por ejemplo), `alfalfa "encargo"` desde un guion y
el control por stdio (`--stdio` y `--pacai`). La app de Mac y CuyBrowser no existen en
Linux. Está probado en Ubuntu 24.04 con Python 3.12. En Windows, con WSL2 (más abajo).

### Linux: instalar

Hace falta Python 3.11 o posterior, git y bubblewrap; poppler-utils es opcional y sirve
para leer el texto de los PDF adjuntos (más abajo). Con pipx:

```bash
sudo apt update
sudo apt install -y pipx git bubblewrap poppler-utils
pipx ensurepath
pipx install "git+https://github.com/0sw2ld0/AlfalfaCode@v0.99.24"
```

Cambia `v0.99.24` por la [última release](https://github.com/0sw2ld0/AlfalfaCode/releases/latest).
Abre otra terminal para que `~/.local/bin` entre en el PATH y comprueba con
`alfalfa --version`. Para actualizar, `pipx install --force` con la versión nueva; para
quitarlo, `pipx uninstall alfalfa` (tus sesiones de `~/.alfalfa` se quedan).

Sin pipx, en un venv propio:

```bash
python3 -m venv ~/.local/share/alfalfa-venv
~/.local/share/alfalfa-venv/bin/pip install "git+https://github.com/0sw2ld0/AlfalfaCode@v0.99.24"
mkdir -p ~/.local/bin && ln -sf ~/.local/share/alfalfa-venv/bin/alfalfa ~/.local/bin/alfalfa
```

`instalar.sh` no sirve en Linux: baja y comprueba la app firmada de Mac, y en Linux sólo
remite aquí.

### Linux: el sandbox

Las órdenes del agente, los hooks, los servidores MCP por stdio y los linters corren en
bubblewrap (`bwrap`), con la misma política que Seatbelt en el Mac:

- leen todo el disco y sólo escriben en la carpeta de trabajo, `$TMPDIR` y `/tmp`;
- `.git` no se puede mover ni borrar, y sus `hooks` y `config` son de solo lectura. Ramas,
  commits y stash funcionan sin pedir permiso;
- el hogar `~/.alfalfa` es de solo lectura;
- sin red salvo un permiso «red». Además de un espacio de red propio, un filtro seccomp
  niega abrir sockets, también los Unix: así una orden no llega a Docker, a tmux ni al bus
  de la sesión;
- sin capacidades, también si AlfalfaCode corre como root, y con su propio espacio de
  procesos: no ve el entorno de AlfalfaCode, donde están las claves. Las credenciales de
  systemd (`/run/credentials`) y la clave de firma del código de cortesía
  (`~/.alfalfa/firma`) también quedan vacías dentro.

Si `bwrap` falta o no arranca, cada orden pide el permiso «SIN SANDBOX», como en el Mac
cuando falla Seatbelt.

**Ubuntu 23.10 y posteriores, 24.04 incluida,** limitan con AppArmor los espacios de
nombres de usuario sin privilegios, y bwrap los necesita. Como usuario normal falla con
`setting up uid map: Permission denied` o `loopback: Failed RTM_NEWADDR`, y AlfalfaCode
lo dice. Se arregla una vez, con sudo, dándole a `/usr/bin/bwrap` un perfil que se lo
permita, como Ubuntu hace con otros programas que los usan:

```bash
sudo tee /etc/apparmor.d/bwrap >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,

  include if exists <local/bwrap>
}
EOF
```

```bash
sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

Para comprobarlo: `bwrap --ro-bind / / --unshare-user --unshare-net true && echo bien`.
Como root no hace falta el perfil, pero es mejor un usuario normal: lo que saliera del
sandbox tendría sus permisos y no los de root. Dentro de Docker, bwrap necesita además
`--security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt
systempaths=unconfined`.

Diferencias con el Mac, porque un montaje sólo protege lo que existe al lanzar la orden:

- En el Mac una orden tampoco puede crear un `.git` ni una carpeta de instrucciones
  (`.alfalfa`, `.agents`, `.claude`, `.codex`) nuevos; en Linux se protegen los que ya
  existen. Si a un `.git` le faltan `hooks` o `config`, se crean vacíos antes de la orden.
- Cada orden tiene su propio espacio de procesos: lo que deja en segundo plano con `&`
  muere con ella, y las órdenes mueren con AlfalfaCode. Para dejar un servidor abierto
  entre turnos está `run_command` en segundo plano, como en el Mac.
- El texto de un PDF adjunto lo saca `pdftotext`, de poppler-utils, dentro de bubblewrap y
  sin red, en lugar de PDFKit. Sin poppler-utils, o si bwrap no arranca, un PDF sólo llega
  a los modelos que leen PDF.
- Las imágenes adjuntas las mide, reduce a 2048 px y pasa a PNG Pillow, que pipx instala
  con AlfalfaCode sólo en Linux, en un proceso aparte dentro de bubblewrap y sin red, en
  lugar de sips. Si bwrap no arranca, una PNG o JPEG va tal cual y las demás no se
  adjuntan. Pillow no lee HEIC.
- Sin macOS no hay sourcekit-lsp aislado ni diagnósticos de Swift.

### Linux: claves

No hay Llavero: `alfalfa --guardar-clave` lo dice, sin pedir la clave, y nombra la
variable de entorno donde va:

| Proveedor | Variable |
|---|---|
| Ollama Cloud | `OLLAMA_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| OpenCode Zen | `OPENCODE_API_KEY` |
| otro compatible del catálogo | la que dice `alfalfa --proveedores --json` (`variable`) |

Para **Ollama Cloud**, además de la clave, apunta Ollama a la nube una vez, porque en un
servidor no hay Ollama local y el motor lo busca en `http://127.0.0.1:11434`:

```bash
alfalfa --fijar-url ollama https://ollama.com
```

Por SSH, se exporta en la sesión antes de usar `alfalfa`. En Ubuntu, con un espacio
delante, bash no la guarda en el historial:

```bash
 export OLLAMA_API_KEY='…'
```

Con `--pacai`, `--stdio` o desde otro programa, el proceso que lanza `alfalfa` le pasa la
variable en su entorno. Las órdenes del agente no la reciben: el arnés les da un entorno
mínimo y el sandbox no les deja leer el de AlfalfaCode. `alfalfa --proveedores` dice qué
proveedores tienen clave, sin enseñarla. Para un servicio sin nadie delante, la clave va
cifrada con `systemd-creds` (en la sección siguiente).

### Linux: sin nadie delante

AlfalfaCode puede trabajar solo en el servidor, sin nadie conectado: un temporizador de
systemd, un guion u otro programa que lo controla por `--pacai`. Nada se queda esperando
un permiso que nadie puede dar:

- **`alfalfa "encargo"` sin terminal** (temporizador, cron, un guion): lo que pide permiso
  se niega en el acto, queda en el registro de la sesión y el proceso termina con código 3.
- **`alfalfa --pacai`** desde tu programa: los permisos le llegan como `approval_request`.
  Si puede no contestar, arráncalo con `--caducidad-aprobacion SEGUNDOS` (o
  `ALFALFA_CADUCIDAD_APROBACION`): pasado el plazo, la petición caduca y se niega. Sin
  plazo, espera la respuesta.
- **`alfalfa --stdio`** es para una app con alguien delante: espera al cliente y no caduca.

| Código de `alfalfa "encargo"` | Qué pasó |
|---|---|
| 0 | Terminó. |
| 1 | Quedó bloqueado o falló; el resumen dice por qué. |
| 3 | Terminó, pero negó al menos un permiso porque no había nadie para responder. |
| 130 | Se interrumpió. |

**Qué puede hacer solo** se decide de antemano. Todo corre igual dentro de bubblewrap:

- `--modo auto` (el de siempre): las órdenes normales corren en el sandbox sin preguntar,
  escriben sólo en la carpeta y no salen a la red. Lo peligroso, la red y salir del sandbox
  piden permiso; sin nadie, se niegan.
- `--modo plan` sólo lee y propone un plan. `manual` y `ediciones` preguntan casi todo, así
  que sin nadie lo niegan casi todo.
- `--modo omitir` lo aprueba todo, también fuera del sandbox: no lo uses sin nadie delante.
- Las reglas de `~/.alfalfa/reglas.toml` («No volver a preguntar») abren lo concreto. Por
  ejemplo, `npm install` con red, dentro del sandbox:

  ```toml
  [[regla]]
  patron = ["npm", "install"]
  decision = "permitida"
  con_red = true
  justificacion = "Instalar dependencias sin preguntar, en el sandbox."
  ```

  Una orden peligrosa, una regla `preguntar` o `prohibida` y lo que viene tras leer
  contenido externo siguen pidiendo permiso. Evita `fuera_del_sandbox = true` en un
  servidor.

**Receta para Ubuntu con systemd.** Usa un usuario propio y sin sudo para AlfalfaCode, no
root, e instala `alfalfa` como ese usuario (`sudo -iu alfalfa` y los pasos de «Linux:
instalar»):

```bash
sudo adduser --disabled-password --gecos '' alfalfa
```

La clave la escribes tú, cifrada con `systemd-creds`. `systemd-creds has-tpm2` dice si el
servidor tiene TPM; sin él (un droplet, por ejemplo) se cifra sólo con la clave del propio
servidor, `/var/lib/systemd/credential.secret`, que sólo lee root. Así ni el usuario
`alfalfa` ni el agente pueden descifrarla del disco, pero quien tenga una copia del disco o
un snapshot del servidor sí, porque esa clave va en él:

```bash
sudo mkdir -p /etc/credstore.encrypted
```

```bash
systemd-ask-password -n 'Clave de Ollama:' | sudo systemd-creds encrypt --name=ollama - /etc/credstore.encrypted/ollama
```

El servicio, `/etc/systemd/system/alfalfa-encargo.service`. La shell pasa la clave
descifrada a `OLLAMA_API_KEY` al arrancar (`$$` es un `$` para systemd), y dentro del
sandbox `/run/credentials` queda vacío, así que las órdenes del agente no la ven:

```ini
[Unit]
Description=AlfalfaCode: encargo programado
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=alfalfa
WorkingDirectory=/home/alfalfa/proyecto
LoadCredentialEncrypted=ollama:/etc/credstore.encrypted/ollama
ExecStart=/bin/sh -c 'OLLAMA_API_KEY="$$(cat "$$CREDENTIALS_DIRECTORY/ollama")" exec /home/alfalfa/.local/bin/alfalfa --modelo deepseek-v4.1-flash:cloud "Revisa los cambios de ayer y deja un informe en informes/"'
# Si una orden se come la memoria, muere AlfalfaCode y no lo demás del servidor.
MemoryMax=600M
MemorySwapMax=1G
Nice=10
```

Con el **código de cortesía** en lugar de una clave de proveedor, el servidor no guarda
ninguna clave de pago y el gasto tiene el tope del código. Actívalo una vez como `alfalfa`
(el párrafo «Fuera de macOS…» del control por JSONL), cifra la clave de firma y el código,
y dale al servicio las dos credenciales:

```bash
sudo systemd-creds encrypt --name=alfalfa-firma /home/alfalfa/.alfalfa/firma/clave.pem /etc/credstore.encrypted/alfalfa-firma
```

```bash
systemd-ask-password -n 'Código CUY:' | sudo systemd-creds encrypt --name=alfalfa-codigo - /etc/credstore.encrypted/alfalfa-codigo
```

```ini
LoadCredentialEncrypted=alfalfa-firma:/etc/credstore.encrypted/alfalfa-firma
LoadCredentialEncrypted=alfalfa-codigo:/etc/credstore.encrypted/alfalfa-codigo
Environment=ALFALFA_FIRMA_CLAVE=%d/alfalfa-firma
ExecStart=/bin/sh -c 'ALFALFA_CODIGO="$$(cat "$$CREDENTIALS_DIRECTORY/alfalfa-codigo")" exec /home/alfalfa/.local/bin/alfalfa --modelo alfalfa/<modelo> "…"'
```

La carpeta `~/.alfalfa/firma` y el archivo de `ALFALFA_FIRMA_CLAVE` también quedan vacíos
dentro del sandbox.

En lugar de cron, un temporizador, `/etc/systemd/system/alfalfa-encargo.timer`. Con
cron la clave tendría que estar en claro en el crontab o en un archivo:

```ini
[Unit]
Description=AlfalfaCode: encargo cada noche

[Timer]
OnCalendar=*-*-* 03:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now alfalfa-encargo.timer
```

Para probarlo en el momento, `sudo systemctl start alfalfa-encargo.service`. Lo que
escribe en pantalla queda en `journalctl -u alfalfa-encargo`. El registro de cada sesión
queda en `/home/alfalfa/.alfalfa/sesiones/<id>/`: `<id>.jsonl` con los eventos, permisos
negados incluidos, y `task.json` con el estado. `sudo -iu alfalfa alfalfa --sesiones`
las lista. Un código 3 sale como fallo en `systemctl status`: se negó algo.

**En un servidor pequeño** (1 GB de RAM, como un droplet de 6 $):

- sólo sirven modelos en la nube (Ollama Cloud u otro proveedor); uno local no cabe;
- el motor ocupa poco; lo que gasta memoria son las órdenes del agente (`npm install`,
  compilar, la suite de pruebas). `MemoryMax=` y `MemorySwapMax=` las limitan con lo
  demás del servicio, también dentro de bubblewrap. Por SSH, lo mismo con un ámbito de
  systemd:

  ```bash
  systemd-run --user --scope -p MemoryMax=600M -p MemorySwapMax=1G alfalfa "encargo"
  ```

  Con `--pacai` desde tu programa, pon el límite en el servicio de tu programa: lo heredan
  `alfalfa` y sus órdenes;
- si no tiene swap, uno de 1 a 2 GB evita que se acabe la memoria de golpe:

  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  ```

  ```bash
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

- una cola de encargos, una tras otra y no en paralelo: `{"encargos": ["…", "…"], "modo":
  "serie"}` en un archivo y `alfalfa --cola-encargos cola.json`.

En WSL2, systemd tiene que estar activo (`[boot]` con `systemd=true` en `/etc/wsl.conf`).

### Windows: WSL2

En Windows, AlfalfaCode corre dentro de WSL2 con Ubuntu 24.04; una versión nativa sería otro
proyecto. Todavía no se ha probado en WSL2. Desde PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Dentro de Ubuntu, instala `alfalfa` y bubblewrap como en «Linux: instalar» y da las claves por
variables de entorno como en «Linux: claves». Trabaja en carpetas del sistema de archivos de
Linux (`~/proyectos/…`), no en `/mnt/c/…`: ahí git y el sandbox van mucho más lentos.

El núcleo de WSL2 no suele traer AppArmor activo. Si
`bwrap --ro-bind / / --unshare-user --unshare-net true && echo bien` ya funciona, el perfil
de «Linux: el sandbox» no hace falta (y `apparmor_parser` puede decir que AppArmor no está
activo). Si falla con los mismos mensajes, aplícalo.
