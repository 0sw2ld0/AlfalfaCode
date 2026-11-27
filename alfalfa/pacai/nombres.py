"""Los nombres de la extensión `alfalfa.*` en inglés y en `snake_case`, como los del núcleo de PACAI (§8).

Los eventos de `--stdio` v2 tienen sus campos y sus valores en castellano (`estado: "trabajando"`,
`modo: "ediciones"`…). Dentro de `--pacai` van en inglés:

- **Claves** (`CAMPOS`): `texto`→`text`, `estado`→`status`, `coste_sesion`→`session_cost`…
- **Valores enumerados** (`VALORES`, por clave inglesa): `trabajando`→`working`, `ediciones`→`accept_edits`,
  `sin_vision`→`no_vision`… Sólo lo que una máquina compara; los textos para personas (mensajes, resúmenes,
  errores) no se traducen, ni los nombres propios (herramientas, modelos, tipos de subagente, ids).
- **Órdenes del cliente** (`ORDEN`, `ORDEN_VALORES`): sus campos y valores vuelven a v2 antes de llegar al motor;
  un valor enumerado que no está en la tabla es un error (`ValueError`), no se pasa tal cual.
- **La herramienta del cliente `browser`** (`NAVEGADOR_*`): los campos y valores de la petición `navegador` de v2
  (`accion: "abrir"`…) y de su respuesta (`ok`, `resultado`, `permiso`…), en los dos sentidos.

`en_ingles` sólo cambia lo que conoce y nunca pisa una clave que ya esté en el mismo objeto (algunas filas llevan
`model` y `modelo`): lo que no está en la tabla sale tal cual. v2 no se toca.
"""
from __future__ import annotations

from typing import Any

__all__ = ['CAMPOS', 'TIPOS', 'VALORES', 'YA_EN_INGLES', 'ORDEN_VALORES', 'en_ingles', 'de_la_orden',
           'razon', 'peticion_del_navegador', 'respuesta_del_navegador']

# Tipo de evento v2 → tipo de la extensión (`alfalfa.<nombre>`).
TIPOS = {
    'proveedor_imagen': 'image_provider',
    'sesion': 'session', 'presupuesto': 'budget', 'procesos': 'processes', 'despierto': 'awake', 'cuota': 'quota',
    'preparando': 'preparing', 'revision_codigo': 'code_review', 'encargos': 'jobs',
    'aprendizaje_propuesto': 'learning_proposed', 'aprendizaje_resuelto': 'learning_resolved',
    'quitado_de_cola': 'dequeued', 'sesion_cambiada': 'session_changed', 'actualizacion': 'update',
    'historial': 'history', 'turnos': 'turns', 'sesiones': 'sessions', 'modelos': 'models', 'reglas': 'rules',
    'importadas': 'imported', 'complementos': 'plugins', 'indice_sesiones': 'session_index',
    'busqueda_sesiones': 'session_search', 'sesiones_alrededor': 'sessions_around', 'navegador_sitios': 'browser_sites',
    'chats_ollama': 'ollama_chats', 'deshacer_vista': 'undo_preview', 'deshecho': 'undone', 'aprendido': 'learned',
    'habilidad': 'skill', 'memoria': 'memory', 'pendiente': 'pending_learning', 'volver': 'reverted',
    'espacio_sesiones': 'session_storage', 'sesiones_liberadas': 'sessions_freed',
}

