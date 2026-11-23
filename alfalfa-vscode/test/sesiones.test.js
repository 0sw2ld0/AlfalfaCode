'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { listarSesiones, leerHistorial, sesionesDeLaCarpeta } = require('../lib/sesiones');

const fila = (id, cwd, extra = {}) => ({ id, cwd, encargo: 'Revisa ' + id, turnos: 1, fecha: 1, ...extra });

test('se quedan las de la carpeta (y sus subcarpetas), de la más reciente a la más antigua', () => {
    const filas = [
        fila('vieja', '/p/app', { fecha: 10 }),
        fila('nueva', '/p/app/sub', { fecha: 30 }),
        fila('otra', '/p/app-2', { fecha: 40 }),          // empieza igual, pero es otra carpeta
        fila('lejos', '/q', { fecha: 50 }),
        fila('vacia', '/p/app', { fecha: 60, encargo: 'Sesión PACAI', turnos: 0 }),  // panel abierto sin escribir
        fila('sin-turnos', '/p/app', { fecha: 20, turnos: 0 }),  // con encargo: la app la enseña
    ];
    assert.deepStrictEqual(sesionesDeLaCarpeta(filas, '/p/app').map(s => s.id), ['nueva', 'sin-turnos', 'vieja']);
    assert.deepStrictEqual(sesionesDeLaCarpeta(null, '/p/app'), []);
});

function alfalfaFalso(t, salidas) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'alfalfa-sesiones-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const ruta = path.join(dir, 'alfalfa');
    fs.writeFileSync(ruta, `#!/usr/bin/env node
const salidas = ${JSON.stringify(salidas)};
const clave = process.argv.slice(2).join(' ');
if (!(clave in salidas)) process.exit(2);
process.stdout.write(salidas[clave]);
`, { mode: 0o755 });
    return { ruta, dir };
}

test('lista con `--sesiones --json` y lee la conversación con `--historial`', async t => {
    const carpeta = fs.mkdtempSync(path.join(os.tmpdir(), 'proyecto-'));
    t.after(() => fs.rmSync(carpeta, { recursive: true, force: true }));
    const eventos = [{ rol: 'user', texto: 'hola', hora: 1, turno: 't1' }, { rol: 'agent', texto: 'Hola.', hora: 2 }];
    const { ruta } = alfalfaFalso(t, {
        '--sesiones --json': JSON.stringify({ version: 2, tipo: 'sesiones', sesiones: [
            fila('a', carpeta, { fecha: 5 }), fila('b', '/otra', { fecha: 9 })] }),
        '--historial a --json': JSON.stringify({ version: 2, tipo: 'historial', sesion: 'a', eventos, recortado: false }),
        '--historial rota --json': 'no es json',
    });
    // `/tmp` y `/private/tmp` son la misma carpeta en el Mac: la del motor puede venir con o sin el enlace.
    const sesiones = await listarSesiones(ruta, fs.realpathSync(carpeta), process.env);
    assert.deepStrictEqual(sesiones.map(s => s.id), ['a']);
    assert.deepStrictEqual(await leerHistorial(ruta, 'a', process.env), { eventos, recortado: false });
    assert.strictEqual(await leerHistorial(ruta, 'rota', process.env), null);
    assert.strictEqual(await leerHistorial(ruta, 'no-existe', process.env), null);
});
