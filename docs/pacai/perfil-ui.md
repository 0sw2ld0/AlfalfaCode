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
    `--pacai --reanudar` las recupera: la vista sigue por la revisión siguiente y no vuelve a la 1, y una acción
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

    **Recomendación: no encenderlo por defecto.** Ahorra un 10 % de entrada cuando no se dibuja, pero baja de 46 % a
    33 % las pantallas que el modelo decide dibujar solo, que es justo lo que el perfil busca. Lo siguiente que
    probaría es dejar en el prompt la parte de *cuándo* dibujar del manual (unos 250 tokens) y diferir sólo el
    formato, el catálogo y los ejemplos, y medirlo también con Haiku.

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

## 10. PACAI UI Lang (por modelo) y piezas declarativas (detrás de un flag)

Sale de la 3.ª y la 4.ª lluvia de ideas. **PACAI UI Lang va encendido por modelo**: sólo con los modelos con los que
se midió y cumple el criterio de adopción (hoy GPT-6 Luna y Claude Haiku 5.5, `ui_lang.MODELOS_MEDIDOS`; §10.1).
Con cualquier otro modelo el JSON con ejemplos de arriba sigue siendo el camino normal. `ALFALFA_PACAI_UI_LANG=1`
lo fuerza con cualquier modelo (y es lo único que enciende las piezas declarativas); `ALFALFA_PACAI_UI_LANG=0` lo
apaga con todos, y así se revierte. La app de Mac no lo conoce y no hace falta: el motor sigue mandando
`pacai.ui/1`.

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
  hijo que la línea del padre deja fuera también se va. Todo el bloque se aplica o nada. El modelo ve la pantalla con
  sus líneas enteras (`Vistas.resumen_lang`). La versión 1 (`~id`, `+>`) y el `rev` escrito por el modelo (versión
  2) se quitaron: eran donde más fallaba.
- **Reparaciones seguras:** una línea idéntica a una que ya vale se ignora, en el reintento de un alta la línea de
  un id que ya existe lo sustituye, y un `rev` del modelo se ignora. Nunca cambian lo que se pidió. Además, sólo en
  un alta (y anotadas en `Vistas.reparaciones`, sin contar como error):
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

**Lo que no se ha probado**

- Ningún otro modelo (Sonnet, Opus, GPT-6 sin Luna, GLM, DeepSeek, Gemma…): por eso siguen con el JSON.
- Una sesión de verdad en la app de Mac con Luna o Haiku: la medida es por tarea, sin herramientas ni historial.
- Cambiar de modelo a mitad de sesión con pantallas en el historial.
- El streaming por trozos sigue sin servirse (el servicio no trocea).
- Datos: `muestra-haiku-lang3-valla.jsonl` y `muestra-haiku-lang3-raiz.jsonl`, en las notas de la sesión.
