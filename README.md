# AlfalfaCode

Arnés de agentes de programación para modelos de Ollama (local o Ollama Cloud),
de OpenAI, OpenRouter, OpenCode Zen u otro proveedor compatible con OpenAI con tu
propia clave, o de un servicio Alfalfa.
Recibe un encargo, trabaja en la carpeta actual y pregunta antes de ejecutar lo
que no sea una lectura. La orden es `alfalfa`.

Se usa desde la terminal, con `--stdio` (JSONL) para integrarlo en otra
aplicación o con la app de Mac de `alfalfa-mac/`
(`cd alfalfa-mac && swift run AlfalfaCode`).

La app de Mac también se puede instalar sin el repositorio: `alfalfa-mac/build-app.sh`
la empaqueta con su propio Python y el motor dentro, y `alfalfa-mac/publicar.sh` hace
el DMG firmado y notarizado. Detalles en
[alfalfa-mac/EMPAQUETADO.md](alfalfa-mac/EMPAQUETADO.md).

## Instalar

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Después `alfalfa` queda en `.venv/bin/`. También se puede lanzar con
`scripts/alfalfa`, que admite un enlace simbólico desde cualquier carpeta del `PATH`.

Las sesiones, reglas, complementos y hooks del usuario viven en `$ALFALFA_HOME`
(por defecto `~/.alfalfa`).

## Proveedores

El modelo dice con quién se habla:

| Modelo | Proveedor |
|---|---|
| `deepseek-v4.1-flash:cloud`, `ollama/…` | Ollama, local o Ollama Cloud |
| `openai/gpt-6-luna` | OpenAI, con tu clave de la API (Responses API) |
| `openrouter/…`, `opencode/…`, `<proveedor>/…` | un proveedor compatible con OpenAI del catálogo, con tu clave |
| `alfalfa/…` | un servicio Alfalfa, con un código de cortesía |

```bash
alfalfa --proveedores                         # estado, sin secretos
alfalfa --guardar-clave openai                # pide la clave sin enseñarla
alfalfa --fijar-url ollama https://ollama.com
alfalfa --proveedor-por-defecto openai
```

Las claves van al Llavero de macOS (servicio `AlfalfaCode`), nunca a un archivo.
También valen las variables estándar `OLLAMA_API_KEY` y `OPENAI_API_KEY`, y
`ALFALFA_OLLAMA_URL` (por defecto `http://127.0.0.1:11434`). Lo que no es
secreto (URL, proveedor por defecto) queda en `$ALFALFA_HOME/proveedores.json`.
La app de Mac lo configura todo en AlfalfaCode ▸ Ajustes… (⌘,) ▸ Proveedores, que
también abre «Proveedores…» en el selector de modelo.

Con un **código de cortesía** de un servicio Alfalfa, que trae la dirección del
servicio detrás de una arroba (`CUY-XXXX-XXXX-XXXX@servicio.example.com`):

```bash
alfalfa --activar-codigo     # pide el CUY-…@servicio y lo ata a este Mac
alfalfa --cuota              # % usado este mes
```

El repositorio no trae la dirección de ningún servicio: llega con el código. Al
servicio sólo se le manda la parte `CUY-…`, y la dirección queda guardada en
`proveedores.json` cuando el servicio acepta el código. Sin esquema se usa HTTPS
(HTTP sólo en este mismo equipo, como `@127.0.0.1:8090`). Un código suelto, sin
arroba, vale si la dirección ya está guardada o se fija a mano con
`alfalfa --fijar-url alfalfa https://…`.

El código queda atado a este Mac con una clave del Secure Enclave: copiado a otro
ordenador no funciona. Los modelos salen como «GPT-6 Luna - Alfalfa» y la app
enseña una barra con el % de la cuota del mes.

### Catálogo de modelos (models.dev)

AlfalfaCode sabe la ventana de contexto, la salida máxima, si razona y con qué
esfuerzos, si llama herramientas, si ve imágenes y el precio de cada modelo por el
catálogo público de [models.dev](https://models.dev) (MIT, de los creadores de
OpenCode). Funciona así:

- Se baja `https://models.dev/api.json` sin clave, se valida (tamaño máximo,
  identificadores, URL sólo HTTPS, números finitos) y se guarda reducido en
  `$ALFALFA_HOME/catalogo/models.dev.json` con su fecha.
- Se refresca cada 24 horas, al listar los modelos. Sin red vale la última copia;
  tras un fallo no se reintenta hasta pasada una hora.
- La primera vez sin internet vale la copia mínima que viaja con el paquete
  (`alfalfa/catalogo/minimo.json`: OpenAI, Ollama Cloud, OpenRouter, OpenCode Zen
  y NVIDIA).
- El motor nunca espera a la red: en cada turno lee la copia que haya.
- Lo que responde el propio proveedor manda. Por ejemplo, la ventana de
  `/api/show` de Ollama. El catálogo sólo rellena lo que falta: la ventana que usa
  la compactación y el tope de salida que se pide.

