# Subagentes

Como `spawn_agent` de Codex y la herramienta Agent de Claude Code, el agente puede
lanzar otros agentes con una tarea acotada. Cada uno tiene su propia conversación y
sus herramientas, trabaja en la misma carpeta y pide los permisos con la misma
tarjeta, que dice qué subagente pide.

- `spawn_agent` lo lanza. Sin `background` espera su informe final y lo devuelve,
  como Claude Code; con `background: true` devuelve su id al momento y varios
  trabajan a la vez, como Codex. `fork_context` le pasa la conversación hasta ahí.
- `wait_agent` recoge sus informes, `send_input` les manda otro mensaje (o una
  tarea nueva si ya terminaron, con lo que aprendieron), `close_agent` los cierra
  y `list_agents` dice cómo van.
- Tipos: `general` (las herramientas del modo actual), `explorador` (sólo lee) y
  los tuyos en `.alfalfa/agents/*.md`, `.claude/agents/*.md` del proyecto,
  `~/.alfalfa/agents` o `~/.claude/agents`, con el formato de Claude Code
  (`name`, `description`, `tools`, `model` en la cabecera e instrucciones debajo).
  También los de OpenCode que autorices (ver [Venir de OpenCode, Codex o Claude Code](migrar.md)).
  `tools: Read, Grep` se traduce a `read_file` y `search`; un modelo de Claude
  (`sonnet`, `inherit`…) usa el de la sesión.
- Como en Codex, hay 6 trabajando como mucho y un subagente no lanza otros. En
  modo Plan también sólo leen.
- Si el agente termina su respuesta con subagentes trabajando, el turno espera a
  que acaben y le pasa sus informes, como las notificaciones de Claude Code. Al
  detener el turno se detienen con él.

La app los enseña en el panel «Subagentes» (tipo, modelo, estado, lo que hacen y
sus pasos) y en el pie («2 subagentes trabajando»); sus pasos no se mezclan con la
conversación principal.