# Campo v2 → campo de la extensión. Los que ya están en inglés (`argv`, `cwd`, `diff`, `id`…) no hacen falta.
CAMPOS = {
    'preferencia': 'preference',
    'vista_enriquecida': 'rich_view',
    'lluvia_limite': 'brainstorm_limit', 'lluvia_participantes': 'brainstorm_participants',
    'modelo_codex_imagen': 'image_codex_model', 'esfuerzo_codex_imagen': 'image_codex_effort',
    # sesiones/espacio.py (--espacio-sesiones y --liberar-sesiones)
    'movidas': 'moved', 'saltadas': 'skipped_sessions', 'liberado': 'freed', 'totales': 'totals',
    'imagenes': 'images', 'en_uso': 'in_use', 'ultimo_uso': 'last_used', 'dias_sin_usar': 'days_unused',
    'vista_previa': 'preview', 'instantaneas': 'snapshots', 'indice': 'index',
    'accion': 'action', 'actividad': 'activity', 'activo': 'active', 'actualizada': 'updated',
    'actualizado': 'updated', 'agente': 'agent', 'agotado': 'exhausted', 'ajustes': 'settings', 'alcance': 'scope',
    'almacen': 'store', 'almacenes': 'stores', 'ambito': 'area', 'ancla': 'anchor', 'antes': 'before',
    'apagado_en': 'disabled_in', 'aplicado': 'applied', 'aplicar': 'apply', 'app_mac_cambio': 'mac_app_changed',
    'aprende_aqui': 'learns_here', 'aprobado': 'approved', 'archivada': 'archived', 'archivo': 'file',
    'archivos': 'files', 'argumentos': 'arguments', 'autor': 'author', 'autorizable': 'authorizable',
    'aviso': 'warning', 'avisos': 'warnings', 'base_git': 'git_base', 'bifurcada_de': 'forked_from',
    'bloqueada': 'blocked', 'bloqueados': 'blocked', 'borrado': 'deleted', 'buscado': 'matched',
    'cache': 'cache', 'capacidad': 'capacity', 'capacidades': 'capabilities', 'carpeta': 'folder',
    'cierre': 'closing', 'clase': 'kind', 'clasificacion': 'classification', 'clave': 'key', 'codigo': 'code',
    'codigo_salida': 'exit_code', 'coincidencias': 'matches', 'cola': 'queue', 'comandos': 'commands',
    'compactacion': 'compaction', 'compactar_en': 'compact_at', 'complemento': 'plugin', 'complementos': 'plugins',
    'composicion': 'composition', 'con_vision': 'with_vision', 'configuracion': 'configuration',
    'conflictos': 'conflicts', 'conservados': 'kept', 'consulta': 'query', 'contexto': 'context',
    'continuada_de': 'continued_from', 'corta': 'short', 'coste': 'cost', 'coste_estimado': 'estimated_cost',
    'coste_sesion': 'session_cost', 'coste_turno': 'turn_cost', 'creada': 'created', 'cuotas': 'quotas',
    'de': 'from', 'de_esta_carpeta': 'from_this_folder', 'definicion': 'definition', 'descartada': 'discarded',
    'descripcion': 'description', 'desde': 'since', 'desglose': 'breakdown', 'despues': 'after',
    'detalle': 'detail', 'detenido': 'stopped', 'diagnosticos': 'diagnostics', 'disponible': 'available',
    'donde': 'where', 'ejecucion': 'execution', 'elementos': 'items', 'eliminada': 'deleted', 'en_curso': 'ongoing',
    'encargos': 'jobs', 'entrada': 'input', 'entradas': 'entries', 'errores': 'errors', 'esfuerzo': 'effort',
    'esfuerzos': 'efforts', 'esquema': 'schema', 'estado': 'status', 'estimado': 'estimated', 'estricto': 'strict',
    'etiqueta': 'label', 'eventos': 'events', 'explicacion': 'explanation', 'fallo_sandbox': 'sandbox_failure',
    'fallos': 'failures', 'falta_clave': 'missing_key', 'faltan': 'remaining', 'fase': 'phase', 'fecha': 'date',
    'fijada': 'pinned', 'fin': 'end', 'formato': 'format', 'fragmento': 'snippet', 'gastado': 'spent',
    'grandes': 'large', 'gratis': 'free', 'grave': 'severe', 'habilidad': 'skill', 'habilidades': 'skills',
    'hallazgos': 'findings', 'hasta': 'until', 'hay_nueva': 'update_available', 'herramienta': 'tool',
    'herramientas': 'tools', 'herramientas_mcp': 'mcp_tools', 'herramientas_mcp_base': 'mcp_tools_base',
    'herramientas_mcp_base_bytes': 'mcp_tools_base_bytes', 'herramientas_mcp_bytes': 'mcp_tools_bytes',
    'herramientas_mcp_cargadas': 'mcp_tools_loaded', 'herramientas_mcp_diferidas': 'mcp_tools_deferred',
    'herramientas_mcp_diferidas_tokens': 'mcp_tools_deferred_tokens', 'herramientas_mcp_exacto': 'mcp_tools_exact',
    'herramientas_mcp_lista': 'mcp_tools_list', 'herramientas_mcp_metodo': 'mcp_tools_method', 'hora': 'time',
    'huella': 'fingerprint', 'identidad': 'identity', 'ignoradas': 'ignored', 'importada_de': 'imported_from',
    'importadas': 'imported',
    'indice_al_dia': 'index_up_to_date', 'informe': 'report', 'inicio': 'start', 'instalacion': 'installation',
    'instalada': 'installed', 'instrucciones': 'instructions', 'instrucciones_mcp': 'mcp_instructions',
    'intento': 'attempt', 'irreversibles': 'irreversible', 'justificacion': 'justification', 'limite': 'limit',
    'limpieza': 'cleanup', 'linea': 'line', 'llamada': 'call', 'lugar': 'location', 'manifiesto': 'manifest',
    'marcas': 'marks',
    'memoria': 'memory', 'mensaje': 'message', 'mensajes': 'messages', 'mensajes_antes': 'messages_before',
    'mes': 'month', 'modelo': 'model', 'modelo_revision': 'review_model', 'modelos': 'models', 'modo': 'mode',
    'motivo': 'reason', 'motivos': 'reasons', 'no_cubiertos': 'not_covered', 'nombre': 'name',
    'nombre_proveedor': 'provider_name', 'nota': 'note', 'nueva_conversacion': 'new_conversation',
    'objetivo': 'target', 'objetivos': 'targets', 'omitidos': 'skipped', 'orden': 'command', 'ordenes': 'commands',
    'origen': 'origin', 'papelera': 'trash', 'para_sesion': 'for_session', 'paralelos': 'parallel', 'paso': 'step',
    'pasos': 'steps', 'patron': 'pattern', 'pendiente': 'pending', 'pendientes': 'pending',
    'permisos': 'permissions', 'permitidos': 'allowed', 'podadas': 'pruned', 'politica': 'policy',
    'posicion': 'position', 'precio': 'price', 'prefijo': 'prefix', 'prefijo_alcance': 'prefix_scope',
    'primer_turno': 'first_turn',
    'prioridad': 'priority', 'procedencia': 'provenance', 'procesos': 'processes', 'proposito': 'purpose',
    'proveedor': 'provider', 'proyecto': 'project', 'quitado': 'removed', 'raiz': 'root', 'rama': 'branch',
    'rama_base': 'base_branch',
    'razona': 'reasoning', 'rechazado': 'rejected', 'recortado': 'truncated', 'red': 'network',
    'referencia': 'reference', 'regla': 'rule', 'reintento_de': 'retry_of', 'renueva': 'renews',
    'reserva_maxima': 'max_reserve', 'restaurables': 'restorable', 'restaurados': 'restored',
    'resultado': 'result', 'resultados': 'results', 'resumen': 'summary', 'revision': 'review', 'rol': 'role',
    'ruta': 'path', 'salida': 'output', 'salida_max': 'max_output', 'servicio': 'service', 'servidor': 'server',
    'servidores': 'servers', 'servidores_mcp': 'mcp_servers', 'sesion': 'session', 'sesiones': 'sessions',
    'siempre': 'always', 'sin_sandbox': 'unsandboxed', 'sistema': 'system', 'sitio': 'site', 'sitios': 'sites',
    'subagentes': 'subagents', 'tarifa': 'pricing', 'texto': 'text', 'tiempo': 'duration', 'tipo': 'type',
    'titulo': 'title', 'tokens_contados': 'counted_tokens', 'tokens_sesion': 'session_tokens',
    'tokens_turno': 'turn_tokens', 'tope': 'cap', 'transporte': 'transport', 'turno': 'turn', 'turnos': 'turns',
    'ultima': 'latest', 'ultimas': 'tail', 'umbral': 'threshold', 'usado_pct': 'used_pct', 'usados': 'used',
    'ventana': 'window', 'version_disponible': 'available_version', 'visita': 'visit', 'visita_id': 'visit_id',
    'visita_sitio': 'visit_site', 'vista': 'view', 'vivo': 'alive',
}

