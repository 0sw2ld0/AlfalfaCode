'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { CLAVE_HEREDADA, yaSeUsaba, vistaEnriquecida } = require('../lib/vista');

test('una instalación nueva empieza apagada y una de antes sigue encendida', () => {
    assert.equal(vistaEnriquecida({ defaultValue: false }, false), false);
    assert.equal(vistaEnriquecida({ defaultValue: false }, true), true);
    assert.equal(vistaEnriquecida(undefined, undefined), false);
});

test('lo que puso el usuario manda, y la carpeta sobre el usuario', () => {
    assert.equal(vistaEnriquecida({ globalValue: false }, true), false);
    assert.equal(vistaEnriquecida({ globalValue: true }, false), true);
    assert.equal(vistaEnriquecida({ globalValue: true, workspaceValue: false }, false), false);
    assert.equal(vistaEnriquecida({ globalValue: false, workspaceFolderValue: true }, false), true);
});

test('se sabe que ya se usaba por el estado que dejó la extensión', () => {
    assert.equal(yaSeUsaba(['alfalfa.ultimaSesion', 'alfalfa.modos']), true);
    assert.equal(yaSeUsaba([]), false);
    assert.equal(yaSeUsaba([CLAVE_HEREDADA]), false);
    assert.equal(yaSeUsaba(['otra.extension']), false);
});
