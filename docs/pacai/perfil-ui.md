# Perfil `ui` de PACAI (borrador 1)

> Estado: **borrador**, con las preguntas del primer borrador resueltas (sección 9). El motor de AlfalfaCode
> ya lo habla, y la app de Mac, la extensión de VS Code y una demo web dibujan las pantallas (sección 8).
> Esquema: [`pacai-ui-1.schema.json`](../../alfalfa/pacai/pacai-ui-1.schema.json). Ejemplo completo:
> [`ejemplos/ui-tickets.jsonl`](ejemplos/ui-tickets.jsonl).

El perfil `ui` deja que el agente dibuje pantallas dentro del chat mientras conversa: tarjetas, tablas,
formularios, botones, gráficos. El cliente las va mostrando a medida que llegan, y lo que el usuario hace en
ellas (pulsar un botón, enviar un formulario) vuelve al agente como un mensaje más de la conversación.

Funciona con PACAI 0.3 tal como está: no añade tipos de mensaje. Usa tres piezas que el núcleo ya tiene:

| Pieza del núcleo | Para qué la usa `ui` |
| --- | --- |
| `hello.payload.render` | El cliente declara qué sabe dibujar (su catálogo de componentes). |
| bloque `structured` en `message_start`, `message_delta` y `message_end` | El agente manda las pantallas, por partes. |
| bloque `structured` en `input` | El cliente devuelve las acciones del usuario. |

**AlfalfaUI** es el nombre del kit de AlfalfaCode que implementa este perfil: los renderizadores de la app
de Mac (SwiftUI) y de la extensión de VS Code (web), y el traductor del motor. El formato es de PACAI y vale
para cualquier agente.

## 1. Ideas de partida

- **El cliente manda en lo que se dibuja.** El agente solo puede usar los componentes que el cliente declaró.
  Nada de HTML, scripts ni estilos libres.
- **Se dibuja mientras llega.** Una pantalla es una lista plana de nodos. Cada nodo llega solo y se muestra en
  cuanto llega; los hijos pueden llegar después que el padre.
- **Cambiar es mandar lo que cambia.** Un nodo con un `id` que ya existe reemplaza al anterior. En el turno
  siguiente el agente manda solo los nodos nuevos o distintos.
- **Una pantalla siempre puede reconstruirse.** Al cerrar el mensaje, el agente manda la pantalla completa.
  Un cliente que reanuda o llega tarde no tiene que reconstruir la historia.
- **Todo se valida con JSON Schema.** Lo que ya usa PACAI. No hay un lenguaje propio que analizar.

## 2. Negociación

El cliente pide el perfil en `profiles` y describe lo que sabe dibujar en `render.ui`:

```json
{"type": "hello", "sender": "client", "payload": {
  "versions": ["0.3"], "profiles": ["work", "ui"], "extensions": ["alfalfa"],
  "render": {"ui": {
    "version": "1",
    "catalog": "base",
    "components": {
      "Ticket": {"description": "Un ticket con su estado", "props": {"type": "object",
        "properties": {"title": {"type": "string"}, "status": {"enum": ["open", "closed"]}},
        "required": ["title"]}, "children": false, "events": ["press"]}
    },
    "actions": ["send", "set", "open_url"],
    "limits": {"max_nodes": 300, "max_depth": 12}
  }}
}}
```

| Campo | Significado |
| --- | --- |
| `version` | Versión del perfil. Este borrador es la `"1"`. |
| `catalog` | `"base"` (los 12 componentes de la sección 4) o `"none"` (solo los de `components`). Por defecto, `"base"`. |
| `components` | Componentes propios del cliente. Cada uno: `description` (para el modelo), `props` (JSON Schema), `children` (si admite hijos), `events` y, opcional, `example`: de 1 a 4 nodos `pacai.ui/1` que enseñan al modelo a usarlo (enlaces `{"$": …}`, la forma de su `query`…). Un nombre propio no puede pisar uno del catálogo base. |
| `actions` | Acciones que el cliente sabe ejecutar. Por defecto, las tres. |
| `limits` | Nodos por pantalla y profundidad máxima. Por defecto, 300 y 12. |

