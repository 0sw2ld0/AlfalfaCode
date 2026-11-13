# Guía de AlfalfaCode

Cada tema está en su archivo. La página de cada uno da nombre a su sección de
[../README.md](../../README.md).

## Empezar

- [Instalar](instalar.md): Homebrew, DMG, `instalar.sh`, desinstalar y desde el código.
- [La primera vez, sin claves](primera-vez.md): modelos gratis, conectar OpenRouter en un clic.
- [Uso diario](uso.md): órdenes de la terminal, opciones de cada mensaje, órdenes con «/» y chats de Ollama.
- [Actualizar](actualizar.md): buscar versión nueva, qué actualiza cada instalación.

## Proveedores y coste

- [Proveedores](proveedores.md): la tabla, Claude, todos los modelos con tu clave y los compatibles con OpenAI.
- [Catálogo, coste y presupuesto](catalogo.md): models.dev, Gratis ahora, coste estimado, caché de prompts y presupuesto.

## El trabajo del agente

- [Imágenes](imagenes.md): adjuntar imágenes, visión, y enseñar imágenes y vídeos.
- [Adjuntar archivos](archivos.md): PDF, texto, código, Word y Excel.
- [Sesiones y búsqueda](sesiones.md): sesiones desde la app y buscar en sesiones anteriores.
- [Contexto y compactación](contexto.md): modelo y esfuerzo, compactar, modelos que cobran más por contexto largo.
- [Plan del agente](plan.md): la lista de tareas con `update_plan`.
- [Volver a un mensaje y deshacer el código](deshacer.md): instantáneas por turno, deshacer y rehacer.
- [Cierre de cada turno](cierre.md): el resumen con siguiente paso y las sesiones propuestas.

## Controles y seguridad

- [Permisos](permisos.md): modos de permisos y «Permitir en esta sesión».
- [Procesos en segundo plano](procesos.md): procesos largos y mantener el Mac despierto.
- [Errores al editar (servidores de lenguaje)](diagnosticos.md): diagnósticos tras cada edición.
- [Mensajes con el agente ocupado](ocupado.md): la cola de mensajes, redirigir y enviar ahora.
- [Habilidades y memoria](habilidades.md): skills sueltas y lo aprendido de cada sesión, con tu aprobación.
- [Subagentes](subagentes.md): `spawn_agent` y los tipos de agente.

## Integraciones

- [Venir de OpenCode, Codex o Claude Code](migrar.md): AGENTS.md, CLAUDE.md, MCP, agentes y órdenes «/» de otras herramientas.
- [Probar apps del Mac (control de apps)](apps-mac.md): usar una app que construyó el agente.
- [Navegador propio (app de Mac)](navegador.md): las herramientas `browser_*`, permisos y Chromium con CuyBrowser.
- [SwiftPM y apps dentro del sandbox](sandbox.md): cómo compila SwiftPM bajo el sandbox.

## Referencia

- [Qué incluye](que-incluye.md): los módulos del motor.
- [Integrarlo en otra aplicación](integrar.md): lo que cambia una app que lo integra.
- [Pruebas](pruebas.md): pytest, `swift test` y el estilo.

Otras páginas fuera de `guia/`: el [uso diario y el protocolo `--stdio`](../USO.md),
las [reglas de estilo](../ESTILO.md) y las del navegador:
[NAVEGADOR-ACTIVIDAD.md](../NAVEGADOR-ACTIVIDAD.md),
[NAVEGADOR-EXPORTAR.md](../NAVEGADOR-EXPORTAR.md),
[NAVEGADOR-LECTURAS.md](../NAVEGADOR-LECTURAS.md),
[NAVEGADOR-SITIOS.md](../NAVEGADOR-SITIOS.md) y
[comparar con Chromium](../navegador-comparar.md).
