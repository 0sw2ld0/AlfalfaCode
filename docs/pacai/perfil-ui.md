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
14. **Las pantallas se guardan con la sesión.** El transporte guarda cada vista (revisión, nodos, estado inicial y
    resumen) en `pacai-vistas.json`, en la carpeta de la sesión, cada vez que cambia una revisión. Con
    `--pacai --resume` las recupera: la vista sigue por la revisión siguiente y no vuelve a la 1, y una acción
    sobre una pantalla de antes de reanudar llega al modelo como cualquier otra. Al recuperarlas se descartan los
    nodos que el catálogo del cliente nuevo no admite.
15. **Una vista nueva sin `root` se repara.** Si el primer bloque de una vista no trae nodo `root` (el modelo empieza
    por `{"id":"formulario","component":"Form",…}` y no escribe la raíz), el motor añade un `root` `Stack` con
    `direction: vertical` cuyos hijos son los nodos que no cuelgan de ningún otro, en el orden en que llegaron, y lo
    anota en `Vistas.reparaciones`; no cuenta como error. Ningún id cambia, así que los cambios siguientes valen igual,
    y un nodo existente nunca se convierte en `root`. Cuenta como vista nueva también una que aún no tiene nodos (un
    primer bloque sin ninguno válido). No se repara un bloque sobre una vista con nodos ni uno cuyo `root` se rechazó,
    aunque no se pueda leer (una línea con `"id":"root"` que no es JSON): ahí el modelo quiso otra raíz y se le pide.
    Los hijos de un nodo rechazado (los campos de un `Form` que no valió) no suben a la raíz nueva. Si la pantalla ya
    tiene el máximo de nodos, no cabe el `root` y el error lo dice.
16. **Prototipo: el manual bajo demanda (`ALFALFA_PACAI_UI_MANUAL=1`, apagado por defecto).** El manual de este
    apartado va entero en el prompt de sistema de cada petición, aunque no se dibuje nada: unos 1.620 tokens con
    buscador. Con el flag, el prompt lleva sólo `AVISO_MANUAL` (cuándo conviene una pantalla y que primero cargue el
    manual) y, si hay buscador, `BUSCAR_ANTES`. El modelo pide el manual con la herramienta `ui_manual`, sin
    argumentos, que sólo existe con el perfil `ui` negociado.
    - Devuelve el manual de la sesión: catálogo del cliente y formato (JSON o PACAI UI Lang). Si se repite y el manual
      sigue en el historial, contesta un aviso corto; tras compactar ya no está y lo devuelve entero.
    - `BUSCAR_ANTES` se queda en el prompt porque, en la prueba corta, GPT-6 Luna dijo «no puedo consultar noticias
      en tiempo real» antes de cargar el manual: decide si busca antes de saber que va a dibujar.
    - Con el prompt real del motor (capturado de `TerminalManager`, catálogo base), el sistema pasa de 6.589 a 5.123 (−1.466)
      tokens por petición; la herramienta añade 80 y el manual que devuelve pesa 1.580.

    **Medición (26-11-2026, `scripts/piloto_manual_ui.py`).** GPT-6 Luna por el plan de ChatGPT, esfuerzo medium, el
    prompt y las herramientas de verdad, 20 preguntas × 2 brazos × 2 repeticiones = 80 turnos y 140 peticiones. 12
    preguntas donde conviene una pantalla sin pedirla (3 con búsqueda, con resultados fijos) y 8 de código. Claude
    Haiku 5.5 no se midió: no está en el plan de ChatGPT. Datos brutos fuera del repositorio
    (`piloto-manual-ui-luna.jsonl`).

    | GPT-6 Luna | Manual completo | `ui_manual` |
    |---|---:|---:|
    | Dibuja sin que se pida, donde conviene | 11/24 (46 %, IC95 % 28–65 %) | 8/24 (33 %, 18–53 %) |
    | … sin las 3 preguntas de búsqueda | 10/18 (56 %) | 6/18 (33 %) |
    | Pantallas válidas a la primera | 11/11 | 8/8 |
    | Pantallas fuera de lugar en preguntas de código | 0/16 | 0/16 |
    | Carga el manual (donde conviene · código) | — | 11/24 · 0/16 |
    | Entrada media por turno de código (sin pantalla) | 13.805 tokens | 12.385 (−10 %) |
    | Entrada media por turno con pantalla | 22.690 tokens | 37.925 (+67 %) |
    | Coste medio por turno, todas las preguntas | 2,48 $/1000 | 2,55 $/1000 (+3 %) |
    | Tiempo hasta la primera pantalla (mediana) | 13,6 s | 14,8 s |

    - La línea corta **dibuja menos**: el formulario de vacaciones (2/2 → 0/2) y las opciones de alojamiento (2/2 →
      0/2) pasan a texto o a una tabla en markdown (3 → 5 turnos con tabla markdown). Con n = 24 las diferencias no
      son significativas, pero ninguna pregunta mejora salvo la de elegir curso (1/2 → 2/2) y la del precio de la papa.
    - Tres turnos cargaron el manual y no dibujaron: dos desconfiaron de los resultados fijos (dominios de ejemplo,
      igual que en el brazo completo) y uno agotó los 6 pasos buscando. Es un límite del montaje, igual en los dos
      brazos.
    - Ahorra en los turnos sin pantalla y cuesta una petición más en los que sí la llevan, que además arrastran el
      manual en el historial. En una sesión de agente con muchas herramientas por turno, el ahorro de ~1.450 tokens
      se cobra en cada petición, así que pesaría más que en este piloto de turnos cortos.

    **Con PACAI UI Lang 3 (`--ui-lang`, los dos brazos con `ALFALFA_PACAI_UI_LANG=1`).** Mismas preguntas, modelo y
    plan: 80 turnos, 127 peticiones. El manual compacto es más corto (el sistema pasa de 6.031 a 5.123 tokens, −908) y
    el que devuelve `ui_manual` pesa 1.022. Datos brutos: `piloto-manual-ui-luna-lang.jsonl`.

    | GPT-6 Luna | JSON completo | UI Lang 3 completo | UI Lang 3 con `ui_manual` |
    |---|---:|---:|---:|
    | Dibuja sin que se pida, donde conviene | 11/24 (46 %) | 11/24 (46 %) | 6/24 (25 %, IC95 % 12–45 %) |
    | … sin las 3 preguntas de búsqueda | 10/18 | 10/18 | 5/18 |
    | Pantallas válidas a la primera | 11/11 | 10/11 | 6/6 |
    | Pantallas fuera de lugar en preguntas de código | 0/16 | 0/16 | 0/16 |
    | Entrada por turno de código (mediana) | 13.805 | 13.252 | 12.397 |
    | Coste medio por turno, todas las preguntas | 2,48 $/1000 | 2,07 $/1000 | 2,34 $/1000 |
    | Tiempo hasta la primera pantalla (mediana) | 13,6 s | 8,4 s | 11,1 s |

    - Con UI Lang, `ui_manual` pierde todavía más pantallas (46 % → 25 %): organizar la semana pasa de 2/2 a 0/2 y la
      mudanza y el alojamiento de 1/2 a 0/2. Cuesta un 13 % más de media por turno y tarda 2,7 s más en la primera
      pantalla, porque ahorra 908 tokens por petición y paga una petición entera más cuando dibuja.
    - UI Lang 3 solo, con su manual siempre en el prompt, dibuja lo mismo que el JSON (46 %), cuesta un 17 % menos por
      turno y saca la primera pantalla 5 s antes. La pantalla no válida fue un formulario sin `root`.

    **Recomendación: no encender `ui_manual` por defecto, ni con JSON ni con UI Lang.** Ahorra un 6–10 % de entrada
    cuando no se dibuja, pero baja las pantallas que el modelo decide dibujar solo (46 % → 33 % con JSON, 46 % → 25 %
    con UI Lang), que es justo lo que el perfil busca. Para gastar menos prompt sin perder pantallas, encender UI Lang
    3 da más: ahorra más que diferir el manual y no pierde ninguna. Si se quiere seguir con la carga bajo demanda, lo
    siguiente sería dejar en el prompt la parte de *cuándo* dibujar (unos 250 tokens) y diferir sólo el formato, el
    catálogo y los ejemplos, y medirlo también con Haiku.
