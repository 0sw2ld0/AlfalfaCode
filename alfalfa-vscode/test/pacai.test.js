'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { once } = require('node:events');
const { ClientePacai } = require('../lib/pacai');

const FALSO = path.join(__dirname, 'agente-falso.js');

function cliente(opciones = {}) {
    const c = new ClientePacai({ ejecutable: FALSO, cwd: __dirname, ...opciones });
    const recibidos = [];
    c.on('mensaje', m => recibidos.push(m));
    c.on('fallo', e => { throw e; });
    return { c, recibidos };
}

function esperar(c, tipo, cond = () => true) {
    return new Promise(resolver => {
        const f = m => { if (m.type === tipo && cond(m)) { c.off('mensaje', f); resolver(m); } };
        c.on('mensaje', f);
    });
}

test('negocia, pasa las opciones como argumentos y abre un turno', async () => {
    const { c } = cliente({ modo: 'accept_edits', modelo: 'qwen3:8b', esfuerzo: 'high' });
    const sesion = esperar(c, 'alfalfa.session');
    const peticion = esperar(c, 'approval_request');
    c.enviarTexto('hola');
    const s = await sesion;
    assert.deepEqual(s.payload.argv, ['--pacai', '--modo', 'accept_edits', '--modelo', 'qwen3:8b', '--esfuerzo', 'high']);
    assert.equal(c.iniciada, true);
    await peticion;
    assert.equal(c.ocupado, true);
    c.cerrar();
    await once(c, 'salida');
});

test('aprobar en la sesión cierra la herramienta y el turno', async () => {
    const { c, recibidos } = cliente();
    const peticion = esperar(c, 'approval_request');
    c.enviarTexto('edita');
    const p = await peticion;
    const fin = esperar(c, 'run_finished');
    c.aprobar(p.id, 'session');
    assert.equal((await fin).payload.outcome, 'success');
    assert.equal(recibidos.find(m => m.type === 'tool_end').payload.summary, 'approved/session');
    assert.equal(c.ocupado, false);
    c.cerrar();
    await once(c, 'salida');
});

test('interrumpir cancela la aprobación pendiente', async () => {
    const { c, recibidos } = cliente();
    const peticion = esperar(c, 'approval_request');
    c.enviarTexto('edita');
    await peticion;
    const fin = esperar(c, 'run_finished');
    assert.ok(c.interrumpir());
    assert.equal((await fin).payload.outcome, 'interrupted');
    assert.equal(recibidos.find(m => m.type === 'approval_result').payload.decision, 'cancelled');
    assert.equal(c.interrumpir(), null);
    c.cerrar();
    await once(c, 'salida');
});

test('el steer lleva el run_id del turno en curso', async () => {
    const { c } = cliente();
    const enviados = [];
    c.on('enviado', m => enviados.push(m));
    const peticion = esperar(c, 'approval_request');
    c.enviarTexto('edita');
    await peticion;
    c.enviarTexto('y también b.txt', { encauzar: true });
    const steer = enviados.at(-1);
    assert.equal(steer.payload.mode, 'steer');
    assert.equal(steer.payload.run_id, c.raiz);
    assert.deepEqual(steer.payload.alfalfa, { redirect: false });
    c.cerrar();
    await once(c, 'salida');
});

test('set_settings pasa cualquier modo con forma de id (los valida el motor)', async () => {
    const { c } = cliente();
    const sesion = esperar(c, 'alfalfa.session', m => m.payload.mode === 'turbo');
    c.iniciar();
    assert.throws(() => c.ajustes({ modo: 'Modo Raro!' }), /Modo no válido/);
    c.ajustes({ modo: 'turbo' });
    assert.equal((await sesion).payload.mode, 'turbo');
    c.cerrar();
    await once(c, 'salida');
});

test('la vista enriquecida se manda con alfalfa.set_rich_view', async () => {
    const { c } = cliente();
    const sesion = esperar(c, 'alfalfa.session', m => m.payload.rich_view === false);
    c.iniciar();
    c.vistaEnriquecida(false);
    assert.equal((await sesion).payload.rich_view, false);
    c.cerrar();
    await once(c, 'salida');
    assert.equal(c.vistaEnriquecida(true), null);   // ya cerrado: no se manda nada
});

test('una secuencia rota se avisa como fallo', async () => {
    const c = new ClientePacai({ ejecutable: FALSO, cwd: __dirname });
    const fallo = once(c, 'fallo');
    c.iniciar();
    c._recibir(JSON.stringify({ pacai: '0.3', sender: 'agent', seq: 99, type: 'heartbeat', payload: {} }));
    assert.match((await fallo)[0].message, /Secuencia rota/);
    c.cerrar();
    await once(c, 'salida');
});

test('pide el perfil ui con el catálogo base', async () => {
    const { c } = cliente();
    const enviados = [];
    c.on('enviado', m => enviados.push(m));
    const empezada = esperar(c, 'session_started');
    c.iniciar();
    const s = await empezada;
    const hola = enviados.find(m => m.type === 'hello');
    assert.deepEqual(hola.payload.profiles, ['work', 'ui']);
    assert.equal(hola.payload.render.ui.catalog, 'base');
    assert.ok(s.payload.profiles.includes('ui'));
    c.cerrar();
    await once(c, 'salida');
});

test('una acción de pantalla va como input con su texto y su bloque, dentro del turno en curso', async () => {
    const { c } = cliente();
    const enviados = [];
    c.on('enviado', m => enviados.push(m));
    const peticion = esperar(c, 'approval_request');
    c.enviarTexto('hola');
    await peticion;                                    // el turno sigue abierto, esperando el permiso
    const data = { view: 'tickets', rev: 2, node: 'crear', event: 'press', name: 'crear', values: { title: 'Uno' } };
    c.accionDePantalla(data, 'Pulsé «Crear»');
    const input = enviados.at(-1);
    assert.equal(input.type, 'input');
    assert.equal(input.payload.mode, 'steer');
    assert.equal(input.payload.run_id, c.raiz);
    assert.deepEqual(input.payload.blocks, [{ type: 'text', text: 'Pulsé «Crear»' },
        { type: 'structured', schema: 'pacai.ui.action/1', data }]);
    c.cerrar();
    await once(c, 'salida');
});
