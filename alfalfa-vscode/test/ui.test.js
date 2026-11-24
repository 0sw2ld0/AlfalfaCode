'use strict';
// El renderizador de pantallas del panel (media/ui.js) es una copia del de la demo web del perfil `ui`.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const COPIA = path.join(__dirname, '..', 'media', 'ui.js');
const ORIGINAL = path.join(__dirname, '..', '..', 'docs', 'demos', 'pacai-ui', 'ui.js');

test('media/ui.js es idéntico al de la demo (cópialo de allí si cambia)', { skip: !fs.existsSync(ORIGINAL) }, () => {
    assert.equal(fs.readFileSync(COPIA, 'utf8'), fs.readFileSync(ORIGINAL, 'utf8'));
});

test('el texto legible de una acción es el mismo que pone la app de Mac', async () => {
    const { resumen } = await import(pathToFileURL(COPIA).href);
    const base = { view: 'v', rev: 1, node: 'n', name: 'crear', values: {} };
    assert.equal(resumen({ ...base, event: 'press' }, 'Crear ticket'), 'Pulsé «Crear ticket»');
    assert.equal(resumen({ ...base, event: 'press' }, null), 'Pulsé «crear»');
    assert.equal(resumen({ ...base, event: 'submit' }, 'Crear'), 'Envié el formulario «Crear»');
    assert.equal(resumen({ ...base, event: 'row_press', item: { title: 'Login', id: 'T-12', priority: 'Alta' } }),
        'Elegí «T-12 · Alta · Login»');
    assert.equal(resumen({ ...base, event: 'item_press', item: 'a1' }), 'Elegí «a1»');
});