17. **Vista enriquecida.** El cliente puede apagar las pantallas sin renegociar la sesión con
    `alfalfa.set_rich_view` (`active`). Apagada, el modelo no recibe el manual de pantallas y contesta en texto; las
    que ya dibujó se siguen viendo y sus acciones siguen valiendo. Con `ui` negociado, `alfalfa.session` lleva
    `rich_view` con el estado actual, así el cliente sabe que el motor admite el interruptor. La app de Mac lo enseña
    en el panel derecho (Entorno), con su valor por defecto en Ajustes ▸ General, y la extensión de VS Code en el
    ajuste `alfalfa.vistaEnriquecida`. Desde 0.99.57 viene apagada en lo nuevo: en la app, una sesión nueva fija la
    suya con la de Ajustes al recibir su id (`Preferences.adoptRichView`); en VS Code, una instalación nueva. Lo de
    antes no pierde las pantallas al actualizar: una sesión de la app sin valor propio sigue la de Ajustes si se tocó
    y, si no, encendida, y en VS Code una instalación que ya se usaba sigue encendida mientras no se toque el ajuste
    (`lib/vista.js`). El motor la deja encendida para el cliente que no dice nada.

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

## 10. PACAI UI Lang (con todos los modelos) y piezas declarativas (detrás de un flag)

> **En pocas palabras.** PACAI es el idioma fijo con el que el motor y las apps (app de Mac, VS Code, la web)
> se entienden, y las pantallas viajan siempre en su formato oficial, `pacai.ui/1`. PACAI UI Lang 3 es una
> forma abreviada de escribir esas mismas pantallas que usa sólo el modelo, porque escribir menos tokens
> cuesta menos. El motor la traduce a `pacai.ui/1`, la valida y le pone la revisión antes de mandarla, así
> que las apps nunca la ven y no cambian.
>
> La misma tarjeta, en los dos formatos que puede escribir el modelo (49 y 86 tokens, `o200k_base`):
>
> ```text
> root Card "Palta Hass" meta="hoy · fruta" > nota,ver
> nota Text "Sube un 3 % esta semana."
> ver Button "Ver historial" variant=primary @press={"send":"ver_historial"}
> ```
>
> ```text
> {"id":"root","component":"Card","props":{"title":"Palta Hass","meta":"hoy · fruta"},"children":["nota","ver"]}
> {"id":"nota","component":"Text","props":{"text":"Sube un 3 % esta semana."}}
> {"id":"ver","component":"Button","props":{"label":"Ver historial","variant":"primary"},"events":{"press":{"send":"ver_historial"}}}
> ```
>
> En los dos casos, el `message_end` lleva al cliente los mismos tres nodos `pacai.ui/1`. Una analogía: el mozo
> apunta «2 lomo s/ceb» en su libreta, y a la cocina le llega la comanda impresa con todo escrito. Deja de valer
> en un punto: el motor no adivina. Una línea que no cumple el catálogo se rechaza y se le pide al modelo que la
> corrija; sólo se hacen unos pocos arreglos que no cambian lo pedido (§10, «Reparaciones seguras»).
> Explicación larga, con diagrama: https://docs.pacai.dev/ui-lang.html.

Sale de la 3.ª y la 4.ª lluvia de ideas. **PACAI UI Lang va encendido con todos los modelos.** `ui_lang.MODELOS_MEDIDOS`
sólo dice con cuáles se midió y cumple el criterio de adopción (hoy GPT-6 Luna y Claude Haiku 5.5; §10.1); con el
resto el formato se usa igual, sin esa medida. `ALFALFA_PACAI_UI_LANG=0` lo apaga con todos y vuelve el JSON con
ejemplos de arriba, y así se revierte; `ALFALFA_PACAI_UI_LANG=1` enciende además las piezas declarativas.
La app de Mac no lo conoce y no hace falta: el motor sigue mandando `pacai.ui/1`.

- **Dónde se decide:** `Catalogo.de(declaracion, entorno, modelo)` y, antes de cada inferencia,
  `Catalogo.con_modelo(modelo)` desde `manual_ui(modelo=…)`, con el modelo sin el prefijo del proveedor (vale
  `chatgpt/gpt-6-luna`, `alfalfa/claude-haiku-5-5`, `openrouter/anthropic/claude-haiku-5.5` o una instantánea con
  fecha). El motor lee los bloques de la respuesta con el formato del último prompt que vio el modelo.
- **Si el usuario cambia de modelo a mitad de sesión,** el prompt siguiente cambia de formato. Las pantallas ya
  dibujadas no cambian (el motor guarda nodos `pacai.ui/1`), pero el historial conserva los bloques que escribió el
  modelo anterior. No se ha medido cómo le sienta a un modelo ver en el historial el otro formato.

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
  hijo que la línea del padre deja fuera también se va. **La última línea de un id gana** (§10.3): borrar y reescribir un nodo lo reescribe, escribirlo y borrarlo lo borra, y dos líneas
  distintas del mismo id se quedan con la segunda; borrar un id que no está no hace nada, y `- id` vale como `-id`.
  Una línea con sólo un id (una baja sin el guion) se ignora sólo si da la misma pantalla ignorarla que borrar ese
  nodo; si no, se rechaza. Todo queda en `Vistas.reparaciones`. Todo el bloque se aplica o nada.
  El modelo ve la pantalla con sus líneas enteras (`Vistas.resumen_lang`). La versión 1 (`~id`, `+>`) y el `rev`
  escrito por el modelo (versión 2) se quitaron: eran donde más fallaba.
- **Reparaciones seguras:** una línea idéntica a una que ya vale se ignora; en un alta, como en un cambio, la última
  línea de un id gana (también en el reintento, donde el modelo reenvía el bloque); y un `rev` del modelo se ignora.
  Nunca cambian lo que se pidió.
  - **Sin valla:** si la respuesta entera son líneas del formato (nodos del catálogo o bajas, sin ninguna otra valla
    ni frase), se lee como un bloque (`Vistas.sin_valla`). Es un cambio de la última vista si alguna línea nombra o
    cuelga uno de sus nodos (aparte de root); si no, y trae root sin bajas, es un alta en una vista nueva `pantalla`
    (`pantalla_2`…). Si no, es texto.

  Además, sólo en un alta (y anotadas en `Vistas.reparaciones`, sin contar como error):
  - **Sin `root`, con la raíz con otro nombre:** si la primera línea valió, es un contenedor (Stack, Card, Form o un
    componente propio con hijos) y no cuelga de nadie, es la raíz con otro nombre (`raiz`, `fila`: lo hace Haiku,
    §10.1) y pasa a llamarse `root`, sin un nodo de más. El modelo ve después la pantalla con `root`.
  - **Sin `root`, en cualquier otro caso** (como en OpenUI Lang, donde la primera sentencia es la raíz): un `root`
    `Stack direction=vertical` nuevo envuelve los nodos que no son hijos de ningún otro, en el orden en que llegaron.
    Los ids no cambian, así que un cambio posterior a `formulario` funciona. Para las dos reglas: si el modelo
    escribió un `root` y se rechazó (aunque la línea no se pueda leer), o no hay nodos de primer nivel, sigue siendo
    «Falta root.»; si no cabe por el máximo de nodos, el diagnóstico lo dice. Los hijos de una línea rechazada no
    suben al `root` nuevo: quedan sin colgar, como con un `root` explícito.
  - **Posicional y nombrada a la vez** (`f Form "Mandar" submit_label="Enviar"`): se queda la nombrada.

  Un cambio sobre una vista que ya existe no se repara. Sobre las respuestas guardadas de las muestras
  (`scripts/muestra_ejemplos_ui.py`, sin pedir nada nuevo) pasan de no válidas a válidas: GPT-6 Luna 0 de 208 (valla)
  y 0 de 104 (grande); Claude Haiku 5.5 1 de 104 (grande) y 8 de 208 (valla). Ninguna válida deja de serlo y el
  JSON no cambia (sus fallos de raíz eran `root` rechazados). Con las dos reglas juntas las cifras son las mismas:
  en las 11 altas de Haiku sin `root` de §10.1 la primera línea era un contenedor suelto y se renombró.