```bash
alfalfa --catalogo           # fecha, origen y cuántos modelos gratis hay ahora
```

Los datos de models.dev y su licencia están en
[alfalfa/catalogo/PROCEDENCIA.md](alfalfa/catalogo/PROCEDENCIA.md).

### Gratis ahora

El selector de modelos tiene una sección **Gratis ahora**. Entran los modelos con
coste de entrada y de salida 0 que cumplen todo esto:

- son de un proveedor de nube que habla el formato de OpenAI por HTTPS;
- llaman herramientas;
- no están retirados.

Se cuida qué significa ese 0:

- Se excluyen los proveedores locales (LM Studio y similares), donde 0 es «corre
  en tu máquina».
- Se excluyen los planes de suscripción (`*-coding-plan`, `*-token-plan`,
  OpenCode Go), donde 0 es «incluido en el plan».
- Se excluyen los enrutadores `auto`.

La lista se actualiza sola con el catálogo.

**Gratis no quiere decir privado.** Junto a cada modelo gratis sale el aviso
«Gratis: puede usar tus datos para entrenar». Muchos proveedores pagan los modelos
gratis guardando lo que les mandas para entrenar o mejorar sus modelos. No mandes
nada confidencial a un modelo gratis sin leer antes las condiciones de su
proveedor.

AlfalfaCode no revende nada: cada petición va desde tu Mac al proveedor, con tu
clave.

### Proveedores compatibles con OpenAI

OpenRouter, OpenCode Zen y cualquier otro proveedor del catálogo que hable el
formato de OpenAI funcionan con tu clave y la URL del catálogo.

- Se usa Chat Completions.
- Se usa la Responses API en los modelos que el catálogo marca así (los GPT de
  Zen, por ejemplo).
- Para añadir una clave:

  ```bash
  alfalfa --guardar-clave openrouter     # pide la clave sin enseñarla
  alfalfa --guardar-clave opencode       # OpenCode Zen
  alfalfa --guardar-clave nvidia         # cualquier otro id del catálogo
  ```

  En la app, en Ajustes ▸ Proveedores. Si eliges en el selector un modelo cuyo
  proveedor no tiene clave, AlfalfaCode te dice cuál falta (la variable del
  catálogo, como `OPENROUTER_API_KEY`) y dónde conseguirla, y la guarda ahí mismo.
- La clave va al Llavero y queda atada a la URL que había al guardarla. Si el
  catálogo cambiara luego esa URL, la clave no la sigue.
- Sólo OpenRouter y OpenCode Zen leen también su variable estándar
  (`OPENROUTER_API_KEY`, `OPENCODE_API_KEY`). De los demás no se lee ninguna
  variable, porque su nombre viene de un dato externo.
- Además de los gratis, en el selector salen los modelos de pago que añadas en
  `proveedores.json`:

  ```json
  {"openrouter": {"modelos": ["anthropic/claude-sonnet-5"]}}
  ```

**OpenCode Zen y otras herramientas.** Según su documentación, los modelos de pago
de Zen se pueden usar desde cualquier agente, pero sus modelos gratis
«require OpenCode». Por eso los gratis de Zen no salen en «Gratis ahora». Sus
condiciones de uso también limitan el uso a tu propio uso interno.

### Coste estimado

Con los precios del catálogo, AlfalfaCode estima lo que cuesta cada turno y la
sesión entera. Usa los tokens de entrada, de salida y de caché que devuelve el
proveedor.

- La app lo enseña donde ya enseña los tokens: en el panel de la sesión, en
  `/estado` y en la lista de sesiones.
- Sólo se estima para quien cobra por token: OpenAI y los compatibles. Ollama local
  no cuesta nada; Ollama Cloud y el servicio Alfalfa se pagan con su plan o su
  cuota.
- Es una estimación, no la factura del proveedor.

## Usar

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud "corrige la prueba que falla"
alfalfa --sesiones
alfalfa --reanudar ID "sigue"
alfalfa --bifurcar ID --turno T "prueba otra solución"
alfalfa --comprobar-orden git push
```

## Imágenes

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud --imagen captura.png "¿qué falla aquí?"
```

Como en Codex, cada imagen va al texto con `<image name=[Image #N] path="...">`, se
copia a la sesión reducida a 2048 px como mucho y viaja a Ollama en el campo
`images` de `/api/chat`. Hace falta un modelo con visión. En `--stdio` el mensaje
lleva `"imagenes": ["/ruta/absoluta.png"]`. En la app de Mac se adjuntan con el
botón «+», pegándolas con ⌘V o soltándolas sobre el cuadro de texto, y salen como
miniatura, como en Codex: si el campo de texto escribe la ruta al soltarla, la app la
quita y la adjunta. Una captura arrastrada desde su miniatura flotante se copia a
los adjuntos, porque macOS borra enseguida su archivo temporal.