Si el agente admite el perfil, lo incluye en los `profiles` de `session_started`. Si el cliente pide `ui`
sin `render.ui`, vale el catálogo base con todas las acciones y los límites por defecto. Un agente que no
negoció `ui` no manda pantallas; un cliente que recibe un bloque `pacai.ui/1` sin haberlo negociado lo ignora.

## 3. Pantallas: lo que manda el agente

Una **vista** es una pantalla con nombre (`view`). Vive toda la sesión: el agente puede actualizarla en
mensajes posteriores. Dónde se muestra lo sugiere el agente con `placement` (`inline`: en el mensaje donde
apareció por primera vez; `latest`: en el último mensaje que la tocó). Sin esa pista, decide el cliente.

El agente la manda en bloques `structured` con `schema: "pacai.ui/1"`, dentro de los mensajes de siempre:

```json
{"type": "message_delta", "payload": {"message_id": "m_00000011", "blocks": [
  {"type": "structured", "schema": "pacai.ui/1", "data": {
    "view": "tickets", "op": "patch", "rev": 1,
    "nodes": [
      {"id": "root", "component": "Stack", "children": ["titulo", "tabla", "nuevo"]},
      {"id": "titulo", "component": "Text", "props": {"text": "3 tickets abiertos", "style": "title"}}
    ]
  }}
]}}
```

| Campo | Significado |
| --- | --- |
| `view` | Nombre de la vista, único en la sesión. `[a-z][a-z0-9_]*`. |
| `op` | `patch`: mezcla los nodos con los que ya hay. `replace`: la vista completa; lo que no venga se borra. |
| `rev` | Revisión de la vista. Sube en cada bloque que la toca. El cliente descarta un bloque con `rev` menor o igual que el que ya tiene. |
| `nodes` | Nodos nuevos o que cambian. |
| `remove` | Ids de nodos que se borran (solo en `patch`). |
| `state` | Valores iniciales o nuevos del estado de la vista (sección 5). En `patch` se mezclan por clave. |
| `alt` | Resumen en texto de la vista, para accesibilidad, registros y clientes que no dibujan. Obligatorio en `replace`. |
| `placement` | Opcional. `inline` o `latest` (ver arriba). Vale la última pista recibida para esa vista. |

### Nodos

```json
{"id": "enviar", "component": "Button", "props": {"label": "Crear", "variant": "primary"},
 "events": {"press": {"send": "crear_ticket", "with": ["title", "priority"]}}}
```

| Campo | Significado |
| --- | --- |
| `id` | Único dentro de la vista. La raíz se llama `root`. |
| `component` | Componente: uno del catálogo negociado. |
| `props` | Propiedades, según el esquema del componente. Un valor `{"$": "nombre"}` se lee del estado de la vista. |
| `children` | Ids de los hijos, en orden. Solo en componentes que admiten hijos. |
| `events` | Qué hacer en cada evento del componente (sección 5). |

Reglas para dibujar mientras llega:

1. El cliente dibuja desde `root`. Un hijo que todavía no llegó no ocupa sitio (o muestra un hueco de
   carga); cuando llega, aparece en su lugar.
2. Un nodo con un `id` conocido reemplaza al anterior **entero** (no se mezclan sus `props`).
3. Un nodo que nadie referencia no se dibuja. Un ciclo en `children` se corta en el primer nodo repetido.
4. Al terminar el mensaje (`message_end`), el agente manda un `replace` de cada vista que el mensaje tocó.
   Es la foto de referencia: si difiere de lo que el cliente fue montando, gana la foto.

### Validación

- **El agente valida antes de mandar.** Un nodo con un componente fuera del catálogo, propiedades que no
  cumplen su esquema o más nodos que `max_nodes` no sale. AlfalfaCode se lo dice al modelo para que lo corrija.
- **El cliente es tolerante.** Si aun así le llega un nodo que no entiende, no lo dibuja y sigue. Nunca
  rompe la sesión por una pantalla.

## 4. Catálogo base

Doce componentes que cualquier cliente con `catalog: "base"` sabe dibujar. El esquema exacto de sus
propiedades está en `pacai-ui-1.schema.json` (`$defs/base_props`).