- **Streaming:** `Flujo` valida cada línea completa al llegar y publica un nodo en cuanto él y sus ancestros
  existen, con el prefijo contiguo de hijos llegados: cada publicación es un árbol válido que sólo crece. La línea
  que todavía llega se lee cerrando su texto y sus corchetes (`cerrar_cola`) y, si ya vale y su id no existe, se publica de forma provisional: cuando llega entera la sustituye, a medias
  de una palabra se queda la última lectura buena, y si la línea entera no vale desaparece. Una línea
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

### 10.1 Encendido por modelo: la medida (26 de noviembre de 2026)

**Criterio de adopción** (el de la medición de UI Lang 3): ≥95 % de pantallas válidas a la primera, límite inferior
de Wilson (IC 95 %) ≥90 % y al menos un 20 % menos de coste por pantalla exacta que el JSON con ejemplos, en la misma
tanda.

**Condiciones,** las de la última tanda de Luna: `scripts/muestra_ejemplos_ui.py`, las mismas 52 tareas (32 altas y
20 cambios sobre el tablero de CuyMercado), esfuerzo bajo, temperatura 0, sin herramientas, una corrección, 4
repeticiones, brazos «PACAI JSON con ejemplos» (`json_ej`) y UI Lang 3 (`lang`) en la misma tanda, los dos con
`required` en Select y la valla a media línea. Por el servicio Alfalfa (suscripción CUY; Haiku no está en el plan de
ChatGPT). 416 peticiones por tanda, 0 errores del servicio.

| Modelo | Brazo | n | Válidas 1.er intento | IC95 % Wilson | ¿Umbral? | Exactas 1.º | Válidas tras corr. | Entrada (med.) | Salida (med.) | Coste por exacta (1.º) |
|---|---|---:|---|---|---|---|---|---:|---:|---:|
| Luna (tanda anterior) | PACAI JSON con ejemplos | 208 | 205 (98,6 %) | 95,8–99,5 % | sí | 199 (95,7 %) | 208 | 2088 | 105 | 0,30 $/1000 |
| Luna (tanda anterior) | **PACAI UI Lang 3** | 208 | **205 (98,6 %)** | **95,8–99,5 %** | **sí** | 197 (94,7 %) | 208 | 1300 | 84 | **0,20 $/1000 (−33 %)** |
| Haiku 5.5, tanda 1 | PACAI JSON con ejemplos | 208 | 203 (97,6 %) | 94,5–99,0 % | sí | 188 (90,4 %) | 208 | 3391 | 159 | 0,50 $/1000 |
| Haiku 5.5, tanda 1 | PACAI UI Lang 3 | 208 | 191 (91,8 %) | 87,3–94,8 % | **no** | 177 (85,1 %) | 208 | 2110 | 117 | 0,34 $/1000 (−32 %) |
| Haiku 5.5, tanda 1 repuntuada con la reparación de `root` | PACAI UI Lang 3 | 208 | 199 (95,7 %) | 92,0–97,7 % | sí | 184 (88,5 %) | — | 2110 | 117 | 0,33 $/1000 (−34 %) |
| Haiku 5.5, tanda 2 (con la reparación) | PACAI JSON con ejemplos | 208 | 205 (98,6 %) | 95,8–99,5 % | sí | 189 (90,9 %) | 208 | 3391 | 161 | 0,50 $/1000 |
| Haiku 5.5, tanda 2 (con la reparación) | **PACAI UI Lang 3** | 208 | **198 (95,2 %)** | **91,4–97,4 %** | **sí** | 183 (88,0 %) | 208 | 2110 | 118 | **0,33 $/1000 (−34 %)** |

**Qué pasó con Haiku**

- **Tanda 1 (0,16 US$), sin cambios en el motor: no cumple.** Haiku había cumplido en la medida anterior (97,1 %,
  n = 104), pero con más muestra baja al 91,8 %. 8 de sus 17 fallos son altas en las que la raíz se llama `raiz` o
  `fila` en vez de `root`, aunque el prompt dice «La raíz se llama root y va primero». Luna no lo hace. Los otros:
  borrar y reescribir el mismo nodo en un cambio (`c_bajaron … / -c_bajaron`, 4, siempre en `p_iguales`), `root` dos
  veces con contenido distinto (4) y un nodo suelto (1).
- **El arreglo:** la reparación de un alta sin `root` (§10, «Reparaciones seguras»), combinada con la del PR #220. Si
  la primera línea es un contenedor que valió y no cuelga de nadie, pasa a llamarse `root`; si no, un Stack vertical
  nuevo envuelve los nodos de primer nivel. No cambia el árbol que pidió el modelo. La tanda 2 se midió con la primera
  versión (renombrar la primera línea si no cuelga de nadie, fuera o no un contenedor). Todas las respuestas guardadas
  se han vuelto a validar, sin gastar, con la regla combinada, y salen las mismas cifras: en la tanda 2 la reparación
  entra 3 veces, siempre renombrando, y la tanda 1 repuntuada queda en 199/208 (95,7 %).
- **Tanda 2 (0,16 US$), con el arreglo: cumple.** 95,2 % de válidas a la primera, con el límite inferior de Wilson en
  91,4 %, y un 34 % menos de coste por pantalla exacta. La reparación entró 3 veces (`fila` 2, `raiz` 1). Como
  referencia, las dos tandas de Haiku con el arreglo (la 1 repuntuada) suman 397/416 (95,4 %, Wilson ≥ 93,0 %). Luna y
  los brazos JSON no cambian al repuntuar.
- **Lo que queda en Haiku** (10 fallos): borrar y reescribir el mismo nodo (4, `p_iguales`), los de
  `m_sin_filtros` (root dos veces, un panel nuevo que no cuelga de nadie, borrar y reescribir: 4), un nodo suelto (1)
  y los hijos escritos en medio de la línea (1). Todos se arreglan con la corrección (208/208).
- **Ojo: Haiku cumple justo** (198 de 208; con 197 serían 94,7 %). En validez el JSON con ejemplos sigue por encima
  (98,6 %) y en exactas también (90,9 % frente a 88,0 %), dentro del ruido en las exactas. La ventaja de UI Lang 3 es
  de coste.
- **Luna no se ha vuelto a medir con la reparación,** pero sus respuestas guardadas (§14 de la medición) dan lo mismo
  con la regla combinada: 205/208 en los dos brazos.

**Complemento, otra medida (PR #218, §8.16):** con Luna por el plan de ChatGPT y el prompt real del motor (20
preguntas × 2), UI Lang 3 dibuja sin que se lo pidan tanto como el JSON (11/24), con 10/11 pantallas válidas a la
primera, un 17 % menos de coste medio por turno y la primera pantalla en 8,4 s frente a 13,6 s, sin pantallas fuera de
lugar en las 16 preguntas de código.

**Decisión:** UI Lang 3 encendido por defecto con GPT-6 Luna y Claude Haiku 5.5; el JSON con ejemplos con el resto
de modelos. `ALFALFA_PACAI_UI_LANG=1` o `0` lo fuerza en un sentido u otro.