Cada modelo de `--modelos --json` dice si ve imágenes (`vision`: true, false o null si
no se sabe), de su `/api/show` en Ollama, de lo que dice cada proveedor o el servicio
Alfalfa, o del catálogo de models.dev. Un mensaje con imágenes para un modelo que no las
ve se rechaza antes de empezar el turno, con un error que nombra modelos con visión que
tienes de verdad; si quien integra AlfalfaCode configuró una visión delegada, las imágenes
se describen con ella en vez de fallar. Las imágenes que ya estaban en la conversación (o
que devuelve una herramienta) no rompen el turno de un modelo sin visión: se describen
con la visión delegada o el mensaje dice que no se envían. Si no se sabe si las ve, se le
mandan y el proveedor dirá. En la app, con un modelo sin visión el botón de adjuntar queda
desactivado («Este modelo no ve imágenes»), pegar o soltar una imagen lo avisa, las
miniaturas ya adjuntas se marcan y al enviar se ofrece cambiar a un modelo con visión o
quitarlas.

Si el modelo tiene visión, el agente también puede mirar por su cuenta una imagen
del proyecto con `inspect_image`, como `view_image` en Codex: el mismo modelo de
la sesión recibe la imagen y responde a la pregunta. Sin visión, la herramienta
no se ofrece. Una aplicación que integra AlfalfaCode puede delegarlo en otra
sesión con `alfalfa.ajustes.vision_delegada`.

## Cierre de cada turno

Como la caja «siguiente paso» de Claude Code: cuando un turno cambió archivos o
ejecutó órdenes, el agente llama a `wrap_up` antes de su respuesta. Da un resumen
corto de cómo queda el trabajo y, si lo siguiente lo decide el usuario, una
pregunta con una o dos respuestas. En `--stdio` llega como `cierre` dentro de
`turno_terminado` (`resumen`, `pregunta`, `opciones`). La app de Mac lo pinta como
una tarjeta, y cada botón manda su texto como el siguiente mensaje; el primero
también responde con ⌘↩. En la terminal sale como «Siguiente paso». Sólo lo
ofrece el catálogo de AlfalfaCode; los agentes del integrador no lo ven.

Como las tarjetas «Nueva sesión» de Claude Code, el agente puede proponer una
tarea aparte con `propose_session` (`title` y `prompt`, el encargo completo,
porque la sesión nueva no ve la conversación). Sirve para algo fuera del
encargo o para el siguiente paso que recomienda y no va a hacer ahora. Puede
proponer hasta tres por turno, y no ejecuta nada: viaja en el mismo `cierre`, como
`sesiones: [{titulo, encargo}]`, y se guarda con el turno. La app enseña una
tarjeta por propuesta. «Nueva sesión» empieza el encargo aparte, en la misma
carpeta y con el mismo modelo, esfuerzo y modo, sin cambiar de pantalla: la
sesión sale en la barra lateral. «Hacerlo aquí» se lo pide a la sesión actual. En
la terminal se imprime el encargo para abrir otra sesión a mano.

La sesión que nace así es hija de la que la propuso: se crea con `--padre ID`, que
queda guardado en ella y sale como `padre` en `--sesiones --json`, y con `--titulo`
y el título de la propuesta, que la barra lateral enseña en vez del principio del
encargo. Si el padre vuelve a abrirse, su tarjeta ya no ofrece «Nueva sesión». En la barra
lateral va debajo de su padre, con sangría. Cada vez que la hija termina un turno, la
app le manda al padre una nota con los primeros 400 caracteres de su informe: el
resumen de `wrap_up` o, si no lo hay, su última respuesta. Si el padre está
trabajando, la nota entra al acabar su turno. Si está dormido o cerrado, se despierta
sin cambiarte de pantalla. Un turno que corta el usuario no manda nota.

## Sesiones desde la app

`--marcar ID titulo|fijada|archivada VALOR`, `--eliminar-sesion ID` (a la Papelera
de macOS) y `--solo-bifurcar ID` responden una línea JSON. Son lo que usa el menú de
clic derecho de la barra lateral. En ese menú, «Mostrar en Finder» abre la carpeta del
proyecto y «Mostrar historial en Finder», la de la sesión en `$ALFALFA_HOME/sesiones`.
El botón de carpeta de la cabecera de una sesión también abre la del proyecto.

La ventana de Ajustes (⌘,) guarda el nombre del pie de la barra lateral, la carpeta,
el modelo, el esfuerzo y el modo de permisos de los encargos nuevos, los proveedores,
si se mantiene el Mac despierto mientras trabaja y la búsqueda de actualizaciones. Para revisarla sin clics, `ALFALFA_ABRIR_AJUSTES=modelo`
la abre en esa parte al arrancar (`general`, `proveedores`, `modelo` o
`actualizaciones`).

