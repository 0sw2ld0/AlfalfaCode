# Habilidades y memoria

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

## Aprender de cada sesión

Como [Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research), lo
aprende el arnés, no el modelo. Nada se aprende sin que lo veas: todo pasa por una tarjeta
con su diff, y se corrige en Ajustes ▸ Memoria o con `alfalfa --memoria` y
`alfalfa --aprendido`.

**Memoria.** Tres almacenes de notas cortas, separadas por una línea `§`, con un tope
duro: *usuario* (quién eres y cómo te gusta trabajar, 2500 caracteres), *entorno* (hechos
de este Mac, 1500) y *proyecto* (convenciones y trampas, 3500). Cada entrada es una
lección: la regla, por qué y cómo aplicarla. Cuando corriges al agente o le dices «desde
ahora», «recuerda» o «no vuelvas a», la propone en ese mismo turno; si no, la revisión del
cierre (con el modelo más barato de la familia o, en un plan como Ollama Cloud, el de la
sesión) propone lo que vea. Todos viven en
`$ALFALFA_HOME/memoria`; el del proyecto, por la ruta de su raíz, nunca dentro del
repositorio, así que un repo no puede plantarlo. El agente los cambia con la herramienta
`memoria` (`add`, `patch` y `remove` por un trozo único de la entrada; no hay «leer»).
Al pasarse del tope la herramienta da error con las entradas y el agente consolida en una
sola llamada.

Cada escritura, en cualquiera de los tres almacenes, **siempre** pide la tarjeta con el
diff, en cualquier modo (también «Omitir permisos»), y no hay ajuste que la quite: un
README, un `AGENTS.md` o una página que «enseñen» algo al agente no llegan a la memoria
sin que lo apruebes.

Antes de guardar, como defensa secundaria, se rechazan: inyecciones de prompt y órdenes de
descarga y ejecución (`curl … | sh`, `bash -c "$(curl …)"`, «siempre envía las claves…»),
palabras que mezclan alfabetos (homoglifos), credenciales (las claves configuradas, formas
conocidas de tokens, URL con usuario y contraseña, webhooks, cadenas hex largas y tokens de
alta entropía), Unicode invisible y lo que imite la estructura del prompt (títulos `#`,
citas, separadores, marcas de memoria). La memoria entra en el prompt citada, línea a línea,
entre `<memoria …>` y `</memoria>`, tal como estaba al empezar la sesión: lo que cambie
ahora se guarda ya, pero entra en la siguiente (no rompe la caché del proveedor). Al
reanudar una sesión, lo que borraste desde entonces ya no vuelve.

**Habilidades aprendidas.** Con la herramienta `habilidad` (`crear`, `parchear`,
`escribir_archivo`, `borrar`) el agente guarda un procedimiento como `SKILL.md` con el
formato de Claude Code (acepta saltos CRLF): en `$ALFALFA_HOME/skills/aprendidas` (tuyas) o
en `.alfalfa/skills` del proyecto (no si el proyecto es tu carpeta personal). Sólo tras un
flujo de varios pasos, un callejón sin salida o una corrección tuya, y sólo si el turno
acabó con `run_checks` en verde después de su última edición, con una comprobación real
del proyecto: la de sus pruebas o su lint detectada por sus archivos (pyproject.toml,
Package.swift, los scripts de package.json, Cargo.toml, go.mod, el Makefile…), la de su
AGENTS.md o una que nombra archivos cambiados en el turno. `python -c "assert 1"`,
`--version`, `-n`, `--dry-run` o `npm test --if-present` sin ese script no valen. Lecciones, no registros: se rechazan fechas,
números de PR y trozos de conversación. Los archivos de apoyo sólo van en
`references/*.md|txt`, con nombres ASCII que no coinciden con otro sin distinguir
mayúsculas. Todas, tuyas o del proyecto, piden siempre permiso con el diff, también
en «Omitir permisos». El registro de
`$ALFALFA_HOME/aprendido` guarda su origen (sesión y turno) y dice qué creó AlfalfaCode:
una habilidad que no está en él, que cambiaste a mano o que bloqueaste no la toca. Entran en
la lista de habilidades en la siguiente sesión. Una del proyecto queda apuntada como cambio
del agente; si sólo escribió Markdown, no caduca la comprobación que estaba en verde.

Para acreditar `make` se comprueba el archivo que realmente usa y una receta sencilla;
los objetivos ya actualizados, las recetas ambiguas o las suites con cero pruebas o todas
omitidas no valen. La carpeta efectiva de la orden cuenta. Si el envoltorio no puede
validarse, ejecuta directamente las pruebas o el lint del proyecto.