**Decisión posterior (dueño, 0.99.57):** UI Lang 3 con todos los modelos, no sólo con los medidos: es el formato de
las pantallas y el JSON queda como reserva con `ALFALFA_PACAI_UI_LANG=0`. A la vez, las apps (Mac y VS Code) traen
la «Vista enriquecida» apagada por defecto en lo nuevo (sesiones nuevas de la app, instalaciones nuevas de VS Code),
así que nadie empieza a recibir pantallas sin encenderla; lo que ya tenía pantallas las conserva (§8). El
motor sigue con la vista encendida para el cliente que negocia `ui` sin decir nada (la web de CuyMercado, por
ejemplo). Con los modelos que no se han medido, la validez puede ser algo menor (GLM 5.3 Flash: 94,2 % en §10.3).

**Lo que no se ha probado**

- Ningún otro modelo con este rigor (Sonnet, Opus, GPT-6 sin Luna, DeepSeek, Gemma…): usan UI Lang 3 sin esa
  garantía; la clasificación de `docs/pacai/ui-lang.md` §6 da lo que sí se midió.
- Una sesión de verdad en la app de Mac con Luna o Haiku: la medida es por tarea, sin herramientas ni historial.
- Cambiar de modelo a mitad de sesión con pantallas en el historial.
- El streaming por trozos sigue sin servirse (el servicio no trocea).
- Datos: `muestra-haiku-lang3-valla.jsonl` y `muestra-haiku-lang3-raiz.jsonl`, en las notas de la sesión.

### 10.2 PACAI UI Lang 3 frente a OpenUI Lang 1.0 y el JSON (26 de noviembre de 2026)

La página para quien no conoce el formato, con el benchmark ampliado (pantallas que se dibujan en un navegador,
validez por complejidad y clasificación de modelos), es [`ui-lang.md`](ui-lang.md).

**Qué se compara.** Los tres formatos describen las mismas pantallas, con el mismo catálogo (los 12 componentes base y
los 6 de CuyMercado), un ejemplo por componente traducido a cada sintaxis y el mismo validador (los nodos se convierten
a `pacai.ui/1` y pasan el catálogo y el árbol).

- **PACAI JSON con ejemplos** (`json_ej`): el camino por defecto de los modelos sin UI Lang.
- **PACAI UI Lang 3** (`lang`): lo que ven hoy GPT-6 Luna y Claude Haiku 5.5.
- **OpenUI Lang 1.0** (`openui_ej`): según la especificación oficial (`thesysdev/openui`, `spec/language.md` y
  `spec/prompt.md`, 1.0). Tiene argumentos posicionales y `$clave` para el estado. Las acciones son `@OpenUrl`, `@Set`
  y una acción propia de la librería, `@Send(nombre, with?)`, para los eventos que ejecuta el anfitrión. Los cambios
  van en modo edición: se fusiona por nombre, `nombre = null` borra y se recoge lo que `root` ya no alcanza. Si no
  hay `root`, la primera sentencia con `Stack` hace de raíz. Las comas son opcionales y se admiten comentarios.
  Se acepta la respuesta sin valla, y la cola de un stream cortado se cierra sola.
  - El lector es nuestro (`scripts/openui_lang.py`, sin el SDK) y lo prueba `tests/test_openui_lang_piloto.py`.
  - El prompt sigue el orden del que genera el SDK: sintaxis, firmas con tipos, acciones, streaming, modo edición
    y verificación final.
  - Como en el benchmark de OpenUI, cuentan como fallo todo error del lector, las referencias sin definir y los
    huérfanos.
  - **El piloto anterior (§8 de la medición) usaba una versión anterior a la 1.0.** Los eventos iban como un objeto,
    no había acciones `@` y los hijos iban detrás de las props, sin borrado ni recolección. Se ha puesto al día.

**Qué publica OpenUI.** Su benchmark (`thesysdev/generative-ui-bench`) usa 46 briefs en 5 bandas de complejidad, 6
modelos, temperatura 0,7, su catálogo de 70 componentes y 4 generaciones por brief. Compara OpenUI Lang con A2UI y
json-render en validez estructural, pantallas que se dibujan, tokens y coste. La latencia no la mide: la estima a 50
tokens/s. Su titular es «hasta 3 veces más rápido y un 67 % menos de tokens que JSON». Ese 67 % es un máximo: con
sus propias cifras sale un 52–58 % menos de salida.

**Condiciones.**

- **Script:** `scripts/comparar_formatos_ui.py`, por el servicio Alfalfa, con esfuerzo bajo, temperatura 0, sin
  herramientas, en streaming y con una corrección.
- **Tanda A:** las 52 tareas de §10.1, con Luna y Haiku y 4 repeticiones. Son 1248 peticiones.
- **Tanda B:** los 46 briefs de OpenUI, sin cambiar el texto, con Luna y Haiku y 2 repeticiones. Son 552 peticiones;
  el filtro de OpenAI rechazó 2 de Luna. Válida es sin errores y con tantos componentes alcanzables como requisitos
  (su «suelo de cobertura»).
- **Tanda C:** GLM 5.3 Flash, DeepSeek v4.1 Flash y GPT-6.2 Sol, con las 52 tareas y 1 repetición. Son 468
  peticiones. Son los modelos de Alfalfa más cercanos a los de OpenUI; los suyos exactos no están en Alfalfa.
- **Coste:** 1,83 US$ en total.
- **Datos:** `docs/pacai/datos/formatos-ui-*.jsonl`, con todas las respuestas. El informe sale de
  `python scripts/comparar_formatos_ui.py informe --revalidar docs/pacai/datos/*.jsonl`.

**Las 52 tareas** (válidas y exactas al primer intento; coste del primer intento por pantalla exacta):

| Modelo | Formato | n | Válidas 1.er intento | IC95 % Wilson | Exactas | Entrada (med.) | Salida (med.) | Total (med.) | Coste por exacta |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| GPT-6 Luna | PACAI UI Lang 3 | 208 | 203 (97,6 %) | 94,5–99,0 % | 197 | 1300 | 74 | 1,34 s | **0,20 $/1000** |
| GPT-6 Luna | OpenUI Lang 1.0 | 207 | **207 (100 %)** | 98,2–100 % | 195 | 1894 | 67 | 1,42 s | 0,25 $/1000 |
| GPT-6 Luna | PACAI JSON con ejemplos | 208 | 207 (99,5 %) | 97,3–99,9 % | 198 | 2088 | 104 | 1,62 s | 0,30 $/1000 |
| Claude Haiku 5.5 | PACAI UI Lang 3 | 208 | 201 (96,6 %) | 93,2–98,4 % | 186 | 2110 | 118 | 1,14 s | **0,33 $/1000** |
| Claude Haiku 5.5 | OpenUI Lang 1.0 | 208 | **208 (100 %)** | 98,2–100 % | 182 | 3014 | 113 | 1,15 s | 0,43 $/1000 |
| Claude Haiku 5.5 | PACAI JSON con ejemplos | 208 | 200 (96,2 %) | 92,6–98,0 % | 182 | 3390 | 158 | 1,28 s | 0,52 $/1000 |
| GLM 5.3 Flash | PACAI UI Lang 3 | 52 | 45 (86,5 %) | 74,7–93,3 % | 43 | 1334 | 72 | 1,37 s | **0,30 $/1000** |
| GLM 5.3 Flash | OpenUI Lang 1.0 | 52 | **52 (100 %)** | 93,1–100 % | 49 | 1956 | 74 | 1,38 s | 0,37 $/1000 |
| GLM 5.3 Flash | PACAI JSON con ejemplos | 52 | 48 (92,3 %) | 81,8–97,0 % | 45 | 2116 | 119 | 1,53 s | 0,46 $/1000 |
| DeepSeek v4.1 Flash | PACAI UI Lang 3 | 52 | 52 (100 %) | 93,1–100 % | 50 | 1439 | 84 | 0,98 s | **0,59 $/1000** |
| DeepSeek v4.1 Flash | OpenUI Lang 1.0 | 52 | 52 (100 %) | 93,1–100 % | 48 | 2060 | 73 | 0,98 s | 0,81 $/1000 |
| DeepSeek v4.1 Flash | PACAI JSON con ejemplos | 52 | 52 (100 %) | 93,1–100 % | 49 | 2287 | 116 | 0,97 s | 0,94 $/1000 |
| GPT-6.2 Sol | PACAI UI Lang 3 | 52 | 52 (100 %) | 93,1–100 % | 49 | 1300 | 65 | 1,96 s | **3,63 $/1000** |
| GPT-6.2 Sol | OpenUI Lang 1.0 | 52 | 52 (100 %) | 93,1–100 % | 49 | 1894 | 66 | 2,05 s | 5,02 $/1000 |
| GPT-6.2 Sol | PACAI JSON con ejemplos | 52 | 52 (100 %) | 93,1–100 % | 50 | 2088 | 109 | 2,46 s | 5,89 $/1000 |