| Componente | Propiedades principales | Hijos | Eventos |
| --- | --- | --- | --- |
| `Stack` | `direction` (`vertical`/`horizontal`/`grid`: tarjetas en columnas según el ancho), `gap` (`none`/`small`/`medium`/`large`) | sí | — |
| `Card` | `title`, `subtitle`, `image` (`https:` o `data:image/…`), `meta` (línea pequeña: «19 nov · Salud»), `tags` (etiquetas), `detail` (texto largo que el usuario despliega con «Ver más», sin llamar al agente) | sí | `press` |
| `Text` | `text` (texto plano, sin markdown), `style` (`body`/`title`/`subtitle`/`caption`/`muted`) | no | — |
| `Table` | `columns` (`[{key, label}]`), `rows` (lista de objetos) | no | `row_press` |
| `List` | `items` (`[{id, title, subtitle?}]`) | no | `item_press` |
| `Button` | `label`, `variant` (`primary`/`secondary`/`danger`), `disabled` | no | `press` |
| `Form` | `submit_label` | sí | `submit` |
| `Input` | `name`, `label`, `kind` (`text`/`number`/`multiline`/`email`/`date`), `placeholder`, `required` | no | `change` |
| `Select` | `name`, `label`, `options` (`[{value, label}]`), `multiple`, `required` (empieza sin opción elegida y el `Form` no se envía hasta elegir una) | no | `change` |
| `Chart` | `kind` (`bar`/`line`/`pie`), `labels`, `series` (`[{name, values}]`) | no | — |
| `Image` | `src` (`https:` o `data:image/…`), `alt` (obligatorio) | no | — |
| `Code` | `code`, `language` | no | — |

El aspecto (colores, tipografía, márgenes) es del cliente: la app de Mac dibuja con SwiftUI y VS Code con su
tema. El agente describe **qué** hay, no **cómo** se ve.

## 5. Estado y acciones

### Estado de la vista

Cada vista tiene un estado: un objeto plano de valores. Los campos de un formulario (`Input`, `Select`) leen
y escriben en él la clave de su `name`. Cualquier propiedad puede leerlo con `{"$": "clave"}`. El agente da
valores iniciales con `state`; el usuario los cambia al escribir. El estado vive en el cliente: el agente solo
lo ve cuando una acción `send` se lo manda. (La ruta `/client/ui/` de `shared_state` queda reservada para una
versión futura; un cliente de la versión 1 no escribe en ella.)

### Acciones

En `events`, cada evento lleva una acción o una lista de acciones, que se ejecutan en orden:

| Acción | Forma | Qué hace |
| --- | --- | --- |
| `send` | `{"send": "nombre", "with": ["clave", …]}` | Devuelve la acción al agente con los valores pedidos del estado. Sin `with`, manda todo el estado. |
| `set` | `{"set": {"clave": valor}}` | Cambia el estado de la vista. No llama al agente. |
| `open_url` | `{"open_url": "https://…"}` | Abre un enlace. Solo `https:`. El cliente puede pedir confirmación. |

Como mucho un `send` por evento. Las acciones solo se disparan por un gesto del usuario: el cliente nunca
manda un `send` por su cuenta. El evento `change` solo admite `set`: llamar al modelo en cada tecla gastaría un
turno por pulsación. No hay acción que ejecute una herramienta sin pasar por el modelo.

### Lo que manda el cliente

Un `send` se convierte en un `input` con un bloque `structured` de `schema: "pacai.ui.action/1"` y, delante,
un bloque `text` con un resumen legible:

```json
{"type": "input", "sender": "client", "payload": {"blocks": [
  {"type": "text", "text": "Crear ticket: «Error al iniciar sesión», prioridad alta"},
  {"type": "structured", "schema": "pacai.ui.action/1", "data": {
    "view": "tickets", "rev": 3, "node": "enviar", "event": "press", "name": "crear_ticket",
    "values": {"title": "Error al iniciar sesión", "priority": "high"}
  }}
]}}
```

