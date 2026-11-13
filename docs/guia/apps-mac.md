# Probar apps del Mac (control de apps)

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
