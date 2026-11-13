# Permisos

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

El hogar de AlfalfaCode (`ALFALFA_HOME`, por defecto `~/.alfalfa`) queda fuera de las
herramientas de archivos y de las escrituras del sandbox, incluso al abrir una sesión
en `~` o dentro del propio hogar. La protección se conserva en hooks, servidores MCP
locales, diagnósticos y subagentes. Antes de iniciar el sandbox se comprueban los
metadatos del hogar: si contiene archivos con enlaces duros o no se puede completar
el recorrido, se rechaza el arranque para evitar escrituras por otro nombre.

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
