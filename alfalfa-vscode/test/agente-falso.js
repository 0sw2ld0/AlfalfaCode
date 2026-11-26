#!/usr/bin/env node
'use strict';
// Un agente PACAI mínimo para las pruebas: saluda, abre un turno por input, pide un permiso y espera respuesta.
const { createInterface } = require('node:readline');
let seq = 0, sesion = null, turno = null, peticion = null, n = 0;
const id = () => 'm_' + String(++n).padStart(16, '0');
const enviar = (type, payload, reply_to) => {
    const m = { pacai: '0.3', id: id(), type, session: sesion, sender: 'agent', seq: ++seq,
        time: new Date().toISOString(), payload, ...(reply_to && { reply_to }) };
    process.stdout.write(JSON.stringify(m) + '\n');
    return m.id;
};
const argumentos = process.argv.slice(2);
const terminar = (outcome) => {
    enviar('diff_updated', { run_id: turno, files: [{ path: 'a.txt', added: 1, removed: 0 }], patch: '+adios' });
    enviar('run_finished', { run_id: turno, outcome });
    turno = null;
};
const lector = createInterface({ input: process.stdin });
lector.on('line', linea => {
    const m = JSON.parse(linea);
    if (m.type === 'hello') {
        sesion = m.session;
        enviar('hello', { versions: ['0.3'], profiles: ['work'], extensions: ['alfalfa'] });
        enviar('session_started', { version: '0.3', profiles: m.payload.profiles, extensions: m.payload.extensions }, m.id);
        enviar('alfalfa.session', { id: 'falsa', model: 'falso', mode: 'manual', argv: argumentos, cwd: process.cwd() });
    } else if (m.type === 'input') {
        turno = enviar('run_started', { input_id: m.id });
        const msj = enviar('message_start', { run_id: turno, role: 'assistant' });
        enviar('message_delta', { message_id: msj, blocks: [{ type: 'text', text: 'Eco: ' + m.payload.blocks[0].text }] });
        enviar('message_end', { message_id: msj });
        const tc = enviar('tool_start', { run_id: turno, name: 'edit_file', arguments: { path: 'a.txt' }, side: 'agent', title: 'Editar a.txt' });
        peticion = { id: enviar('approval_request', { run_id: turno, tool_call_id: tc, name: 'edit_file', arguments: { path: 'a.txt' },
            digest: 'sha256:' + '0'.repeat(64), reason: 'alfalfa.edit', scopes: ['once', 'session'], alfalfa: { diff: '+adios', purpose: 'Editar a.txt' } }), tc };
    } else if (m.type === 'approval_result' && peticion && m.reply_to === peticion.id) {
        enviar('tool_end', { tool_call_id: peticion.tc, status: m.payload.decision === 'approved' ? 'success' : 'error',
            summary: m.payload.decision + '/' + (m.payload.scope ?? '-') });
        peticion = null;
        terminar('success');
    } else if (m.type === 'interrupt' && turno) {
        if (peticion) {
            enviar('approval_result', { decision: 'cancelled' }, peticion.id);
            enviar('tool_end', { tool_call_id: peticion.tc, status: 'cancelled' });
            peticion = null;
        }
        terminar('interrupted');
    } else if (m.type === 'alfalfa.set_settings') {
        enviar('alfalfa.session', { id: 'falsa', model: m.payload.model ?? 'falso', mode: m.payload.mode ?? 'manual' });
    } else if (m.type === 'alfalfa.set_rich_view') {
        enviar('alfalfa.session', { id: 'falsa', rich_view: m.payload.active });
    } else if (m.type === 'close') {
        enviar('close', {});
        process.exit(0);
    }
});
lector.on('close', () => process.exit(0));