# Lo que el cliente manda en las órdenes `alfalfa.*` → el campo v2 que espera el motor. Es la inversa de `CAMPOS`
# para los campos de esas órdenes (`set_settings`, `set_budget`, `jobs_command`…), sin ambigüedad.
ORDEN = {ingles: castellano for castellano, ingles in CAMPOS.items()
         if castellano in ('modelo', 'esfuerzo', 'modo', 'instrucciones', 'activo', 'sesion', 'mes', 'aplicar',
                           'turno', 'ruta', 'accion', 'encargos', 'paralelos')}


# Valores enumerados de AlfalfaCode → inglés, por la clave (ya inglesa) que los lleva. Se aplica a un texto o a
# cada texto de una lista. Un valor que no está aquí sale igual: o ya está en inglés (`YA_EN_INGLES`) o es un nombre.
_MODOS = {'auto': 'auto', 'manual': 'manual', 'ediciones': 'accept_edits', 'plan': 'plan', 'turbo': 'turbo',
          'omitir': 'bypass_permissions',                   # permisos/modos.py
          'ahorro': 'saving', 'completo': 'full',            # compactacion.MODOS_CONTEXTO
          'serie': 'serial', 'paralelo': 'parallel',         # encargos.validar
          'texto': 'text', 'arbol': 'tree',                  # la lectura del navegador
          'ollama': 'ollama', 'searxng': 'searxng', 'apagado': 'off'}   # buscador web
