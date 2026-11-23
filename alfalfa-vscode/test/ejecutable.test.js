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