Con Luna y Haiku juntos:

- **Válidas a la primera:** UI Lang 3 404/416 (97,1 %), OpenUI 415/415 (100 %) y JSON 407/416 (97,8 %).
- **Exactas:** 383, 377 y 380.
- **Coste por pantalla exacta:** 0,26, 0,34 y 0,40 $/1000. OpenUI cuesta un 31 % más que UI Lang 3 y el JSON un 56 %
  más.

Por tipo, Luna y Haiku juntos:

- **Altas:** los tres formatos salen válidas en las 256.
  - Exactas: UI Lang 3 237, OpenUI 228 y JSON 238.
  - Salida mediana: 83–139 tokens en UI Lang 3, 85–143 en OpenUI y 130–214 en JSON.
- **Cambios:**
  - Válidos: UI Lang 3 148/160, **OpenUI 159/159** y JSON 151/160.
  - Exactos: 146, 149 y 142.
  - Salida mediana: 70–100 tokens en UI Lang 3, **49–81** en OpenUI y 83–103 en JSON.

**Los 46 briefs de OpenUI** (válidas al primer intento; sin «exacta»):

| Modelo | PACAI UI Lang 3 | OpenUI Lang 1.0 | PACAI JSON con ejemplos |
|---|---|---|---|
| GPT-6 Luna | 73/92 (79,3 %), 0,56 $/1000 por válida | **85/90 (94,4 %)**, 0,63 $/1000 | 71/92 (77,2 %), 1,03 $/1000 |
| Claude Haiku 5.5 | 71/92 (77,2 %), **0,93 $/1000** | 59/92 (64,1 %), 1,35 $/1000 | **75/92 (81,5 %)**, 1,39 $/1000 |

- **Por bandas:** los tres bajan con la complejidad. Con 2–6 requisitos van del 88 al 98 %; con 11–20, del 53 al
  72 %.
- **Haiku con OpenUI** pierde sobre todo por nodos que no cuelgan de `root` (18) y por poner `@Send` en un `change`,
  donde sólo vale `@Set` (12).

**Tokens de la misma pantalla** (`o200k_base`, escritas a mano en los tres formatos):

| Pantalla | JSON | OpenUI | UI Lang 3 |
|---|---:|---:|---:|
| Tablero de CuyMercado | 308 | 193 | 146 |
| Formulario de contacto | 120 | 73 | 66 |
| Tabla de tickets | 108 | 106 | 96 |
| Tres tarjetas con enlace | 222 | 162 | 134 |
| Cambio: «Iguales» en lugar de «Bajaron» | 80 | 52 | 40 |

Prompt de sistema: JSON 2037, OpenUI 1843 y UI Lang 3 1249 tokens.

**Errores** (clase del primer error de cada pantalla no válida, en las 52 tareas):

- **OpenUI:** ninguno con ningún modelo.
- **UI Lang 3:**
  - con Luna y Haiku: borrar y reescribir el mismo nodo en un cambio (5), hijos colgantes o nodos sueltos (5),
    sintaxis (1) y sin valla (1);
  - con GLM: sin valla (4).
- **JSON:** sin valla (4 con Haiku y 2 con GLM), tipos (3), hijos colgantes (3) y una prop obligatoria (1).

En los briefs, todos los formatos fallan sobre todo en referencias (huérfanos, hijos que faltan) y en tipos o enums.

**Latencia y streaming.**

- **El servicio entrega cada respuesta en un solo trozo,** así que la primera pantalla medida es el total. La mediana
  va de 1,1 a 1,6 s en las tareas; UI Lang 3 es la más rápida con Luna (1,34 s), por tener menos salida.
- **Estimada como OpenUI** (tokens hasta ver `root` con un hijo, a 50 tokens/s):
  - con Luna: OpenUI 0,49 s, UI Lang 3 0,85 s y JSON 1,50 s;
  - con Haiku: 0,94, 1,53 y 2,19 s.
- **Con la respuesta cortada** (altas válidas, regla de streaming de cada formato), la mediana de la pantalla que ya se
  ve es:
  - OpenUI: 33–38 % al cortar en el 25 % del texto, 60–63 % al 50 % y 80 % al 75 %;
  - UI Lang 3 y JSON: 0–20 %, 33–40 % y 67 %.
- **Por qué:** OpenUI cierra la sentencia a medias (§4 de su especificación), mientras que PACAI sólo publica líneas
  completas, y en un alta la primera línea suele ser la más larga.

**Conclusiones.**

- **OpenUI Lang 1.0 gana en validez a la primera** en nuestras tareas, con los cinco modelos. La ventaja está en los
  cambios y en la robustez:
  - su modo edición (la última definición gana y se recoge lo inalcanzable) no tiene la trampa de UI Lang 3, que
    rechaza borrar y reescribir el mismo nodo en un bloque;
  - acepta la respuesta sin valla;
  - aguanta mejor el corte del stream.
- **PACAI UI Lang 3 gana en coste:** es el más barato por pantalla exacta con los cinco modelos y en los briefs. Cuesta
  un 20–28 % menos que OpenUI y un 33–38 % menos que el JSON, según el modelo, porque su prompt es más corto (hereda `period` y `family` y
  no lleva tipos) y su salida en las altas también.
  - También hace lo pedido algo más a menudo en las altas: 237 exactas frente a 228.
  - La diferencia en validez, 97,1 % frente a 100 %, es de 12 pantallas en 416.
- **El JSON con ejemplos** es el más estable con Haiku en los briefs largos y no necesita lector nuevo. Es el más caro
  en todo.
- **Con los briefs de OpenUI no hay un ganador claro:** depende del modelo. OpenUI gana con Luna (94 %) y pierde con
  Haiku (64 %). Las cifras de OpenUI (96,9 % de validez) no son comparables con estas: catálogo, modelos y
  temperatura distintos.
- **OpenUI no cumple nuestro criterio de adopción frente al JSON.** Cumple en validez, pero su ahorro por pantalla
  exacta es de un 15–17 % con Luna y Haiku, por debajo del 20 % pedido. UI Lang 3 sigue cumpliéndolo.
- **Qué mejorar en UI Lang 3 sin cambiar de formato,** y probarlo con estas mismas tareas:
  - en los cambios, que la última línea de un id gane (borrar y reescribir el mismo nodo ya no sería un error);
  - aceptar un bloque `pacai-ui-lang` sin valla cuando la respuesta entera son líneas del formato;
  - en el streaming, publicar la línea pendiente cerrándola.

**Lo que no se ha probado.**

- Los modelos exactos de OpenUI (GPT-5.6 Sol, Opus 4.8, Kimi K3, Gemini 3.7 Flash…), que no están en Alfalfa.
- Su SDK y su renderizador reales: el lector es nuestro, aunque sigue su especificación y sus códigos de error.
- Su temperatura de 0,7: aquí se usó la de nuestro criterio, 0.
- El streaming de verdad: el servicio no trocea, así que el tiempo hasta la primera pantalla es una estimación y los
  cortes se calculan sobre respuestas guardadas.