Al abrir una sesión guardada, la app carga su conversación (`alfalfa --historial ID
--json`) y la deja lista para seguir, como un hilo de Codex: el siguiente mensaje
abre un turno nuevo con `--reanudar ID "mensaje"`, también si la sesión ya estaba
completada. Las interrumpidas o bloqueadas tienen además «Reanudar» para seguir
sin mensaje.

## Modelo, esfuerzo y contexto

El modelo y el esfuerzo se pueden cambiar en una sesión ya empezada, como `/model`
en Codex: la app manda `{"tipo":"ajustes","modelo":…,"esfuerzo":…}` por `--stdio`
y el cambio vale desde el siguiente turno. Cada `contexto` trae, además del total
que mide Ollama, un `desglose` estimado (mensajes, instrucciones, herramientas,
MCP y, si los hay, archivos de memoria, instrucciones MCP y complementos), el uso
`estimado` con lo añadido desde la última medida, el `umbral` de la compactación
automática y la lista de archivos de `memoria` (`AGENTS.md`). Con servidores MCP
también trae las herramientas cargadas con su peso y cuántas siguen diferidas. La
última medición se guarda con la sesión, y el panel la enseña al abrirla, como el
de Claude Code: % usado, cada parte con sus tokens, lo añadido desde la última
medida, la reserva de compactación, el espacio libre y cuánto falta para compactar.

## Compactar

La compactación automática salta al 90 % de la ventana (o en `--compactar-en`).
Además se puede compactar a mano en cualquier momento, como `/compact` de Claude
Code: el botón **Compactar sesión** del panel o `/compactar`, con instrucciones
detrás si quieres centrar el resumen (`/compactar conserva las rutas y los
errores`). Entre turnos se compacta al momento sin abrir turno; con un turno en
marcha, antes de la siguiente respuesta del modelo. Por `--stdio` es
`{"tipo":"compactar","instrucciones":"…"}`. Una sesión guardada se abre sin turno
con `--reanudar ID --esperar` para compactarla.

La conversación enseña la marca «Conversación compactada · 120k → 9k» con el
resumen que ahora lee el modelo, plegado; el historial completo sigue guardado.
El evento es un `evento` de sistema con `compactacion`
(`estado` `en_curso`/`hecha`/`fallida`, `motivo`, `antes`, `despues`, `resumen`).
Cerca del umbral, el pie del cuadro avisa de cuánto falta («12 % hasta compactar»).
`compactacion` lleva también `composicion`: cuántos tokens había por tipo de
mensaje (`tool.content`, `assistant.tool_calls`…), sin su texto.

Lo que lee el modelo tras compactar junta Codex y Claude Code, para que no rehaga
lo que ya hizo ni tome por suyos cambios ajenos:

- El resumen va por apartados, como en Claude Code: encargo actual, restricciones,
  lo hecho, errores, pedidos anteriores (atendidos o no), lo pendiente, en qué
  estaba y un siguiente paso que sólo continúa el encargo actual.
- Tus mensajes anteriores van juntos y marcados como historia ya atendida; sólo
  el último va como mensaje tuyo. Al compactar entre turnos también el último es
  historia.
- Antes de tu último mensaje, como el contexto que Codex vuelve a dar, va la lista
  de archivos que cambió el propio agente: el resto no lo mete en un commit ni lo
  revierte sin preguntar.

Al empezar cada turno el motor da al modelo un informe breve de estado (archivos
que cambió, cambiados por fuera, últimos fallos), con tope, y sólo cuenta el
último: antes iba entero en cada turno y llenaba el contexto.

Las instrucciones base piden, como Codex y Claude Code, que un commit añada por
nombre sólo los archivos del agente (nunca `git add -A` con otros cambios) y que
pregunte si encuentra cambios inesperados.

## Plan del agente

Como en Codex y Claude Code, el agente lleva una lista de tareas con `update_plan`
(pasos `pending`, `in_progress` o `completed`, y una `explanation` opcional). La
app la enseña bajo la conversación mientras trabaja o le quedan pasos. Si
escribes mientras tanto, se queda como «Plan de antes de tu último mensaje» hasta
que el agente mande otro. `--stdio`: evento `plan`
(`{"pasos":[{"paso":"…","estado":"in_progress"}],"explicacion":"…"}`), también al
reanudar una sesión.

## Opciones de cada mensaje

Bajo cada mensaje tuyo la app muestra hace cuánto lo enviaste y tres botones, como
en Claude: copiarlo; **volver** a él, que abre una copia de la conversación justo
antes de ese mensaje con su texto en el cuadro para cambiarlo y reenviarlo; y
**bifurcar**, que abre una sesión nueva con la conversación hasta ese mensaje y su
respuesta. Los dos usan `--solo-bifurcar ID --turno T`; los archivos del proyecto
no cambian.

## Modos de permisos

Como en Claude Code, cada sesión tiene un modo (`--modo` en la terminal, el selector
junto al modelo en la app, o `/modo`). Se guarda con la sesión y se puede cambiar
en cualquier momento; vale desde el siguiente turno.