_MOTIVOS = {'peligroso': 'dangerous', 'red': 'network', 'salir_sandbox': 'outside_sandbox',
            'reintento': 'sandbox_retry', 'regla': 'rule', 'modo': 'mode', 'edicion': 'edit',
            'navegador': 'browser', 'contenido_externo': 'external_content', 'aprendizaje': 'learning',
            'generar_imagen': 'image_generation', 'app': 'app', 'mcp': 'mcp', 'diagnosticos_swift': 'swift_diagnostics',
            'delegacion_antigravity': 'antigravity_delegation',
            'sin_vision': 'no_vision', 'desconocido': 'unknown', 'entregado': 'delivered',
            'continuacion': 'continuation', 'detenida': 'stopped', 'ajeno': 'not_by_agent',
            'editado_despues': 'edited_later', 'umbral': 'threshold', 'auto': 'auto', 'manual': 'manual',
            'solo_lectura': 'read_only', 'en_uso': 'in_use', 'no_existe': 'not_found'}
_ALCANCES = {'usuario': 'user', 'proyecto': 'project', 'carpeta': 'folder', 'todas': 'all', 'codigo': 'code',
             'sesion': 'session', 'entorno': 'environment'}
VALORES: dict[str, dict[str, str]] = {
    'status': {
        # el turno que cortó «Enviar ahora» (stdio.run): `run_finished` lo cuenta como `interrupted`
        'cortado': 'cut',
        # subagentes.py
        'trabajando': 'working', 'iniciando': 'starting', 'terminado': 'completed', 'fallido': 'failed',
        'cerrado': 'closed', 'interrumpido': 'interrupted', 'interrumpida': 'interrupted',
        # encargos.py (la cola y cada encargo)
        'lista': 'ready', 'ejecutando': 'running', 'pausando': 'pausing', 'pausada': 'paused',
        'completada': 'completed', 'cancelada': 'cancelled', 'cancelado': 'cancelled', 'pendiente': 'pending',
        # encargos_worktrees.py (la limpieza de cada worktree)
        'quitado': 'removed', 'conservado': 'kept', 'rama_conservada': 'branch_kept',
        # revision_codigo.py
        'disponible': 'available', 'revisando': 'reviewing', 'terminada': 'completed', 'fallida': 'failed',
        'sin_presupuesto': 'no_budget', 'sin_cierre': 'no_closing', 'sin_modelo_barato': 'no_cheap_model',
        'sin_senales': 'no_signals', 'tope_de_sesion': 'session_limit', 'no_escribe': 'no_writes', 'apagada': 'off',
        # compactación, aprendizaje, importadas, complementos, navegador…
        'en_curso': 'in_progress', 'en_marcha': 'running', 'hecha': 'done', 'al_dia': 'up_to_date',
        'activo': 'active', 'activa': 'active', 'inactiva': 'inactive', 'autorizado': 'authorized',
        'desactivado': 'disabled', 'desactivada': 'disabled', 'invalido': 'invalid', 'aligerada': 'trimmed',
        'ambito_cambiado': 'scope_changed', 'cambiada': 'changed', 'cambio': 'changed', 'completa': 'complete',
        'incompleta': 'incomplete', 'corrupta': 'corrupt', 'cuota_desconocida': 'unknown_quota',
        'falta': 'missing', 'ilegible': 'unreadable', 'sin_alcance': 'no_scope', 'no_disponible': 'unavailable',
        'cursor_caducado': 'cursor_expired', 'duplicado': 'duplicate',
    },
    'mode': _MODOS, 'permissions': _MODOS, 'context': _MODOS,
    'reason': _MOTIVOS, 'reasons': _MOTIVOS,
    'warning': {'sesion': 'session', 'mes': 'month'}, 'exhausted': {'sesion': 'session', 'mes': 'month'},
    'kind': {'memoria': 'memory', 'habilidad': 'skill'},
    'store': _ALCANCES, 'scope': _ALCANCES, 'area': _ALCANCES,
    'origin': {'agente': 'agent', 'aprender': 'learning', 'externo': 'external', 'integrado': 'builtin',
               'paquete': 'package', 'proyecto': 'project', 'red': 'network', 'revision': 'review',
               'sin_identificar': 'unidentified', 'usuario': 'user', 'cache': 'cache', 'ollama': 'ollama'},
    'action': {'crear': 'create', 'restaurar': 'restore', 'borrar': 'delete', 'parchear': 'patch',
               'permisos': 'permissions', 'add': 'add', 'remove': 'remove', 'preparar': 'prepare',
               'iniciar': 'start', 'pausar': 'pause', 'detener': 'stop', 'cancelar': 'cancel', 'limpiar': 'clean',
               'cancelar_relevo': 'cancel_handoff'},
    'step': {'deshacer': 'undo', 'rehacer': 'redo'},
    'phase': {'inicio': 'start', 'fin': 'end'},
    'decision': {'permitida': 'allowed', 'preguntar': 'ask', 'prohibida': 'forbidden'},
    # Lo que concede «No volver a preguntar» (permisos/bajo_demanda.py, ALCANCE_*).
    'prefix_scope': {'sin_sandbox': 'outside_sandbox', 'red': 'network', 'sandbox': 'sandbox'},
    'installation': {'paquete': 'package'},
    'capacity': {'roto': 'broken'},
    'result': {'correcta': 'succeeded', 'cancelada': 'cancelled', 'fallida': 'failed'},
    'type': {'agente': 'agent', 'comando': 'command', 'instrucciones': 'instructions', 'publicacion': 'publication',
             'sitio': 'site', 'formulario': 'form', 'contrasena': 'password', 'descarga': 'download'},
}
# Valores que AlfalfaCode ya emite en inglés (estados del motor, del plan…): no necesitan traducción.
YA_EN_INGLES = frozenset({
    'completed', 'failed', 'blocked', 'interrupted', 'running', 'waiting', 'starting', 'error', 'pending',
    'in_progress', 'pass', 'failure', 'passed', 'auto', 'manual', 'plan', 'add', 'remove', 'mcp', 'app', 'cache',
    'ollama', 'searxng', 'active', 'deprecated', 'retired', 'low', 'medium', 'high', 'xhigh', 'max',
    'commands', 'interface', 'skills',
})

