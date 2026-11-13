# Mensajes con el agente ocupado

Lo que escribes mientras el agente trabaja queda «en cola» y entra en su siguiente
paso. Debajo del mensaje en cola hay una fila de acciones, como en Claude:

- **Copiar**: copia su texto, sin las imágenes.
- **Editar** (flecha circular): lo quita de la cola y lo devuelve al cuadro con sus
  imágenes, detrás de lo que ya hubieras escrito.
- **Redirigir**, como steer de Codex: el modelo deja la respuesta que estaba
  pensando y la empieza otra vez con tu mensaje dentro, sin cortar el turno.
  También corta las esperas de `wait_agent` y `command_status`; una orden que ya
  corre termina antes.
- **Quitar** (✕): lo saca de la cola sin enviarlo.
- **Enviar ahora**, como Claude Code: detiene el turno en marcha y tu mensaje abre
  el siguiente. La sesión sigue abierta («Turno interrumpido por el usuario»).

Redirigir y Enviar ahora sólo salen con un turno en marcha. Editar y Quitar sólo
funcionan mientras el mensaje no haya entrado en el contexto del modelo. Si ya entró,
la app avisa («Ya lo leyó el agente») y el mensaje se queda en la conversación.

Por `--stdio`, cada `{"tipo":"mensaje"}` puede llevar un `id` propio. Con
`{"tipo":"quitar_de_cola","id":…}` el motor lo saca de la cola y responde
`{"tipo":"quitado_de_cola","id":…,"quitado":true}`. Si no lo saca, responde con
`"quitado":false` y un `motivo`: `entregado` si el modelo ya lo tiene, `desconocido`
si nunca llegó. `{"tipo":"enviar_ahora","id":…}` adelanta justo ese mensaje, aunque
otro diga lo mismo. Si ya entró, no corta el turno ni lo vuelve a enviar. Los mensajes
sin `id` funcionan como antes, pero no se pueden quitar.
La terminal (`alfalfa "encargo"`) no tiene cola: cada orden es un solo encargo.

Entre turnos, un mensaje (o un botón de la caja de cierre) abre el turno siguiente
y el motor lo confirma: deja de verse «en cola» y la sesión pasa a «trabajando».
