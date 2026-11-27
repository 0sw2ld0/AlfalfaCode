# Demo web del perfil `ui` de PACAI

Un chat en el navegador donde el agente dibuja pantallas mientras conversa: una tabla de tickets, un formulario para
crear uno, el detalle de cada ticket y un gráfico. Lo que pulsas vuelve al agente y él actualiza la pantalla. La
especificación del perfil está en [`docs/pacai/perfil-ui.md`](../../pacai/perfil-ui.md).

Necesita Node.js 20 o posterior. No usa dependencias ni compila nada.

## Probarla sin modelo

El agente simulado usa el transporte PACAI real de AlfalfaCode (el mismo `TransportePacai` y el mismo traductor que
`alfalfa --pacai`), pero responde con un guion en vez de llamar a un modelo. No gasta créditos. Desde la raíz del
repositorio, con el `.venv` de AlfalfaCode creado:

```sh
node docs/demos/pacai-ui/servidor.mjs --simulado
```

Abre <http://127.0.0.1:4173> y prueba:

1. **«enséñame los tickets»:** aparece la tabla con un formulario.
2. **Crea un ticket** con el formulario: la tabla se actualiza. Como el agente la manda con `placement=latest`, la
   pantalla baja al último mensaje y en su sitio anterior queda una copia apagada.
3. **Pulsa una fila:** sale su detalle con un botón para cerrarlo.
4. **«dame un gráfico por prioridad»:** un gráfico de barras.

Activa **Ver mensajes PACAI** para ver, a la derecha, cada mensaje que va y viene.

## Con AlfalfaCode de verdad

```sh
node docs/demos/pacai-ui/servidor.mjs --carpeta ~/mi-proyecto
```

Abre `alfalfa --pacai` en esa carpeta con el modelo que tengas configurado (o el de `--modelo`). El modelo recibe
el catálogo en su prompt y decide cuándo dibujar. Las aprobaciones de herramientas salen en el chat con botones.
Otras opciones: `--alfalfa RUTA` para elegir el ejecutable y `--puerto N`. `--help` las lista todas.

## Prototipo: PACAI UI Lang y piezas declarativas

```sh
node docs/demos/pacai-ui/servidor.mjs --ui-lang --carpeta ~/mi-proyecto
```

`--ui-lang` abre AlfalfaCode con `ALFALFA_PACAI_UI_LANG=1`, declara las piezas (`pieces: true`) y dos consultas de
ejemplo (`formPedido` y `comparar`) que resuelve el propio servidor. <http://127.0.0.1:4173/piezas> enseña las
cuatro pantallas de la 4.ª lluvia (margen, vista que cambia, pedido con validación y comparar) sin agente ni gasto.
`node docs/demos/pacai-ui/conformidad.mjs` pasa los vectores compartidos con el motor. Está descrito en
[perfil-ui.md §10](../../pacai/perfil-ui.md#10-pacai-ui-lang-con-todos-los-modelos-y-piezas-declarativas-detrás-de-un-flag).

## Cómo está hecha

| Archivo | Qué hace |
| --- | --- |
| `servidor.mjs` | El cliente PACAI. Abre el agente por stdio y negocia `work` + `ui` con el catálogo base. Pasa los mensajes del agente a la página por Server-Sent Events (`GET /eventos`) y recibe los de la página por `POST /enviar`. Solo escucha en `127.0.0.1`, rechaza otros orígenes y solo deja pasar `input`, `approval_result` e `interrupt`. |
| `ui.js` | El original del renderizador, que la extensión de VS Code copia en `alfalfa-vscode/media/ui.js`. Dibuja el catálogo base: una `Vista` por pantalla, que aplica los `patch` y `replace`, guarda el estado de los campos y ejecuta las acciones `send`, `set` y `open_url`. Todo se construye con `createElement` y `textContent`: el texto del agente nunca se interpreta como HTML. |
| `app.js` | La página de chat: mensajes, pantallas, aprobaciones, el botón de parar y la traza de mensajes. Un tema puede poner en `<body>` el texto de la barra de estado: `data-trabajando` mientras el agente trabaja y `data-listo` cuando espera (sin él, la sesión). |
| `estilo.css` | Estilos, con tema claro y oscuro. |
| `piezas.html`, `piezas.js` | Las cuatro pantallas de las piezas declarativas, sin agente (`/piezas`, con `--ui-lang`). |
| `conformidad.mjs` | Corre los vectores de `tests/conformidad/pacai-ui-piezas.json` contra `ui.js`. |
| `agente_simulado.py` | El agente de guion para `--simulado`. |

Al recargar la página, el servidor reenvía la sesión entera y la página la vuelve a montar. La prueba
`test_el_agente_simulado_de_la_demo_habla_pacai_valido` (en `tests/test_pacai_ui.py`) comprueba que una sesión con
el agente simulado cumple las reglas de PACAI 0.3 y el esquema del perfil.

## Límites

- **Las pantallas llegan cuando termina cada mensaje.** El motor entrega el texto del modelo mensaje a mensaje, no
  token a token, así que una pantalla no se va armando mientras el modelo escribe.
- **Una sola sesión por servidor.** Varias pestañas ven la misma conversación.
- **Lo que escribes en un campo se pierde** si el agente manda la foto completa de la vista antes de que lo envíes.