# Órdenes `alfalfa.*` del cliente con un valor enumerado: campo inglés → valor inglés → valor v2. Un valor que no
# está aquí no llega al motor: es un error, como lo era un valor no válido en v2.
_INVERSA_MODOS = {'auto': 'auto', 'manual': 'manual', 'accept_edits': 'ediciones', 'plan': 'plan', 'turbo': 'turbo',
                  'bypass_permissions': 'omitir'}
ORDEN_VALORES: dict[str, dict[str, dict[str, str]]] = {
    'set_settings': {'mode': _INVERSA_MODOS},
    'set_context_mode': {'mode': {'saving': 'ahorro', 'full': 'completo'}},
    'jobs_command': {'mode': {'serial': 'serie', 'parallel': 'paralelo'},
                     'action': {'prepare': 'preparar', 'start': 'iniciar', 'pause': 'pausar', 'stop': 'detener',
                                'cancel': 'cancelar', 'clean': 'limpiar'}},
}

# La herramienta del cliente `browser`: el navegador de la app de v2 (`navegador` / `navegador_respuesta`).
NAVEGADOR_CAMPOS = {
    # la petición (navegador.py: `_pedir`)
    'accion': 'action', 'red_local': 'local_network', 'modo': 'mode', 'limite': 'limit', 'ancho': 'width',
    'alto': 'height', 'objetivo': 'target', 'texto': 'text', 'enviar': 'submit', 'borrar': 'clear',
    'direccion': 'direction', 'cantidad': 'amount', 'ruta': 'path', 'codigo': 'code', 'modifica': 'modifies',
    'motivo': 'reason', 'tiempo': 'timeout', 'url_contiene': 'url_contains', 'carga': 'load',
    'tiempo_ms': 'timeout_ms', 'tecla': 'key', 'rutas': 'paths', 'proyecto': 'project',
    'documento_aprobado': 'approved_document', 'url_aprobada': 'approved_url', 'sitios': 'sites',
    'bloqueados': 'blocked', 'estricto': 'strict', 'permitidos': 'allowed', 'visita': 'visit',
    'visita_sitio': 'visit_site', 'visita_id': 'visit_id', 'permitido': 'granted',
    # la respuesta (navegador.py, navegador_lectura.py…)
    'resultado': 'result', 'permiso': 'permission', 'tipo': 'type', 'iniciada': 'started', 'detalle': 'detail',
    'documento': 'document', 'recortado': 'truncated', 'captura': 'screenshot', 'arbol': 'tree', 'valor': 'value',
    'peticiones': 'requests', 'mensajes': 'messages', 'respuesta': 'response', 'respuesta_error': 'response_error',
    'lectura': 'reading', 'ventana': 'window', 'envio_formulario': 'form_submission', 'elemento': 'element',
    'cumplida': 'met', 'cambios_dom': 'dom_changes', 'visita_consumida': 'consumed_visit',
    'visitas_consumidas': 'consumed_visits', 'titulo': 'title', 'nota': 'note',
}
NAVEGADOR_VALORES: dict[str, dict[str, str]] = {
    'action': {'abrir': 'open', 'leer': 'read', 'consola': 'console', 'red': 'network', 'viewport': 'viewport',
               'pulsar': 'click', 'escribir': 'type', 'desplazar': 'scroll', 'captura': 'screenshot', 'js': 'eval',
               'relevo': 'handoff', 'esperar': 'wait', 'tecla': 'key', 'hover': 'hover', 'estado': 'state',
               'subir': 'upload', 'descargar': 'download', 'cancelar_sitio': 'cancel_site',
               'reanudar_sitio': 'resume_site', 'cancelar_relevo': 'cancel_handoff'},
    'mode': {'texto': 'text', 'arbol': 'tree'},
    'direction': {'abajo': 'down', 'arriba': 'up', 'izquierda': 'left', 'derecha': 'right'},
    'granted': {'formulario': 'form', 'contrasena': 'password', 'descarga': 'download'},
    'type': {'sitio': 'site', 'formulario': 'form', 'contrasena': 'password', 'descarga': 'download'},
}


