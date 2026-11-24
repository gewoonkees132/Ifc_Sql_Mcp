// The Model panel: what is shown. The project, the disciplines with each model under its
// own, the storeys and the rooms. main.js owns what is hidden; this module lays it out and
// reports the switches. File names, storey names and the hint come from the server: text.

import { el, number } from './dom.js';
import { TINTS } from './scene.js';
import { discipline } from './words.js';

const ARC_SWATCH = '#c9ccd1'; // "in their material colours"

/** An elevation as the Model panel shows it: "−1.25 m", with a true minus. */
export function elevation(value) {
  const text = Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? '−' : ''}${text} m`;
}

/**
 * The disciplines of the models shown, each with its models: those without a scene too,
 * with the command that builds theirs, so that a model loaded without --viewer says why it
 * is not drawn. A discipline whose models all lack a scene keeps its row, switched off.
 * @param {HTMLElement} list
 * @param {object[]} models The models shown, as GET /api/models lists them.
 * @param {Set<string>} hidden The disciplines hidden.
 * @param {(code: string, shown: boolean) => void} onSwitch
 */
export function renderDisciplines(list, models, hidden, onSwitch) {
  const codes = [...new Set(models.map((model) => model.discipline))];
  list.replaceChildren(
    ...codes.map((code) => {
      const own = models.filter((model) => model.discipline === code);
      const drawn = own.filter((model) => model.scene);
      const words = discipline(code);
      const elements = own.reduce((sum, model) => sum + model.elements, 0);
      const swatch = el('span', { className: 'sw', attrs: { 'aria-hidden': 'true' } });
      swatch.style.setProperty('--swatch', Object.hasOwn(TINTS, code) ? TINTS[code] : ARC_SWATCH);
      const id = `discipline-${code}`;
      const toggle = el('input', { className: 'switch', attrs: { type: 'checkbox', role: 'switch', id } });
      toggle.checked = drawn.length > 0 && !hidden.has(code);
      toggle.disabled = drawn.length === 0;
      toggle.addEventListener('change', () => onSwitch(code, toggle.checked));
      const state = drawn.length === 0 ? ' · no 3D yet' : hidden.has(code) ? ' · hidden' : '';
      const label = el(
        'label',
        { attrs: { for: id } },
        el('b', { text: words.name }),
        el('span', { text: `${number(elements)} elements · ${words.colour}${state}` }),
      );
      const lines = own.map((model) => {
        const line = el(
          'li',
          { className: 'model-line' },
          el('span', { className: 'file', text: model.file_name, title: `model ${model.model_id}` }),
          ` · ${number(model.elements)} elements`,
        );
        if (!model.scene) line.append(' · No 3D yet:', el('code', { text: model.scene_hint.replace(/^run /, '') }));
        return line;
      });
      return el('li', {}, el('div', { className: 'disc-row' }, swatch, label, toggle), el('ul', { className: 'models' }, ...lines));
    }),
  );
}

/**
 * One row per storey name across the models shown, highest first, with its elevation and
 * its elements: "Level 1" of the four Duplex models is one row.
 * @param {HTMLElement} list
 * @param {object[]} manifests The scenes shown.
 * @param {Set<string>} hidden The storeys hidden, by name ('' for elements on no storey).
 * @param {(key: string, shown: boolean) => void} onSwitch
 * @returns {Map<string, {elevation: number|null, box: HTMLInputElement}>}
 */
export function renderStoreys(list, manifests, hidden, onSwitch) {
  const storeys = new Map();
  for (const manifest of manifests) {
    for (const chunk of manifest.chunks) {
      const key = chunk.storey_name ?? '';
      if (!storeys.has(key)) storeys.set(key, { key, elevation: chunk.storey_elevation_m, elements: 0 });
      if (chunk.kind === 'elements') storeys.get(key).elements += chunk.element_count;
    }
  }
  const rows = [...storeys.values()].sort(
    (a, b) => (a.key === '') - (b.key === '') || (b.elevation ?? -Infinity) - (a.elevation ?? -Infinity) || a.key.localeCompare(b.key),
  );
  const boxes = new Map();
  list.replaceChildren(
    ...rows.map((storey) => {
      const box = el('input', { attrs: { type: 'checkbox' } });
      box.checked = !hidden.has(storey.key);
      boxes.set(storey.key, { elevation: storey.elevation, box });
      box.addEventListener('change', () => onSwitch(storey.key, box.checked));
      return el(
        'li',
        {},
        el(
          'label',
          {},
          box,
          el('span', { className: 'name', text: storey.key || 'no storey' }),
          el('span', { className: 'elevation', text: storey.elevation === null ? '' : elevation(storey.elevation) }),
          el('span', { className: 'count', text: number(storey.elements), title: 'elements' }),
        ),
      );
    }),
  );
  return boxes;
}
