"""Los nombres de la extensión `alfalfa.*` en inglés y en `snake_case`, como los del núcleo de PACAI (§8).

Los eventos de `--stdio` v2 tienen sus campos en castellano (`texto`, `estado`, `coste_sesion`…). Dentro de la
extensión van con su nombre inglés (`text`, `status`, `session_cost`…), y las órdenes del cliente (`alfalfa.set_budget`
con `session` y `month`…) se traducen de vuelta antes de llegar al motor. Sólo cambian las **claves**: los valores
(estados, textos, modos) son los de AlfalfaCode. v2 no se toca.

`en_ingles` sólo renombra las claves que conoce, y nunca pisa una clave que ya esté en el mismo objeto (algunas
filas llevan `model` y `modelo`): lo que no está en la tabla sale tal cual.
"""
from __future__ import annotations

from typing import Any

__all__ = ['CAMPOS', 'TIPOS', 'en_ingles', 'de_la_orden']

# Tipo de evento v2 → tipo de la extensión (`alfalfa.<nombre>`).
TIPOS = {
    'sesion': 'session', 'presupuesto': 'budget', 'procesos': 'processes', 'despierto': 'awake', 'cuota': 'quota',
    'preparando': 'preparing', 'revision_codigo': 'code_review', 'encargos': 'jobs',
    'aprendizaje_propuesto': 'learning_proposed', 'aprendizaje_resuelto': 'learning_resolved',
    'quitado_de_cola': 'dequeued', 'sesion_cambiada': 'session_changed', 'actualizacion': 'update',
    'historial': 'history', 'turnos': 'turns', 'sesiones': 'sessions', 'modelos': 'models', 'reglas': 'rules',
    'importadas': 'imported', 'complementos': 'plugins', 'indice_sesiones': 'session_index',
    'busqueda_sesiones': 'session_search', 'sesiones_alrededor': 'sessions_around', 'navegador_sitios': 'browser_sites',
    'chats_ollama': 'ollama_chats', 'deshacer_vista': 'undo_preview', 'deshecho': 'undone', 'aprendido': 'learned',
    'habilidad': 'skill', 'memoria': 'memory', 'pendiente': 'pending_learning', 'volver': 'reverted',
}

# Campo v2 → campo de la extensión. Los que ya están en inglés (`argv`, `cwd`, `diff`, `id`…) no hacen falta.
CAMPOS = {
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
    'indice_al_dia': 'index_up_to_date', 'informe': 'report', 'inicio': 'start', 'instalacion': 'installation',
    'instalada': 'installed', 'instrucciones': 'instructions', 'instrucciones_mcp': 'mcp_instructions',
    'intento': 'attempt', 'irreversibles': 'irreversible', 'justificacion': 'justification', 'limite': 'limit',
    'linea': 'line', 'llamada': 'call', 'lugar': 'location', 'manifiesto': 'manifest', 'marcas': 'marks',
    'memoria': 'memory', 'mensaje': 'message', 'mensajes': 'messages', 'mensajes_antes': 'messages_before',
    'mes': 'month', 'modelo': 'model', 'modelo_revision': 'review_model', 'modelos': 'models', 'modo': 'mode',
    'motivo': 'reason', 'motivos': 'reasons', 'no_cubiertos': 'not_covered', 'nombre': 'name',
    'nombre_proveedor': 'provider_name', 'nota': 'note', 'nueva_conversacion': 'new_conversation',
    'objetivo': 'target', 'objetivos': 'targets', 'omitidos': 'skipped', 'orden': 'command', 'ordenes': 'commands',
    'origen': 'origin', 'papelera': 'trash', 'para_sesion': 'for_session', 'paralelos': 'parallel', 'paso': 'step',
    'pasos': 'steps', 'patron': 'pattern', 'pendiente': 'pending', 'pendientes': 'pending',
    'permisos': 'permissions', 'permitidos': 'allowed', 'podadas': 'pruned', 'politica': 'policy',
    'posicion': 'position', 'precio': 'price', 'prefijo': 'prefix', 'primer_turno': 'first_turn',
    'prioridad': 'priority', 'procedencia': 'provenance', 'procesos': 'processes', 'proposito': 'purpose',
    'proveedor': 'provider', 'proyecto': 'project', 'quitado': 'removed', 'raiz': 'root', 'rama': 'branch',
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


def _renombrar(valor: Any, tabla: dict[str, str]) -> Any:
    if isinstance(valor, list):
        return [_renombrar(v, tabla) for v in valor]
    if not isinstance(valor, dict):
        return valor
    salida: dict = {}
    for clave, hijo in valor.items():
        nueva = tabla.get(clave, clave) if isinstance(clave, str) else clave
        if nueva != clave and (nueva in valor or nueva in salida):
            nueva = clave                      # no se pisa una clave que ya existe
        salida[nueva] = _renombrar(hijo, tabla)
    return salida


def en_ingles(valor: Any) -> Any:
    """Una copia con las claves de AlfalfaCode en inglés, a cualquier profundidad."""
    return _renombrar(valor, CAMPOS)


def de_la_orden(valor: Any) -> Any:
    """Los campos de una orden `alfalfa.*` del cliente con sus nombres v2."""
    return _renombrar(valor, ORDEN)