def _invertir(tabla: dict[str, str]) -> dict[str, str]:
    inversa = {v: k for k, v in tabla.items()}
    assert len(inversa) == len(tabla), 'una traducción del navegador no es reversible'
    return inversa


_NAVEGADOR_CAMPOS_INVERSA = _invertir(NAVEGADOR_CAMPOS)
_NAVEGADOR_VALORES_INVERSA = {clave: _invertir(t) for clave, t in NAVEGADOR_VALORES.items()}


def _traducir(valor: Any, claves: dict[str, str], valores: dict[str, dict[str, str]]) -> Any:
    """Copia de `valor` con las claves de `claves` y, bajo cada clave nueva, los valores de `valores[clave]`."""
    if isinstance(valor, list):
        return [_traducir(v, claves, valores) for v in valor]
    if not isinstance(valor, dict):
        return valor
    salida: dict = {}
    for clave, hijo in valor.items():
        nueva = claves.get(clave, clave) if isinstance(clave, str) else clave
        if nueva != clave and (nueva in valor or nueva in salida):
            nueva = clave                      # no se pisa una clave que ya existe
        tabla = valores.get(nueva)
        if tabla is not None and isinstance(hijo, str):
            salida[nueva] = tabla.get(hijo, hijo)
        elif tabla is not None and isinstance(hijo, list) and all(isinstance(x, str) for x in hijo):
            salida[nueva] = [tabla.get(x, x) for x in hijo]
        else:
            salida[nueva] = _traducir(hijo, claves, valores)
    return salida