- Conversaciones con historial y el prompt real del motor: aquí cada tarea va sola.
- La caché de prompt, que el servicio no informa. Con ella la entrada pesaría menos en los tres formatos y las
  diferencias de coste se acortarían.

La página con gráficos de esta comparación es un artifact de Claude: https://claude.ai/artifact/Att5jCddwadVTmNRreMYTP
(privado hasta que se comparta).

### 10.3 Reglas para perder menos pantallas (26 de noviembre de 2026)

La comparación con OpenUI Lang 1.0 y el JSON (§10.2) mostró dónde perdía pantallas UI Lang 3:

- en los cambios que borran y reescriben el mismo nodo, o que borran un id que no existe;
- en las respuestas sin valla;
- en el streaming, que sólo publicaba líneas enteras.

Estas reglas lo resuelven sin cambiar el resto del formato ni el prompt:

1. **La última línea de un id gana** (`aplicar_cambio` y `Flujo`), en vez de rechazar el bloque.
2. **Borrar un id que no está en la pantalla no hace nada**: queda en los avisos.
   Además, `- id` con un espacio vale como `-id`.
3. **Sin valla** (`Vistas.sin_valla`, en el traductor y en `scripts/muestra_ejemplos_ui.py`): una respuesta que son
   sólo líneas del formato se lee como un bloque.
4. **La línea pendiente se publica cerrada** (`Flujo`, `cerrar_cola`).

**Medida, sin gastar:** las mismas respuestas guardadas de la comparación (`docs/pacai/datos/formatos-ui-*.jsonl`
de §10.2), vueltas a validar con el motor nuevo.

- Sólo cambia UI Lang 3.
- Las reglas 1 a 3 cambian la validez. La 4 no, porque las respuestas ya están enteras y el servicio no trocea.

| Modelo | Válidas 1.er intento antes | Ahora | IC95 % Wilson ahora | Exactas antes → ahora | Coste por exacta |
|---|---|---|---|---|---:|
| GPT-6 Luna | 203/208 (97,6 %) | **207/208 (99,5 %)** | 97,3–99,9 % | 197 → 201 | 0,193 $/1000 |
| Claude Haiku 5.5 | 201/208 (96,6 %) | **205/208 (98,6 %)** | 95,8–99,5 % | 186 → 190 | 0,318 $/1000 |
| GLM 5.3 Flash | 45/52 (86,5 %) | **49/52 (94,2 %)** | 84,4–98,0 % | 43 → 47 | 0,272 $/1000 |
| DeepSeek v4.1 Flash, GPT-6.2 Sol | 52/52 | 52/52 | — | sin cambios | — |

- **Luna y Haiku juntos:** UI Lang 3 pasa de 404/416 (97,1 %) a **412/416 (99,0 %)** de válidas, y de 383 a **391**
  exactas.
  - Frente a 415/415 y 377 exactas de OpenUI, y 407/416 y 380 exactas del JSON con ejemplos.
  - Es el formato con más pantallas que hacen lo pedido.
- **Coste por pantalla exacta:** 0,254 $/1000, un 25 % menos que OpenUI y un 37 % menos que el JSON.
- **Los briefs de OpenUI no cambian** (78 %): allí UI Lang 3 falla por referencias, tipos y sintaxis, no por estas
  reglas.
- **Lo que queda** en las 52 tareas con Luna y Haiku (4 de 416):
  - **3 de Haiku**, en la misma tarea: crea un panel nuevo y no lo cuelga de `root`. El benchmark de OpenUI también
    lo cuenta como error (huérfano), y no se arregla sin adivinar dónde va.
  - **1 de Luna:** escribe `***` y un carácter suelto en vez de la valla.

  Para llegar al 100 % habría que tocar el prompt o la gramática, y medirlo con una tanda nueva.

**Lo que no se ha probado:**

- El streaming de verdad: el servicio entrega cada respuesta de una vez.
- Una sesión real en la app.
- Una tanda nueva con los modelos: la validez es la de las respuestas ya guardadas, que se escribieron con el prompt
  de siempre.

**Dos cambios de prompt, de una lluvia de ideas** (GPT-6.2 Sol, Claude Opus 5.5, GLM 5.3 y Kimi K3.1 Pro; 0,19 US$).
Se midieron por separado, con el acuerdo de quedarse sólo con el primero si bastaba:

1. **Regla de cambios** (`ui_lang.prompt`):
   - **Antes:** «Escribe entera la línea de cada nodo que cambia, con todos sus hijos tras >, y las de los nuevos».
   - **Ahora:** «Escribe entera la línea de cada nodo que cambia y las de los nuevos. Cada id nuevo va en el > de su
     padre, en la posición pedida: reescribe la línea del padre con sus demás hijos, salvo los que se borran».
   - Ocupa 7 tokens más.
2. **El ejemplo de `Stack` enseña tarjetas en rejilla:** `tarjetas Stack direction=grid gap=small > palta`, en lugar
   de una fila horizontal de botones.
   - Sólo cambia el prompt compacto (`Catalogo._manual_lang`). El JSON ya lo dice en su línea «un Stack
     direction=grid de Card», así que los modelos sin UI Lang no cambian.
   - Con los dos cambios, el prompt queda en 1255 tokens, frente a los 1249 de antes.

**Medida:**

- `scripts/muestra_ejemplos_ui.py`, brazo `lang`, Luna y Haiku, las 52 tareas y 4 repeticiones, por el servicio
  Alfalfa. Fueron 416 peticiones por tanda: 0,098 US$ la del cambio 1 y 0,100 US$ la de los dos juntos.
- La base son las respuestas de la comparación, validadas con las reglas de arriba; es otra tanda, del mismo día.
- Datos: `docs/pacai/datos/ui-lang-prompt-cambio1.jsonl` y `ui-lang-prompt-cambio1y2.jsonl`.

| Luna y Haiku juntos | Base | Cambio 1 | Cambios 1 y 2 |
|---|---|---|---|
| Válidas a la primera | 412/416 (99,0 %) | 412/416 (99,0 %) | **413/416 (99,3 %)** |
| Exactas | 391 | 386 | 389 |
| Exactas en altas | 237 | 234 | **242** |
| «Tres tarjetas en rejilla», exactas | 1/8 | 4/8 | **8/8** |
| Historial de palta y limón, Haiku: válidas · exactas | 1/4 · 1/4 | 4/4 · 0/4 | 4/4 · 0/4 |
| Coste por petición | 0,239 m$ | 0,234 m$ | 0,240 m$ |

- **El cambio 1 quita los huérfanos de Haiku, pero no basta.** Haiku ya cuelga el panel nuevo de `root`, pero lo pone
  delante de la nota de la fuente (`cifras,p_bajo,p_hist,fuente`), y la tarea pide «al final». Lo mismo le pasa a
  veces en «añade al final un panel con el historial de papa».
- **El cambio 2 cumple:** la tarea de las tarjetas pasa a 8/8 y las exactas de las altas suben de 237 a 242.
- **Con los dos cambios:**
  - las válidas suben de 412 a 413 y las exactas se quedan en lo mismo (391 → 389, dentro del ruido);
  - el coste por petición no cambia.
- **Los cambios de Haiku que no salen exactos** son sobre todo:
  - el panel nuevo delante de la fuente;
  - renombrar un id que la comprobación espera (`bajo_top` → `sube_top`).

  Aparecieron 2 nodos que desaparecen sin pedirlo, siempre ese renombrado.
- **Se quedan los dos cambios.** Lo siguiente, si se quiere, sería decir en la regla que «al final» va detrás de todos
  los hijos, también de la nota de la fuente, y medirlo igual.

**Un tercer cambio, probado y descartado.** Se añadió a la regla «(«al final» es detrás del último hijo que ya tiene,
aunque sea una nota)», para que Haiku no pusiera el panel nuevo delante de la nota de la fuente. Se midió con los
cambios 1 y 2: la misma tanda, 0,101 US$ y `docs/pacai/datos/ui-lang-prompt-cambio3-descartado.jsonl`.