- Sin una ejecución en marcha, el `input` abre una nueva (`new_run`, el modo por defecto). Con una en
  marcha, va como `steer` con su `run_id`.
- `item` dice sobre qué elemento fue el evento: en `row_press`, la fila pulsada (el objeto de `rows`); en
  `item_press`, el `id` del elemento de la lista. Los demás eventos no lo llevan.
- `rev` es la revisión de la vista que el usuario tenía delante. Si el agente ya la cambió, la acción puede
  estar desfasada; el agente se lo dice al modelo y el modelo decide.
- Tras un `send`, el cliente desactiva el control que lo disparó hasta que llegue una revisión nueva de la
  vista o termine la ejecución. Evita el doble envío.
- El bloque `text` es lo que el usuario «dijo». Un cliente que no conozca el perfil, o un registro, lo
  entiende sin dibujar nada.

## 6. Reanudar y llegar tarde

El `replace` de cada `message_end` es la foto de la vista. Un `snapshot` lleva los últimos mensajes en
`last_messages`; con el último `message_end` de cada vista, el cliente la dibuja como estaba. El estado que el
usuario había escrito y no envió se pierde: vive solo en el cliente que lo tenía.

## 7. Seguridad

- **Sin código.** Ni HTML, ni scripts, ni CSS, ni markdown que se interprete. `Text` es texto plano.
- **Enlaces e imágenes.** `open_url` solo con `https:` y siempre por un gesto del usuario. `Image` solo con
  `https:` o `data:image/png|jpeg|gif|webp`. El cliente puede no cargar imágenes remotas (revelan que la
  pantalla se abrió) o pedir permiso.
- **Nada de credenciales.** El agente no debe pedir contraseñas ni tarjetas en un formulario, y el cliente
  puede negarse a dibujar un `Input` cuyo `name` o `label` lo sugiera. Un formulario del agente nunca debe
  parecer una pantalla del sistema o de otra aplicación.
- **Límites.** El cliente aplica `max_nodes`, `max_depth` y `limits.max_message_bytes` del núcleo aunque el
  agente no lo haga.
- **Lo que vuelve es entrada del usuario.** Una acción `send` llega al agente con la misma confianza que un
  mensaje escrito. Los permisos de las herramientas no cambian por venir de un botón.

## 8. Cómo lo hace AlfalfaCode (informativo)

Esto no forma parte del perfil; es cómo encaja en el motor. El código está en `alfalfa/pacai/ui.py`, y las
pruebas en `tests/test_pacai_ui.py`.

1. **Negociación.** `alfalfa --pacai` ofrece `ui` en su `hello`. Si el cliente lo pide, el transporte valida su
   `render.ui` contra `$defs/declaration`. Una declaración inválida recibe `error` `invalid_message` y la
   sesión sigue sin el perfil.
2. **Prompt.** Con `ui` negociado, el motor añade al prompt de sistema, en cada petición, una sección generada
   desde el catálogo (`Catalogo.manual()`): los componentes, sus propiedades y eventos, las acciones que el
   cliente ejecuta y cuándo conviene dibujar en vez de escribir.
3. **Salida del modelo.** El modelo escribe la pantalla en un bloque cercado, con un nodo JSON por línea:

   ````text
   ```pacai-ui view=tickets placement=latest
   {"id":"root","component":"Stack","children":["titulo","tabla"]}
   {"id":"titulo","component":"Text","props":{"text":"3 tickets abiertos","style":"title"}}
   {"state":{"priority":"medium"}}
   ```
   ````

   Además de los nodos, el bloque admite tres líneas especiales: `{"remove":[ids]}`, `{"state":{…}}` y
   `{"alt":"…"}`. El traductor separa el texto de los bloques (`partir`) y valida cada nodo contra el catálogo.
   Cada bloque sale como un `patch` en su propio `message_delta`, y al final el `message_end` lleva la foto
   (`replace`) de cada vista que el mensaje tocó. El bloque no sale como texto; lo que el modelo escriba antes
   y después, sí. Una valla sin cerrar (respuesta cortada) se procesa igual.
4. **Lo rechazado.** Los nodos que no pasan la validación no salen. El cliente recibe
   `alfalfa.ui_rejected` (`view`, `errors`) si negoció la extensión `alfalfa`.
