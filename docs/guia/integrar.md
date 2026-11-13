# Integrarlo en otra aplicación

El arnés no importa nada de quien lo integra. Lo que esa aplicación necesita
cambiar está en `alfalfa/ajustes.py`: instrucciones por defecto, dónde buscar los
binarios, cómo llegar al buscador web, qué repositorio da por propio y las URL
y claves de los proveedores (`ollama_url`, `ollama_clave`, `openai_clave`, `anthropic_clave`,
`clave_compatible`, `alfalfa_servicio_url`).
También cómo se nombran el runtime y el integrador en los textos (`nombres`), a
qué ramas puede avanzar un agente con `git merge --ff-only` (`ramas_de_avance`) y
qué órdenes propias de su proyecto da por seguras (`orden_permitida`). La
aplicación los registra al cargarse.
