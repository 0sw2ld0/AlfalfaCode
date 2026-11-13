# Procesos en segundo plano

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