5. **Historia.** El modelo conserva en su historia el bloque tal como lo escribió, así puede mandar solo los
   nodos que cambian en el turno siguiente.
6. **Acciones.** Un `input` con `pacai.ui.action/1` se valida contra `$defs/action` y llega al modelo como un
   mensaje del usuario: el texto del cliente, `[Acción en la pantalla]` y los datos en JSON. Si la `rev` es
   anterior a la actual, se añade un aviso de que la acción puede estar desfasada. Sin el perfil, la acción
   recibe `not_negotiated`.
7. **Granularidad.** El motor entrega el texto del modelo mensaje a mensaje, no token a token. Por eso hoy las
   pantallas llegan cuando el modelo termina cada mensaje, no mientras lo escribe. El formato ya admite que
   llegue nodo a nodo; queda pendiente pasar el texto en vivo desde el proveedor.
8. **Demo web.** [`docs/demos/pacai-ui/`](../demos/pacai-ui/README.md) tiene un renderizador web del catálogo
   base (`ui.js`, sin dependencias) y un chat que lo usa, con AlfalfaCode o con un agente simulado.
9. **App de Mac.** Pide `ui` en su `hello` con el catálogo base (`PacaiScreens.swift`). El modelo puro está en
   `AlfalfaKit/Screens/` (`Screen`, `ScreenUpdate`, `ScreenNode`, `ScreenValue`): aplica los bloques y resuelve los
   eventos sin SwiftUI, y lo prueban `ScreenTests` y `ScreenFlowTests`. Las pantallas llegan con el texto de su
   mensaje (`AgentEvent.screens`) y entran en la conversación como elementos `.screen`, que `ScreenTimeline`
   coloca según `placement`. Las vistas (`AlfalfaCode/Session/Screens/`) sólo dibujan: tabla, lista, formulario,
   campos, desplegable, gráfico con Swift Charts, imagen y código. Con `--stdio` v2, una acción sale como un
   mensaje con su texto legible.
10. **Extensión de VS Code.** Pide `ui` en su `hello` (`lib/pacai.js`) y dibuja con una copia idéntica del `ui.js`
    de la demo (`media/ui.js`; una prueba falla si se separan), cargado como módulo por `media/ui-global.js`. Las
    acciones van del panel a la extensión (`accionPantalla`) y de ahí al agente; los enlaces los abre la
    extensión tras preguntar, y las imágenes remotas no se cargan (se ve su `alt`).
11. **Pendiente.** Que el modelo se entere de los nodos rechazados para corregirlos, y cómo se ven las pantallas
    al reanudar una sesión guardada (sin comprobar). En la app de Mac y en VS Code las pantallas de un mensaje
    salen debajo de todo su texto, no entre los párrafos donde las escribió el modelo.

12. **Un ejemplo por componente en el prompt.** `Catalogo.manual()` añade, tras la lista de componentes, un nodo de
    ejemplo por componente base (`EJEMPLOS_BASE` en `alfalfa/pacai/ui.py`) y el `example` de cada componente propio.
    Un ejemplo de la app que no pasa su propio esquema no entra. Son unos 600 tokens más de entrada; en el piloto
    con GPT-6 Luna y Claude Haiku 5.5 (52 tareas, 624 peticiones), las pantallas válidas a la primera pasaron del
    86–89 % al 97 %. `scripts/muestra_ejemplos_ui.py` repite la medida.