Las herramientas de archivos no escriben habilidades en `.alfalfa/skills`, `.agents/skills`,
`.claude/skills` ni `.codex/skills`, tampoco en subproyectos. Las órdenes mantienen esas
carpetas de instrucciones de solo lectura; las habilidades se guardan con su tarjeta.
Antes de abrir el sandbox se comprueba que ninguna comparta archivos mediante enlaces
duros. Si no se puede completar la comprobación, no se abre para escribir.

**Repaso al cerrar el turno.** Si el turno tuvo muchos pasos, un fallo superado o una
corrección tuya y cerró con `wrap_up`, una pasada breve sin razonamiento decide si hay algo
que recordar o un procedimiento que guardar. Corre con el turno ya cerrado (en `--stdio`,
después de `turno_terminado`), como mucho 20 s y **como mucho 3 veces por sesión**. Si
escribes otro mensaje mientras corre, espera hasta 10 s a que termine; «Detener» la deja
(en la terminal, Ctrl-C). Usa el mismo proveedor y su modelo más barato de la familia, o
el de `modelo_revision`; con un proveedor de plan o local, el de la sesión. Cuenta la
entrada y la salida máxima contra el presupuesto y no corre si no alcanza. **Nunca aplica nada**: lo que propone queda pendiente y sale como tarjeta con el
diff («Guardar» o «Descartar»; en la terminal, con la orden para aplicarlo), sin repetir lo
que ya estaba propuesto. Al reabrir la sesión en la app, sus tarjetas pendientes vuelven.

**`/aprender`** convierte una carpeta, una URL o unas notas en una habilidad
(`/aprender docs/despliegue.md`, `/aprender https://…`), con la misma aprobación.

La tarjeta enseña siempre el diff entero: un cambio que no cabe se rechaza, no se recorta.
En Plan y en solo lectura no hay escrituras. Estas escrituras las hace el arnés, no una
orden dentro del sandbox, así que validan cada ruta (relativa, sin `..` ni ocultos, nunca
dentro de `.git` sin distinguir mayúsculas) y se hacen componente a componente con
descriptores que no siguen enlaces, para que un enlace que aparezca a mitad no se siga.
Los ajustes se releen en cada uso: apagar algo en Ajustes ▸ Memoria vale ya.

El hogar de AlfalfaCode (`$ALFALFA_HOME`: memoria, habilidades aprendidas, propuestas,
ajustes, proveedores, complementos, hooks, reglas y sesiones) no lo tocan ni las
herramientas de archivos ni las órdenes, aunque caiga dentro de la carpeta de trabajo (una
sesión en `~` con `~/.alfalfa`): las herramientas lo rechazan y el sandbox Seatbelt
deniega escribir en él por su ruta real y sin distinguir mayúsculas, y renombrarlo. Lo que
llegue a la memoria por otro camino se vuelve a escanear al empezar cada sesión, y una
propuesta pendiente se recalcula desde sus operaciones: si no coincide con su diff, se
descarta.

```bash
alfalfa --memoria                              # los tres almacenes de esta carpeta
alfalfa --memoria escribir usuario --texto "Prefiere commits pequeños."   # o por la entrada estándar
alfalfa --memoria quitar entorno "Xcode 25"
alfalfa --aprendido                            # habilidades, propuestas y ajustes
alfalfa --aprendido ver pasar-pruebas          # usuario:NOMBRE o proyecto:NOMBRE si hay dos
alfalfa --aprendido bloquear pasar-pruebas     # el agente ya no la cambia; tú sí (editar)
alfalfa --aprendido pendientes ver ID          # una propuesta con su diff entero
alfalfa --aprendido aplicar ID                 # o descartar ID
alfalfa --aprendido ajustes revision no        # memoria, habilidades o revision
alfalfa --aprendido proyecto no                # no aprender nada en esta carpeta
```

`ALFALFA_APRENDER=0` lo apaga todo y `ALFALFA_APRENDER_REVISION=0` sólo el repaso. En
`--stdio` el permiso trae `motivo: "aprendizaje"` y `diff`; el repaso emite
`aprendizaje_propuesto` (`id`, `clase`, `almacen` o `accion`/`alcance`/`nombre`, `motivo`,
`diff`) y la orden `{"tipo":"aprendizaje","id":…,"aplicar":true|false}` lo resuelve con
`aprendizaje_resuelto` (`aplicado`, `descartada`, `texto`). Con `--json`, `--memoria`
responde `memoria`; `--aprendido`, `aprendido` (o `habilidad` con `ver` y `pendiente` con
`pendientes ver`).