def en_ingles(valor: Any) -> Any:
    """Una copia con las claves y los valores enumerados de AlfalfaCode en inglés, a cualquier profundidad."""
    return _traducir(valor, CAMPOS, VALORES)


def razon(motivo: str) -> str:
    """El motivo de un permiso de AlfalfaCode en inglés (`edicion`→`edit`); uno desconocido, tal cual."""
    return _MOTIVOS.get(motivo, motivo)


def de_la_orden(valor: Any, orden: str | None = None) -> Any:
    """Los campos de una orden `alfalfa.<orden>` del cliente con sus nombres y valores v2. Un valor enumerado que
    no está en la tabla de esa orden es un `ValueError` que lo dice."""
    salida = _traducir(valor, ORDEN, {})
    for campo, tabla in ORDEN_VALORES.get(orden or '', {}).items():
        castellano = ORDEN.get(campo, campo)
        if castellano not in salida:
            continue
        recibido = salida[castellano]
        if recibido not in tabla:
            raise ValueError('Valor desconocido para ' + campo + ': ' + repr(recibido)[:60] + '. Valores: '
                             + ', '.join(sorted(tabla)) + '.')
        salida[castellano] = tabla[recibido]
    return salida


def peticion_del_navegador(campos: dict) -> dict:
    """Los `arguments` de la herramienta del cliente `browser` a partir de la petición `navegador` de v2."""
    return _traducir(campos, NAVEGADOR_CAMPOS, NAVEGADOR_VALORES)


def respuesta_del_navegador(datos: Any) -> Any:
    """La respuesta `structured` del cliente a `browser`, con los nombres y valores que espera el motor (v2)."""
    claves = _NAVEGADOR_CAMPOS_INVERSA
    salida = _traducir(datos, claves, {})
    return _valores_de_vuelta(salida)


def _valores_de_vuelta(valor: Any) -> Any:
    """En la respuesta ya con claves v2, los valores enumerados del navegador vuelven a v2 (`site`→`sitio`)."""
    if isinstance(valor, list):
        return [_valores_de_vuelta(v) for v in valor]
    if not isinstance(valor, dict):
        return valor
    salida = {}
    for clave, hijo in valor.items():
        tabla = _NAVEGADOR_VALORES_INVERSA.get(NAVEGADOR_CAMPOS.get(clave, clave))
        if tabla is not None and isinstance(hijo, str):
            salida[clave] = tabla.get(hijo, hijo)
        elif tabla is not None and isinstance(hijo, list) and all(isinstance(x, str) for x in hijo):
            salida[clave] = [tabla.get(x, x) for x in hijo]
        else:
            salida[clave] = _valores_de_vuelta(hijo)
    return salida