13. **La valla vale aunque no empiece la línea.** Algunos modelos (GPT-6 Luna) escriben una frase o su razonamiento
    y abren el bloque sin saltar de línea: `Aquí está:```pacai-ui view=x`. `partir` lo reconoce y deja el texto de
    delante como texto normal; el contenido del bloque no cambia. No cuenta si tras la valla sigue la frase, si el
    texto de delante lleva comillas invertidas (una valla citada en `código`) o si está dentro de otro bloque de
    código.

## 9. Decisiones (24 de noviembre de 2026)

Las preguntas abiertas del primer borrador, ya resueltas:

1. **Dónde se muestra una vista que se actualiza: pista del agente.** El campo opcional `placement` de una
   actualización dice `inline` (la vista se queda en el mensaje donde nació) o `latest` (se mueve al último
   mensaje que la tocó, y en su sitio anterior queda una foto estática). Sin `placement`, decide el cliente.
   Vale la última pista recibida.
2. **`change` no puede llevar `send`.** Solo admite `set`. Un filtro en vivo se hace con estado local; para
   llamar al agente hace falta un gesto explícito (`press`, `submit`, `row_press`, `item_press`). Así no se
   gasta un turno del modelo por tecla. El esquema lo comprueba.
3. **No hay acción `call` en la versión 1.** Todo lo que vuelve al agente pasa por el modelo con `send`. Una
   acción que ejecute una herramienta sin el modelo abre más superficie de seguridad; se reevaluará con casos
   reales.
4. **El estado de la vista es local.** Vive en el cliente y solo viaja con `send`. La ruta `/client/ui/<vista>`
   de `shared_state` queda reservada para una versión 2 que refleje el estado en vivo; un cliente de la
   versión 1 no debe escribir en ella.
5. **Coste en tokens: medido.** Con la vista final del ejemplo (tabla de 3 tickets y formulario de 2 campos),
   contada con el tokenizador `o200k_base` (el de Claude es distinto; las proporciones sirven de guía):

   | Forma | Tokens |
   | --- | --- |
   | La misma información en markdown, sin interacción | 75 |
   | `pacai-ui`, vista completa | 276 |
   | `pacai-ui` con claves de una letra (`c`, `p`, `kids`, `on`) | 276 |
   | Sintaxis de línea al estilo OpenUI Lang (aproximada) | 197 |
   | `pacai-ui`, turno siguiente con `patch` (2 nodos) | 158 |

   Conclusiones: una pantalla cuesta unas 3,7 veces lo que el texto equivalente, y un 40 % más que una
   sintaxis propia de línea. El `patch` ahorra un 43 % frente a reescribir la vista. Se acepta ese 40 % a cambio
   de validar con JSON Schema y no tener que escribir un analizador en cada cliente.
6. **Claves legibles.** El primer borrador usaba claves de una letra (`c`, `p`, `kids`, `on`) para ahorrar
   tokens, pero no ahorran nada: `c` y `component` son un token cada una. El coste está en la puntuación de
   JSON y en los datos. Los nodos usan `component`, `props`, `children` y `events`, que el modelo y las
   personas entienden mejor, y la declaración de un componente propio dice `children` en vez de `kids`.

## 10. Prototipo: PACAI UI Lang y piezas declarativas (detrás de un flag)

Sale de la 3.ª y la 4.ª lluvia de ideas. **Está apagado por defecto**: sin `ALFALFA_PACAI_UI_LANG=1` en el entorno
del motor no cambia nada y el JSON de arriba sigue siendo el camino normal. Se revierte apagando el flag. La app de
Mac no lo conoce y no hace falta: el motor sigue mandando `pacai.ui/1`.

**PACAI UI Lang** (`alfalfa/pacai/ui_lang.py`) es una forma compacta, sólo datos, de escribir los mismos nodos. El
motor la traduce exacta a `pacai.ui/1` y los clientes no cambian:

```text
root Stack direction=vertical gap=medium > filtros,cifras,p_bajo,fuente
p_bajo Panel "Lo que más bajó" > bajo_top
bajo_top PriceBars query={"tipo":"productos","orden":"baja","limite":6}
```

- Una línea por nodo, `id Comp ["posicional"] clave=valor … > hijo,hijo`, en DFS desde `root`, en una valla
  ```` ```pacai-ui-lang view=nombre ````. El posicional es la prop que declara el catálogo (`positional` en un
  componente propio; en los base, `Text.text`, `Card.title`, `Button.label`…), un solo valor: un texto o un enlace
  `$clave`. `query`, objetos y listas van en JSON compacto; una palabra suelta es un enum; `$clave` es
  `{"$": "clave"}`; `@evento={…}` lleva sus acciones.