| Modo | Qué hace |
| --- | --- |
| `auto` (por defecto) | Las órdenes normales corren en el sandbox sin preguntar; piden permiso las peligrosas, las de red y las que salen del sandbox |
| `manual` | Pregunta antes de cada cambio: cada orden que no sólo lee y cada edición de archivos |
| `ediciones` | Aceptar ediciones: edita sin preguntar y pregunta antes de cada orden que no sólo lee |
| `plan` | Sólo lee (sin herramientas de escritura ni órdenes) y propone un plan |
| `omitir` | Aprueba todo sin preguntar, también fuera del sandbox; las reglas `prohibida` siguen mandando. La app pide confirmación |

## Permitir en esta sesión

Como «Yes, and don't ask again for this command» de Codex, la tarjeta de permiso
ofrece **Permitir en esta sesión** (en la terminal, `t`). Se guarda la orden exacta
con su carpeta y si sale del sandbox; mientras dure la sesión no vuelve a preguntar
por ella. Si lo aprobado era el reintento sin sandbox, la próxima vez esa misma
orden sale del sandbox directamente. Nunca vale para órdenes peligrosas ni para las
que una regla manda preguntar, y una bifurcación empieza sin aprobaciones. Por
`--stdio` es `{"tipo":"permiso","id":…,"aprobado":true,"sesion":true}` cuando el
permiso trae `para_sesion`.

Cuando el sandbox bloquea una orden y su reintento fuera funciona, el intento de
dentro ya no deja el turno «bloqueado».

## Procesos en segundo plano

Un juego, una app o un servidor que no termina (`swift run Juego`, `npm run dev`)
se lanza con `run_command` y `background: true`, como los «background terminals»
de Codex: a los ~10 s el agente recibe su id (`p1`) y lo que lleva escrito, el
turno sigue y el programa queda abierto aunque el turno acabe. `command_status`
le enseña al agente la salida nueva, si terminó y con qué código, y lo detiene
con `stop: true`. La salida va a `sesiones/ID/procesos/p1.log`.

Una orden normal que pasa de su plazo (120 s o `timeout_seconds`), como un
`swift build -c release` largo, tampoco se corta: pasa a segundo plano con lo que
llevaba escrito y el agente espera su final con `command_status` y
`wait_for_exit: true` (hasta 300 s por consulta), como Codex con una orden que
sigue viva. `run_checks` sí se corta, porque su resultado es la evidencia.
Detener o cortar una orden cierra también a sus descendientes que se fueron a su
propio grupo de procesos (`swift-driver`, `swift-frontend`): antes seguían
compilando sueltos y el reintento competía con ellos. En eso AlfalfaCode va más
allá que Codex, que en macOS sólo mata el grupo de la orden.

En la app aparecen en la sección **Procesos** del panel (orden, cuánto lleva,
últimas líneas y **Detener**) y en el pie («1 proceso en segundo plano»).
`/procesos` (`/ps`) los lista y `/cerrar-procesos` (`/clean`) los cierra. Por
`--stdio`: evento `procesos` y `{"tipo":"detener_proceso","id":"p1"|"todos"}`.
Como en Codex, se cierran con la sesión; la app no la cierra al cambiar de chat
mientras haya alguno abierto.

## Mantener el Mac despierto

Como «Prevent sleep while running» de Codex: mientras trabaja un turno, el Mac no se
duerme por inactividad. Cuenta todo el turno: los subagentes, las órdenes que lanza el
agente (también las de segundo plano mientras el turno sigue) y la espera de un permiso.
Al terminar, cancelar o fallar el turno se suelta; entre turnos el Mac vuelve a dormirse
como siempre, aunque quede abierto un proceso en segundo plano.

El arnés lo hace él mismo, sin la app: toma una aserción de energía de macOS
(`PreventUserIdleSystemSleep`, por IOKit) con el nombre «AlfalfaCode trabaja en un turno».
La aserción es del proceso del arnés, así que macOS la suelta también si el proceso
muere, aunque sea con `kill -9`: no queda ningún `caffeinate` huérfano. Se ve con:

```bash
pmset -g assertions | grep AlfalfaCode
```

Está **encendido por defecto**: sólo dura lo que dura un turno, no apaga ni enciende la
pantalla y no hace nada cuando el agente no trabaja. Sin él, un encargo largo que dejas
corriendo se para a la media hora porque el Mac se durmió. Para apagarlo:

- en la terminal, `ALFALFA_MANTENER_DESPIERTO=0 alfalfa "…"` o `alfalfa --sin-mantener-despierto "…"`;
- en la app, **Ajustes ▸ General ▸ Mantener el Mac despierto**. El cambio vale ya, también
  en las sesiones que están trabajando.

Mientras lo mantiene despierto, el pie de la sesión enseña «Mac despierto».

Límite: no impide que el Mac se duerma al **cerrar la tapa**. Con la tapa cerrada y sin
corriente se duerme igual. Sólo sigue despierto con la tapa cerrada si está enchufado y
con una pantalla externa (modo «clamshell»). Tampoco usa `pmset` ni pide contraseña de
administrador.

