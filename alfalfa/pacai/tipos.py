"""Tablas del catálogo de mensajes de PACAI 0.2 (§4).

Derivado del SDK de Python de PACAI (Apache-2.0); ver NOTICE y LICENSE en esta carpeta.
"""
VERSION = '0.2'

# Quién puede enviar cada tipo del núcleo y de los perfiles; None = ambos.
SENDER_OF = {
    'hello': None, 'session_started': 'agent', 'input': 'client', 'run_started': 'agent',
    'run_finished': 'agent', 'message_start': 'agent', 'message_delta': 'agent', 'message_end': 'agent',
    'tool_start': 'agent', 'tool_end': None, 'approval_request': 'agent', 'approval_result': None,
    'interrupt': 'client', 'usage': 'agent', 'heartbeat': None, 'error': None, 'close': None,
    'snapshot': 'agent', 'voice_turn': None, 'state_snapshot': None, 'state_delta': None,
    'plan_updated': 'agent', 'diff_updated': 'agent', 'expression': 'agent',
}
MESSAGE_TYPES = frozenset(SENDER_OF)
PROFILE_OF = {'voice_turn': 'voice', 'state_snapshot': 'shared_state', 'state_delta': 'shared_state',
              'plan_updated': 'work', 'diff_updated': 'work', 'expression': 'expression'}
# Mensajes del agente cuyo payload.run_id debe nombrar una ejecución activa (§4.2).
RUN_SCOPED = frozenset({'message_start', 'tool_start', 'approval_request', 'usage', 'plan_updated',
                        'diff_updated', 'expression'})
# Tipos que se pueden enviar antes de `session_started` (§4.1).
ANTES_DE_EMPEZAR = frozenset({'hello', 'session_started', 'error', 'close', 'heartbeat'})