- **Herencia:** un componente propio declara `inherit` (`{"period": "periodo"}`) y el motor pone el enlace si la prop
  falta. Un valor explícito gana. Un default sólo se rellena si el esquema de la prop lo declara.
- **Cambios (versión 3):** el mismo bloque `view=…`. Si la vista ya existe es un cambio contra la última revisión
  que el motor le enseñó al modelo; si no, un alta. **El modelo nunca escribe `rev`**: si lo pone, se ignora y queda
  en `Vistas.reparaciones`. Cada línea es el nodo entero: un id nuevo es un alta y uno que ya existe se sustituye
  (con todos sus hijos tras `>`). `-id` borra el nodo, lo quita de su padre y se lleva lo que sólo colgaba de él; un
  hijo que la línea del padre deja fuera también se va. Todo el bloque se aplica o nada. El modelo ve la pantalla con
  sus líneas enteras (`Vistas.resumen_lang`). La versión 1 (`~id`, `+>`) y el `rev` escrito por el modelo (versión
  2) se quitaron: eran donde más fallaba.
- **Reparaciones seguras:** una línea idéntica a una que ya vale se ignora, en el reintento de un alta la línea de
  un id que ya existe lo sustituye, y un `rev` del modelo se ignora. Nunca cambian lo que se pidió.
- **Streaming:** `Flujo` valida cada línea completa al llegar y publica un nodo en cuanto él y sus ancestros
  existen, con el prefijo contiguo de hijos llegados: cada publicación es un árbol válido que sólo crece. Una línea
  mala se descarta sin corregirla y, al cerrar, se piden todas de una vez con un solo reintento. Hoy el motor
  entrega el texto del modelo por mensajes enteros (y el servicio Alfalfa tampoco lo trocea), así que en la práctica
  las publicaciones salen juntas al cerrar el mensaje.
- **Prompt:** `ui_lang.prompt` lo genera del catálogo, con 15 líneas como mucho, y detrás van los ejemplos de cada
  componente (`Catalogo.ejemplos()`, los mismos que el prompt JSON) escritos en la sintaxis compacta.

**Piezas declarativas** (`alfalfa/pacai/piezas.py`, y en la web la opción `piezas` de `Vista`). Sólo con el flag y si
el cliente declara `"pieces": true` en `render.ui`:

| Pieza | Props | Qué hace |
| --- | --- | --- |
| `Calc` | `name`, `preset: "margen"`, `compra`, `venta`, `cantidad` | Publica `name.pct` (fracción), `name.u` y `name.total` (dinero) y `name.perdida` (bool). Margen = (venta − compra) / venta. |
| `si` | `{"$": clave, "es": valor}` en cualquier nodo | Se ve sólo si el estado vale `valor` (comparado en NFC). El motor exige que `valor` sea una `option` del Select de esa clave. |
| `rojo`, `value` | en `Text` | `value` escribe un slot tipado con el formateador común (tras `text`, si lo hay); `rojo` lo pinta en rojo si vale `true`. |
| `required`, `kind`, `min`, `max`, `step` | en `Input` (`required` también en `Select`) | Semántica de PACAI: `1.5` con `step=1` falla en todos los clientes. Un Select sin `required` empieza en su primera opción; con él, vacío. |
| `Form` | — | Bloquea su propio `submit` mientras un campo visible de su subárbol no vale. Otros botones con `send` no se bloquean. |
| `Data` | `name`, `query: {"tipo": …}`, hijos | Pide una consulta que declara la app en `queries` (`{"tipo": {"args": …, "out": {slot: tipo}}}`), espera a tener sus argumentos y vale la última respuesta. Sus salidas sólo se leen en su subárbol (`{"$": "q.hoy"}`). |

El formateador escribe sin locale: dinero con 2 decimales, fracción como porcentaje con 1 decimal, miles con punto y
decimales con coma, nulo «—». Los números van en coma fija (×10⁶, half-even). No hay AST, ni evaluador de
expresiones, ni perfil `pacai.ui-logica/1`. Web y Python pasan los mismos vectores
(`tests/conformidad/pacai-ui-piezas.json`; en la web, `node docs/demos/pacai-ui/conformidad.mjs`). Swift no los
implementa todavía.