- **Lo que mejora:**
  - las exactas suben a 394, las más altas de las tres tandas;
  - «añade al final un panel con el historial de papa» pasa a 8/8.
- **Lo que empeora:**
  - Haiku vuelve a dejar el panel huérfano en la tarea del historial de palta y limón (3 de 4), lo que el cambio 1
    había arreglado en sus dos tandas;
  - las válidas a la primera bajan de 413 a 407/415. Una petición la rechazó el filtro de OpenAI.
  - De esas válidas que se pierden, 5 son de Luna y tienen la valla mal escrita (`~~~`, `/`, `*** Begin Patch`).

Entre tandas, las cifras se mueven unas 4 pantallas, así que la mejora en exactas no compensa volver a perder lo que
arregló el cambio 1. La regla se queda como estaba con los cambios 1 y 2.

**Vallas `~~~` y `:::`.** Luna, con esfuerzo bajo, a veces abre el bloque con `~~~pacai-ui-lang` (la otra valla de
CommonMark) o `:::pacai-ui-lang` (la de las directivas).

- **Qué hace ahora `partir`:** acepta las tres marcas y cierra cada bloque con la suya. Un bloque ``` se sigue
  cerrando sólo con ``` sola en su línea. Uno ~~~ o ::: se cierra con una línea que empieza por su marca, aunque el
  modelo escriba algo detrás.
- **Dónde más se aplica:** el traductor (`tiene_valla`) y la regla sin valla, que no salta si hay cualquiera de las
  tres marcas. El script de la muestra lee igual.
- **Efecto sobre las respuestas guardadas,** sin gastar: sólo 3 de las 1663 de UI Lang de este apartado usan estas
  vallas.
  - Una pasa a válida: en la tanda descartada, de 407 a 408.
  - Las otras dos ya valían por un bloque ``` que venía detrás.
  - Las vallas rotas de verdad (`***` con un carácter suelto, `/ pacai-ui-lang`) siguen siendo texto.

**Comprobaciones de «exacta», revisadas** (`scripts/muestra_ejemplos_ui.py`). Algunas comprobaban una forma concreta
y no lo pedido. Ahora aceptan otra forma de dibujar exactamente lo mismo:

- **«En rejilla»:** un `Stack direction=grid` o un `Grid`.
- **«Lado a lado»:** un `Stack` horizontal o un `Grid` de 2 columnas o más que contenga los dos paneles.
- **«Añade al final»** (historial de papa, historial de palta y limón, botón Actualizar):
  - vale el nodo nuevo detrás de todos los hijos;
  - también vale justo antes de la nota final en caption (la fuente). Las dos pantallas ponen lo nuevo al final de lo
    que se ve.
  - Cuando la tarea dice «debajo de la fuente» (`m_formulario`), sigue exigiendo que vaya detrás.
- **«Una tarjeta "Resumen de hoy"»:** un `Card` o un `Panel` con ese título y la cifra dentro, aunque no sea su hijo
  directo.
- **«Un título "Mercado de hoy"»:** un `Text` en estilo title o un `Panel`/`Card` con ese título.
- **«Envía alerta»:** un evento cuyo nombre contenga `alerta` (los modelos escriben `suscribir_alerta`).
- **«Cambia las barras de p_bajo»:** las barras dentro de `p_bajo`, aunque el modelo les cambie el id.

**Siguen siendo estrictas:**

- las cifras que no leen `$periodo`/`$familia`, porque no seguirían los filtros;
- un título suelto donde se pidió un panel;
- «una rejilla de 3 columnas» hecha con un `Stack` sin columnas;
- un `Stack direction=grid` para «lado a lado», que en una pantalla estrecha pone los paneles uno debajo de otro.

**Las mismas respuestas guardadas, sin gastar** (las 52 tareas, Luna y Haiku juntos):

| | Válidas | Exactas antes | Exactas con las comprobaciones revisadas | Coste por exacta |
|---|---|---|---|---|
| OpenUI Lang 1.0 (comparación) | 415/415 | 377 | 407 | 0,314 m$ |
| PACAI JSON con ejemplos (comparación) | 407/416 | 380 | 400 | 0,383 m$ |
| UI Lang 3 con las reglas, prompt anterior (comparación) | 412/416 | 391 | 410 | 0,242 m$ |
| **UI Lang 3 con los cambios 1 y 2 de prompt** (su tanda) | **413/416** | 389 | **409** | **0,244 m$** |

- **OpenUI es el que más sube** (+30), porque sufría las mismas formas que ahora se aceptan.
- **Las diferencias entre formatos en exactas se acortan.** UI Lang 3 queda a la par de OpenUI y por delante del JSON.
- **El coste por pantalla exacta no cambia de orden:** UI Lang 3 cuesta un 22–23 % menos que OpenUI y un 36 % menos
  que el JSON.
- **Con los cambios 1 y 2**, Haiku hace bien la tarea del historial de palta y limón en 4 de 4: ya no la deja huérfana,
  y ponerla antes de la nota vale. Sólo quedan 4 no exactas:
  - Luna, en «rejilla de 3 columnas» con un `Stack` (2);
  - Haiku, en «lado a lado» con un `Stack direction=grid` (2). Esto puede ser un efecto del ejemplo de rejilla del
    cambio 2.

**Por qué OpenUI no dejaba huérfanos.**

- **Su gramática no lo evita.** En OpenUI, el padre también nombra a sus hijos en una lista (`root = Stack([…, p_hist,
  …])`), igual que el `>` de UI Lang, y un nodo sin colgar también es un error.
- **Lo evita su prompt.** Tiene una regla propia: «toda variable, salvo root, tiene que estar referenciada; si no, no
  se dibuja: pon siempre las variables que defines en la lista de children de su padre». Y la repite en la
  verificación final.
- **El cambio 1 lleva esa regla al prompt de UI Lang,** y los huérfanos de Haiku desaparecen en las dos tandas que lo llevan.
- **Lo de poner el panel antes de la fuente también lo hacía Haiku en OpenUI** (4 de 4 en las dos tareas «al final»).
  No era un fallo del formato.

**Una línea con sólo el id** (`c_bajaron` en vez de `-c_bajaron`). Una segunda lluvia de ideas (los mismos cuatro
modelos, 0,90 US$) descartó dos alternativas:

- borrar sólo reescribiendo el padre, por el riesgo de perder hermanos y por los tokens de salida;
- otra sintaxis de baja, que también se puede escribir a medias.

Se quedó con una tolerancia que no adivina (`aplicar_cambio`):

- La línea se ignora sólo si **las dos lecturas dan la misma pantalla**: ignorarla y tratarla como `-id`. Pasa
  cuando el modelo también reescribió el padre sin ese nodo.
- **Se rechaza el bloque, como antes:**
  - si las dos lecturas dan pantallas distintas, por ejemplo si el padre no lo suelta o si otra línea lo cuelga de
    otro sitio;
  - si el id no estaba en la pantalla, porque puede ser una línea cortada;
  - si es `root`;
  - si hay más de un id suelto en el bloque.
- Hay pruebas para cada caso.
- El prompt no cambia.

**Sobre las respuestas guardadas, sin gastar:** se compararon, una por una, las 1819 respuestas de UI Lang de este
apartado con el motor anterior y con el nuevo.

- Sólo cambian 2, y las dos pasan de no válida a válida y exacta: Haiku en «quita la cifra Bajaron» y Luna en «quita
  los filtros».
- Ninguna que ya valía cambia de resultado.
- De las 167 bajas `-id` de los cambios válidos, 152 sobran: quitarlas da la misma pantalla, porque el padre ya se
  reescribió sin ese nodo.

| UI Lang 3 con los cambios 1 y 2 (Luna y Haiku, 52 tareas) | Válidas a la primera | Exactas | Coste por exacta |
|---|---|---|---|
| Antes de esta regla | 413/416 (99,3 %) | 409 | 0,244 m$ |
| **Con esta regla** | **415/416 (99,8 %)** | **411** | **0,242 m$** |
| OpenUI Lang 1.0, como referencia | 415/415 (100 %) | 407 | 0,314 m$ |

La única que falla es la valla pegada de Luna (`-cifra?```pacai-ui-lang …`): es otro problema y se deja aparte.