## Mensajes con el agente ocupado

Lo que escribes mientras el agente trabaja queda «en cola» y entra en su siguiente
paso. Debajo del mensaje en cola hay dos botones:

- **Redirigir**, como steer de Codex: el modelo deja la respuesta que estaba
  pensando y la empieza otra vez con tu mensaje dentro, sin cortar el turno.
  También corta las esperas de `wait_agent` y `command_status`; una orden que ya
  corre termina antes.
- **Enviar ahora**, como Claude Code: detiene el turno en marcha y tu mensaje abre
  el siguiente. La sesión sigue abierta («Turno interrumpido por el usuario»).

Entre turnos, un mensaje (o un botón de la caja de cierre) abre el turno siguiente
y el motor lo confirma: deja de verse «en cola» y la sesión pasa a «trabajando».

## Habilidades (skills)

Como Claude Code y Codex, AlfalfaCode lee las habilidades sueltas (`SKILL.md`):
las tuyas en `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills` y
`~/.alfalfa/skills`, y las del proyecto en `.claude/skills`, `.agents/skills`,
`.codex/skills` y `.alfalfa/skills` (de la raíz del repositorio y de la carpeta de
trabajo). El modelo ve su nombre y su descripción; cuando una encaja, la abre con
`abrir_habilidad` y sigue sus pasos con los permisos de siempre. Las de sistema de
Codex (`.system`) no entran porque usan herramientas de Codex. Un SKILL.md que
cambia después de descubrirlo no se abre. `"habilidades_sueltas": false` en
`~/.alfalfa/complementos.json` las desactiva. Las de los complementos autorizados
siguen igual.

## Subagentes

Como `spawn_agent` de Codex y la herramienta Agent de Claude Code, el agente puede
lanzar otros agentes con una tarea acotada. Cada uno tiene su propia conversación y
sus herramientas, trabaja en la misma carpeta y pide los permisos con la misma
tarjeta, que dice qué subagente pide.

- `spawn_agent` lo lanza. Sin `background` espera su informe final y lo devuelve,
  como Claude Code; con `background: true` devuelve su id al momento y varios
  trabajan a la vez, como Codex. `fork_context` le pasa la conversación hasta ahí.
- `wait_agent` recoge sus informes, `send_input` les manda otro mensaje (o una
  tarea nueva si ya terminaron, con lo que aprendieron), `close_agent` los cierra
  y `list_agents` dice cómo van.
- Tipos: `general` (las herramientas del modo actual), `explorador` (sólo lee) y
  los tuyos en `.alfalfa/agents/*.md`, `.claude/agents/*.md` del proyecto,
  `~/.alfalfa/agents` o `~/.claude/agents`, con el formato de Claude Code
  (`name`, `description`, `tools`, `model` en la cabecera e instrucciones debajo).
  `tools: Read, Grep` se traduce a `read_file` y `search`; un modelo de Claude
  (`sonnet`, `inherit`…) usa el de la sesión.
- Como en Codex, hay 6 trabajando como mucho y un subagente no lanza otros. En
  modo Plan también sólo leen.
- Si el agente termina su respuesta con subagentes trabajando, el turno espera a
  que acaben y le pasa sus informes, como las notificaciones de Claude Code. Al
  detener el turno se detienen con él.

La app los enseña en el panel «Subagentes» (tipo, modelo, estado, lo que hacen y
sus pasos) y en el pie («2 subagentes trabajando»); sus pasos no se mezclan con la
conversación principal.

## Probar apps del Mac (control de apps)

Como Computer Use de Codex, el agente puede abrir la app o el juego que construyó y
usarlo como un usuario para comprobar que funciona:

- `app_list`: las apps abiertas y sus ventanas.
- `app_state`: el árbol de accesibilidad de una ventana con elementos numerados
  (`[2] Button titulo="Sumar uno" ayuda="Suma uno al contador" (34,119 91×26)`), su
  tamaño y una captura a la misma escala en puntos. Con `question`, la visión mira la
  captura y responde; con `wait_for`, espera a que salga un texto; con `zoom`, da una
  zona a la resolución completa para leer detalles pequeños.
- `app_action`: `click`, `double_click`, `right_click`, `type`, `key` (`return`,
  `cmd+s`…), `scroll`, `set_value`, `press`, `drag`, `hover`, `menu`
  (`"Archivo > Guardar"`) y `focus`. Se apunta con `target` (el texto visible),
  con un número de elemento o con un punto de la ventana, y `steps` hace varias en
  una llamada. Devuelve la ventana ya cambiada y qué cambió (`cambios`).

Codex lo hace con un servicio nativo y un servidor MCP en Node con una sola
herramienta `js`, y sus acciones traen la ventana al frente. AlfalfaCode compila
la primera vez un ayudante en Swift (`alfalfa/apps/ayudante.swift`, guardado en
`$ALFALFA_HOME/bin`) y usa estas herramientas normales. Lo que hace mejor:

- **No necesita la app delante.** Todo va en fondo, también los menús.
- **Apuntar por texto** (`target`) en vez de por coordenadas o por un número que
  puede haber cambiado, como `find` de las herramientas de Claude.
- **Varios pasos en una llamada** (`steps`), como `computer_batch` de Claude: menos
  idas y vueltas con el modelo.
- **Qué cambió** tras cada acción, y un aviso si la ventana no cambió.
- **Esperar un texto** (`wait_for`) en vez de mirar una y otra vez.
- **Zoom** sobre una zona de la captura, como el de Computer Use de Claude.
- **Menús** de la app por accesibilidad, como `app_menu` de Claude, sin abrirlos.
- **Desplegables** (selectores, botones de menú): un clic en fondo no abre su menú, porque AppKit lo sigue con el ratón de verdad; el clic, también por coordenadas, los abre y elige su opción por accesibilidad, aunque el menú salga de la ventana.
- **Tooltips** en el árbol (`ayuda`), sin pasar el ratón por encima.
- **Niveles por app**, como los de Claude: los navegadores sólo se miran; las
  terminales y los editores se miran y admiten clics sueltos, nunca texto, teclas,
  clic derecho ni arrastrar.

- **Ratón propio, como Codex.** Los clics, dobles clics, clics derechos, arrastres,
  desplazamientos y teclas se mandan a la ventana de la app en segundo plano: tu ratón
  no se mueve, la app no pasa al frente y puedes seguir trabajando en la tuya. Un
  cursor verde dibujado enseña dónde actúa el agente y se va solo
  (`ALFALFA_SIN_CURSOR=1` lo apaga). Una ventana inactiva se traga el primer clic de
  casi todas las vistas, así que, como el servicio de Codex, AlfalfaCode le avisa
  sólo a esa app de que tiene el foco con los registros del gestor de procesos de
  macOS (el truco de yabai). La app de delante no cambia. Para una app que ignore
  los eventos de fondo, `real_mouse: true` usa el ratón de verdad, y sólo si la app
  está delante en ese punto.
- **Números de elemento seguros.** Un número se resuelve con la ruta y el rol de la
  última lectura: si la ventana cambió, la acción falla en vez de pulsar otra cosa.
- **Permiso por app.** La primera vez que se toca una app se pregunta («Controlar una
  app», con «Permitir en esta sesión»), y la tarjeta dice su nivel. Los títulos de
  ventana de las apps no aprobadas no se enseñan. Los gestores de contraseñas, Acceso
  a Llaveros, Ajustes del Sistema, los editores de scripts y otros agentes nunca se
  manejan ni se miran; escribir en una terminal sería salir del sandbox sin preguntar.
- **Modos.** En el modo Plan y en las revisiones no hay `app_action`.

Necesita los permisos de **Accesibilidad** y **Grabación de pantalla** de la app que
lanzó AlfalfaCode (Terminal o la app AlfalfaCode), en Ajustes del Sistema →
Privacidad y seguridad. La prueba de punta a punta abre una app de verdad:
`PRUEBA_APPS_DE_VERDAD=1 python3 -m pytest tests/test_control_de_apps.py`.

## SwiftPM y apps dentro del sandbox

SwiftPM abre su propio sandbox y macOS no deja anidarlo. Dentro del de AlfalfaCode,
`swift build/run/test/package` recibe `--disable-sandbox`, también cuando lo llama
un script (`sh "Abrir app.command"`, `make`): las órdenes del sandbox llevan en el
PATH envoltorios de `swift`, `swift-build`… y `xcrun`. Abrir una app (`open`) no
funciona dentro del sandbox; el agente lo pide directamente con
`sandbox_permissions: "require_escalated"`, como en Codex.

## Órdenes con «/»

Como en Codex, escribir «/» en el cuadro de la app abre un menú de órdenes que se
filtra por prefijo: ↑↓ para moverse, ↩ para ejecutar, ⇥ para completar y Esc para
cerrar. Las que no valen en ese momento salen atenuadas, por ejemplo `/revisar`
con un turno en marcha. También aceptan su nombre en inglés (`/model`, `/compact`,
`/init`…).

`/nuevo`, `/modelo`, `/esfuerzo`, `/compactar`, `/estado`, `/revisar`,
`/inicializar` (crea un AGENTS.md, como `/init` de Codex), `/renombrar`, `/fijar`,
`/bifurcar`, `/archivar`, `/copiar`, `/exportar` (la conversación en Markdown) y
`/detener`. Lo que empieza por «/» y no es una orden, como una ruta, se envía como
mensaje normal.

## Cargar un chat de Ollama

Como `/import` de Codex, que trae chats de Claude Code: en la app, «Cargar chat de
Ollama» (en Nuevo encargo) lista los chats de la app de Ollama y copia el elegido
en una sesión nueva de la carpeta actual. Sus mensajes de usuario y asistente
quedan como contexto, y el siguiente mensaje la continúa. Desde la terminal:

```bash
alfalfa --chats-ollama
alfalfa --importar-ollama ID_DEL_CHAT
alfalfa --reanudar SESION "sigue desde aquí"
```

La base de Ollama (`~/Library/Application Support/Ollama/db.sqlite`) sólo se lee:
conexión de solo lectura, `query_only` y una transacción que se deshace al final.
La lista sólo enseña títulos y recuentos. No se traen imágenes adjuntas,
herramientas ni respuestas a medio escribir.

## Actualizar

Cada versión es una etiqueta `vX.Y.Z` de este repositorio.

```bash
alfalfa --version
alfalfa --buscar-actualizacion
alfalfa --actualizar
```

En un clon, `--actualizar` avanza la rama hasta la etiqueta nueva (sólo si no hay
cambios sin guardar ni commits propios) y reinstala el paquete; instalado con pip,
lo reinstala desde GitHub en esa etiqueta. Así llegan también las dependencias
nuevas. Dentro de AlfalfaCode.app no instala nada (el bundle está firmado): dice
dónde descargar el DMG nuevo. Si actualizas un clon con `git pull` y falta la
librería de búsqueda web, la app de Mac la instala sola al abrirse. En la
terminal, usa `alfalfa --instalar-buscador` o `pip install -e .`. Los `.venv`
creados con `uv`, que no traen pip, se instalan con `uv pip`. La app de Mac tiene
el mismo botón en
la barra lateral (flecha hacia abajo, o el menú del nombre), en Ajustes ▸
Actualizaciones y en el menú AlfalfaCode → «Buscar actualización…». Si la versión nueva cambia `alfalfa-mac/`, hay que volver a
compilarla con `swift build`.

Al publicar una versión, sube `__version__` en `alfalfa/__init__.py` y crea la
etiqueta con el mismo número. Una aplicación que lo integra debería fijar la
versión en sus dependencias.

## Qué incluye

| Módulo | Hace |
|---|---|
| `proveedores/nativo/` | El bucle del agente (`NativeRuntime`) y sus herramientas |
| `terminal.py`, `stdio.py` | La orden `alfalfa` y el protocolo JSONL para apps |
| `permisos/`, `seatbelt/`, `reglas/` | Permisos bajo demanda, sandbox de macOS y reglas de `reglas.toml` |
| `mcp_cliente/`, `complementos/`, `hooks/` | Servidores MCP, complementos y hooks compatibles con Claude Code |
| `compactacion/`, `agents_md/`, `sesiones/` | Compactación del contexto, `AGENTS.md` y sesiones con id por turno |
| `apply_patch/`, `edicion_tolerante/` | Edición por parches y reemplazos tolerantes |
| `catalogo/`, `proveedores/compatible.py` | Catálogo de models.dev y proveedores compatibles con OpenAI |

## Integrarlo en otra aplicación

El arnés no importa nada de quien lo integra. Lo que esa aplicación necesita
cambiar está en `alfalfa/ajustes.py`: instrucciones por defecto, dónde buscar los
binarios, cómo llegar al buscador web, qué repositorio da por propio y las URL
y claves de los proveedores (`ollama_url`, `ollama_clave`, `openai_clave`,
`clave_compatible`, `alfalfa_servicio_url`).
También cómo se nombran el runtime y el integrador en los textos (`nombres`), a
qué ramas puede avanzar un agente con `git merge --ff-only` (`ramas_de_avance`) y
qué órdenes propias de su proyecto da por seguras (`orden_permitida`). La
aplicación los registra al cargarse.

## Pruebas

```bash
.venv/bin/python -m pytest -q
cd alfalfa-mac && swift test
```

El estilo se comprueba con `ruff check .` en el motor y con `swiftlint` dentro de
`alfalfa-mac/`. Las reglas, la deuda que queda y cómo partir los archivos grandes están
en [docs/ESTILO.md](docs/ESTILO.md).

## Licencia

AlfalfaCode es MIT; ver [LICENSE](LICENSE). Las piezas adaptadas de terceros
conservan la suya, como se cuenta en «Procedencia».

## Procedencia

Varias piezas son adaptaciones de [OpenAI Codex](https://github.com/openai/codex)
(Apache-2.0). Cada una lleva junto a su código `LICENSE`, `NOTICE` y
`PROCEDENCIA.md` con lo que se tomó y cómo se cambió. El código de terceros de la
app de Mac está en [alfalfa-mac/TERCEROS.md](alfalfa-mac/TERCEROS.md).

La búsqueda web usa la librería [CuySearch](https://github.com/0sw2ld0/CuySearch)
(MIT) como dependencia.

El catálogo de modelos viene de [models.dev](https://models.dev) (MIT); ver
[alfalfa/catalogo/PROCEDENCIA.md](alfalfa/catalogo/PROCEDENCIA.md).
