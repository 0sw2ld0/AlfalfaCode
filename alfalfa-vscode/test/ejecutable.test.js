'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { buscarAlfalfa, deLaApp } = require('../lib/ejecutable');

const APP = '/Applications/AlfalfaCode.app/Contents/Resources/motor/alfalfa';

test('con sólo la app instalada se usa el motor que lleva dentro', { skip: !fs.existsSync(APP) && 'sin la app' }, async () => {
    assert.equal(await deLaApp(), APP);
});

test('el ajuste manda: una ruta ejecutable vale, una que no existe no', async () => {
    const carpeta = fs.mkdtempSync(path.join(os.tmpdir(), 'alfalfa-'));
    const falso = path.join(carpeta, 'alfalfa');
    fs.writeFileSync(falso, '#!/bin/sh\n', { mode: 0o755 });
    assert.equal(await buscarAlfalfa(falso), falso);
    assert.equal(await buscarAlfalfa(path.join(carpeta, 'no-existe')), null);
    fs.rmSync(carpeta, { recursive: true });
});

test('el shell corre en su propia sesión, sin terminal, y se corta al vencer el plazo', async () => {
    const { enElShell } = require('../lib/ejecutable');
    // Sin terminal: `tty` dice «not a tty» y sale con 1, así que no hay salida.
    assert.equal(await enElShell('/bin/sh', ['-c', 'tty']), '');
    // Su propio grupo de procesos (pgid = pid) y sin terminal de control (`??` en macOS, `?` en Linux).
    const [grupo, terminal, pid] = (await enElShell('/bin/sh', ['-c', 'ps -o pgid= -o tty= -p $$; echo $$'])).trim().split(/\s+/);
    assert.ok(pid, 'el shell no respondió');
    assert.equal(grupo, pid);
    assert.match(terminal, /^\?+$/);
    const inicio = Date.now();
    assert.equal(await enElShell('/bin/sh', ['-c', 'sleep 30'], 300), '');
    assert.ok(Date.now() - inicio < 3000);
});