**Un bloque que se vuelve a abrir sin cerrarse.** A veces Luna empieza un bloque, se equivoca a mitad de una línea y
abre otro igual sin haber cerrado el primero:

~~~
```pacai-ui-lang view=tablero
root Stack direction=vertical gap=medium > cifras,p_bajo,fuente,p_historial
-cifra?```pacai-ui-lang view=tablero
root Stack direction=vertical gap=medium > cifras,p_bajo,fuente,p_historial
-filtros
…
~~~

Hasta ahora `partir` juntaba los dos en uno y el bloque se rechazaba. Ahora, una valla del perfil que aparece dentro
de un bloque todavía abierto, al principio de la línea o detrás de lo que sea, funciona así:

- **Misma vista** (o el primer bloque no la decía): **gana el bloque nuevo**, porque el modelo empezó de nuevo. Es la
  misma idea que «gana la última línea de un id». Queda en `Vistas.reparaciones`.
- **Otra vista:** el primer bloque se cierra y cada uno vale por separado.

El script de la muestra lee igual, y hay una prueba.

**Sobre las respuestas guardadas, sin gastar:** se compararon, una por una, las 2391 respuestas de UI Lang y del JSON
de este apartado.

- Sólo cambian 2, las dos de Luna, de no válida a válida y exacta: «quita los filtros», en la tanda que se queda, y
  «borra el panel», en la del cambio 1.
- Ninguna que ya valía cambia de resultado.

| UI Lang 3 con los cambios 1 y 2 (Luna y Haiku, 52 tareas) | Válidas a la primera | IC95 % Wilson | Exactas | Coste por exacta |
|---|---|---|---|---|
| Antes de las dos últimas reglas | 413/416 (99,3 %) | 97,9–99,8 % | 409 | 0,244 m$ |
| Con la baja sin guion | 415/416 (99,8 %) | 98,7–100 % | 411 | 0,242 m$ |
| **Con el bloque reabierto** | **416/416 (100 %)** | **99,1–100 %** | **412** | **0,242 m$** |
| OpenUI Lang 1.0, como referencia | 415/415 (100 %) | 99,1–100 % | 407 | 0,314 m$ |

Con Luna y Haiku, UI Lang 3 queda con todas las pantallas válidas a la primera en las 52 tareas. Hace lo pedido en 412
de 416 y cuesta un 23 % menos por pantalla exacta que OpenUI.

Las 4 que no son exactas son modelos que eligen otra forma de lo pedido:

- Luna, en «una rejilla de 3 columnas», usa un `Stack` sin columnas (2);
- Haiku, en «lado a lado», usa un `Stack direction=grid`, que en una pantalla estrecha los apila (2).

Es una sola tanda, así que no demuestra que el fallo sea imposible.

**Confirmación con una tanda nueva (27 de noviembre de 2026).** Las cifras de arriba salían de volver a validar
respuestas ya guardadas. Para confirmarlas se pidieron respuestas nuevas:

- Motor y prompt de `main` 0.99.55, sólo el brazo `lang`.
- Luna y Haiku, las 52 tareas y 4 repeticiones: 416 peticiones por el servicio Alfalfa.
- Coste: 0,098 US$, con un tope de 0,30.
- 0 errores del servicio.
- Datos: `docs/pacai/datos/ui-lang-confirmacion.jsonl`.

| | n | Válidas 1.er intento | IC95 % Wilson | Exactas | Entrada (med.) | Salida (med.) | Total (med. · p90) | Coste por exacta |
|---|---:|---|---|---:|---:|---:|---|---:|
| GPT-6 Luna | 208 | **208 (100 %)** | 98,2–100 % | 205 | 1328 | 81 | 1,51 s · 2,18 s | 0,186 m$ |
| Claude Haiku 5.5 | 208 | **208 (100 %)** | 98,2–100 % | 207 | 2145 | 117 | 1,15 s · 1,51 s | 0,291 m$ |
| **Luna y Haiku** | 416 | **416 (100 %)** | **99,1–100 %** | **412** | 1792 | 95 | 1,32 s · 1,89 s | **0,239 m$** |
| Revalidación anterior, como referencia | 416 | 416 (100 %) | 99,1–100 % | 412 | — | — | — | 0,242 m$ |
| OpenUI Lang 1.0 (§10.2), como referencia | 415 | 415 (100 %) | 99,1–100 % | 377 | — | — | — | 0,314 m$ |

- **Se confirma con respuestas nuevas:** ninguna pantalla no válida, en altas ni en cambios (256/256 y 160/160).
  Ninguna necesitó la corrección.
- **Las 4 que no hacen lo pedido** son las mismas formas de antes:
  - «una rejilla de 3 columnas» con un `Stack direction=grid` sin columnas (Luna, 2);
  - «lado a lado» con un `Stack direction=grid` (Haiku 1, Luna 1).

  Todos los cambios salen exactos (160/160).
- **Coste:** 0,239 m$ por pantalla exacta, un 24 % menos que OpenUI en §10.2.
- **El intervalo de Wilson** dice que, con esta muestra, la tasa real de pantallas válidas a la primera está por
  encima del 99,1 % con un 95 % de confianza. No demuestra que un fallo sea imposible.

### 10.4 Dos tolerancias más que dejan el mismo árbol (27 de noviembre de 2026)

Salen del benchmark de UI Lang 3 con los briefs de OpenUI y de una lluvia de ideas de la Mesa Dorada. Ocho
pantallas de Luna y Haiku se perdían por sintaxis, y casi todas por dos fallos que no cambian lo pedido:

5. **Un `>` sin nada detrás** (`current Card "Ahora" detail="…" >`) vale como si no estuviera: el nodo no tiene
   hijos. Una lista empezada (`> a,`) sigue siendo un error.
6. **Ids con tildes, eñes o mayúsculas** (`previsión_panel`, `serviceTable`) se leen en ASCII y minúsculas
   (`prevision_panel`, `servicetable`), en la línea del nodo, en los hijos y en las bajas.
   - Queda en las reparaciones (`previsión → prevision: los ids van en a-z, 0-9 y _.`).
   - **No se juntan nunca dos ids distintos.** Si un bloque escribe `prevision` y `previsión`, es un error. En
     un cambio, los ids de la pantalla que ya existe cuentan, así que `fuénte` no es `fuente`.
   - La primera letra sigue teniendo que ser una minúscula: `T Text "a"` es un error.

Lo que **no** se repara, porque obligaría a adivinar:

- un panel que el modelo define pero no pone en el `>` de ningún padre;
- un hijo que se nombra en el padre y no se define.

Son la mayoría de los fallos de referencias de los briefs. Si se arreglan, será en el prompt, y eso pide una tanda
nueva.

**Medida, sin gastar:** todas las respuestas guardadas de UI Lang 3, vueltas a validar con el motor nuevo. Son las
de §10.2 y las de la clasificación de 15 modelos del benchmark (1868 respuestas).

- **5 pantallas más,** válidas y exactas:
  - 4 de Luna en los briefs de OpenUI: de 73/92 a 77/92. Con Luna y Haiku juntos, los briefs pasan de 144/184 (78,3 %) a
    148/184 (80,4 %).
  - 1 de GLM 5.3 Flash en las 52 tareas: de 49/52 a 50/52.
- **Ninguna pantalla que ya valía cambia:** las 1868 dan los mismos nodos `pacai.ui/1`.
- **Las demás pantallas con estos dos fallos** (Haiku y otros modelos) ahora pasan la sintaxis, pero fallan en un
  error real del catálogo, casi siempre un enum. Es el diagnóstico correcto para la corrección.
