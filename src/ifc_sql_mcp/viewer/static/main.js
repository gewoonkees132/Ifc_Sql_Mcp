// The page: the models of a project, every discipline together, storey and discipline
// toggles, and an element's details on a click (ADR 14, decision 6). The server decides
// what can be decided there (the projects, the set shown first, the counts and their
// wording); this module only lays it out.

import { ApiError, liveSource, takeToken } from './api.js';
import { Details } from './details.js';
import { el, metres, number } from './dom.js';
import { SceneView, TINTS } from './scene.js';

const LOADS_AT_ONCE = 4;

const $ = (id) => document.getElementById(id);
const view = new SceneView($('canvas'));
const details = new Details($('drawer'), () => select(null));

/**
 * For a browser test and for measuring: `ready` turns true once the shown models are drawn,
 * `ready_ms` milliseconds after the page began to load.
 */
const viewer = {
  ready: false,
  ready_ms: null,
  error: null,
  highlighted: 0,
  stats: () => view.stats(),
  measure: (seconds) => view.measure(seconds),
};
window.ifcViewer = viewer;

let api;
let catalogue;
let generation = 0; // a newer show() makes the loads of an older one stop
const shown = { models: [], manifests: [], spacesLoaded: false, bytes: 0 };
const hiddenDisciplines = new Set();
const hiddenStoreys = new Set();
let selection = null;

function status(text) {
  $('status').textContent = text;
}

function message(text) {
  $('message').hidden = !text;
  $('message').textContent = text ?? '';
}

function fail(error) {
  const text =
    error instanceof ApiError && error.status === 401
      ? 'The token is missing or wrong: ifc-sql view was probably started again. Open the URL it printed.'
      : String(error.message ?? error);
  viewer.error = text;
  message(text);
  status('Stopped.');
}

async function start() {
  const token = takeToken();
  if (!token) {
    fail(new Error('This page needs the token in the URL that ifc-sql view printed. Open that URL.'));
    return;
  }
  api = liveSource(token);
  try {
    catalogue = await api.models();
  } catch (error) {
    fail(error);
    return;
  }
  fillProjects();
  await show(catalogue.shown);
}

function fillProjects() {
  const picker = $('project');
  const sets = [...catalogue.projects];
  if (!sets.some((set) => sameSet(set, catalogue.shown))) sets.unshift(catalogue.shown);
  picker.replaceChildren(
    ...sets.map((set, index) => {
      const option = el('option', { text: set.label, attrs: { value: String(index) } });
      option.selected = sameSet(set, catalogue.shown);
      return option;
    }),
  );
  picker.disabled = sets.length < 2;
  picker.addEventListener('change', () => show(sets[Number(picker.value)]));
}

function sameSet(a, b) {
  return a.label === b.label && a.model_ids.join() === b.model_ids.join();
}

/** Show one set of models: the scenes of those that have one, each at its offset. */
async function show(set) {
  const mine = ++generation;
  viewer.ready = false;
  viewer.ready_ms = null;
  viewer.highlighted = 0;
  select(null);
  view.clear();
  message(null);
  hiddenDisciplines.clear();
  hiddenStoreys.clear();
  $('spaces').checked = false;
  const byId = new Map(catalogue.models.map((model) => [model.model_id, model]));
  shown.models = set.model_ids.map((id) => byId.get(id)).filter(Boolean);
  shown.manifests = [];
  shown.spacesLoaded = false;
  shown.bytes = 0;
  listModels();

  const drawn = shown.models.filter((model) => model.scene);
  if (drawn.length === 0) {
    listDisciplines();
    listStoreys();
    message(
      shown.models.length === 0
        ? 'No model to show. Load one with ifc-sql ingest <file> --discipline ARC --viewer.'
        : 'None of these models has a scene yet: the list on the left says how to build one.',
    );
    status('Nothing to draw.');
    ready();
    return;
  }
  status(`Reading ${drawn.length} scene${drawn.length === 1 ? '' : 's'}…`);
  try {
    shown.manifests = await Promise.all(drawn.map((model) => api.scene(model.model_id)));
  } catch (error) {
    if (mine === generation) fail(error);
    return;
  }
  if (mine !== generation) return;

  // Every model at its origin less the first model's: the models of a project line up.
  const [x0, y0, z0] = shown.manifests[0].origin_m;
  for (const manifest of shown.manifests) {
    const [x, y, z] = manifest.origin_m;
    view.addModel(manifest.model_id, manifest.discipline, [x - x0, y - y0, z - z0]);
  }
  listDisciplines();
  listStoreys();
  try {
    await load('elements', mine);
  } catch (error) {
    if (mine === generation) fail(error);
    return;
  }
  if (mine !== generation) return;
  if (!view.moved) view.fit();
  summarise();
  ready();
}

function ready() {
  viewer.ready = true;
  viewer.ready_ms = Math.round(performance.now());
}

/** Load the chunks of one kind of every shown model, a few at a time. */
async function load(kind, mine) {
  const queue = shown.manifests.flatMap((manifest) =>
    manifest.chunks.filter((chunk) => chunk.kind === kind).map((chunk) => ({ manifest, chunk })),
  );
  let next = 0;
  let done = 0;
  let first = true;
  const worker = async () => {
    while (next < queue.length && mine === generation) {
      const { manifest, chunk } = queue[next++];
      const buffer = await api.chunk(manifest.model_id, chunk.chunk_no);
      if (mine !== generation) return;
      await view.addChunk(manifest.model_id, chunk, buffer);
      shown.bytes += chunk.byte_count;
      done += 1;
      applyVisibility();
      if (first && kind === 'elements') {
        first = false;
        view.fit();
      }
      status(`Loading: ${done} of ${queue.length} chunks.`);
    }
  };
  await Promise.all(Array.from({ length: Math.min(LOADS_AT_ONCE, queue.length) }, worker));
}

function summarise() {
  const drawn = shown.manifests.reduce((sum, manifest) => sum + manifest.element_count, 0);
  status(
    `${shown.manifests.length} of ${shown.models.length} models drawn: ${number(drawn)} elements, ` +
      `${view.chunks.length} chunks, ${number(shown.bytes / 1e6, 1)} MB.`,
  );
}

function listModels() {
  $('models').replaceChildren(
    ...shown.models.map((model) => {
      const item = el(
        'li',
        { className: model.scene ? 'model' : 'model no-scene' },
        el('div', { className: 'model-head' }, badge(model.discipline), el('span', { className: 'file', text: model.file_name, title: `model ${model.model_id}` })),
      );
      if (model.scene) {
        item.append(el('div', { className: 'muted', text: model.scene.summary }));
      } else {
        item.append(el('div', { className: 'muted' }, 'No scene: ', el('code', { className: 'hint', text: model.scene_hint.replace(/^run /, '') })));
      }
      return item;
    }),
  );
}

function badge(discipline) {
  const node = el('span', { className: 'badge', text: discipline });
  if (Object.hasOwn(TINTS, discipline)) node.style.setProperty('--swatch', TINTS[discipline]);
  return node;
}

function listDisciplines() {
  const disciplines = [...new Set(shown.manifests.map((manifest) => manifest.discipline))];
  $('disciplines').replaceChildren(
    ...disciplines.map((discipline) => {
      const chip = el('button', { className: 'chip', attrs: { type: 'button', 'aria-pressed': 'true' } }, badge(discipline), Object.hasOwn(TINTS, discipline) ? ' tinted' : ' own colours');
      chip.addEventListener('click', () => {
        const hidden = hiddenDisciplines.has(discipline);
        if (hidden) hiddenDisciplines.delete(discipline);
        else hiddenDisciplines.add(discipline);
        chip.setAttribute('aria-pressed', String(hidden));
        applyVisibility();
      });
      return chip;
    }),
  );
}

/** The storeys of every shown model, by name, highest first: "Level 1" is one row for all. */
function listStoreys() {
  const storeys = new Map();
  for (const manifest of shown.manifests) {
    for (const chunk of manifest.chunks) {
      const key = chunk.storey_name ?? '';
      if (!storeys.has(key)) storeys.set(key, { key, elevation: chunk.storey_elevation_m, elements: 0 });
      if (chunk.kind === 'elements') storeys.get(key).elements += chunk.element_count;
    }
  }
  const rows = [...storeys.values()].sort(
    (a, b) => (a.key === '') - (b.key === '') || (b.elevation ?? -Infinity) - (a.elevation ?? -Infinity) || a.key.localeCompare(b.key),
  );
  $('storeys').replaceChildren(
    ...rows.map((storey) => {
      const box = el('input', { attrs: { type: 'checkbox' } });
      box.checked = true;
      box.addEventListener('change', () => {
        if (box.checked) hiddenStoreys.delete(storey.key);
        else hiddenStoreys.add(storey.key);
        applyVisibility();
      });
      return el(
        'li',
        {},
        el(
          'label',
          { className: 'storey' },
          box,
          el('span', { className: 'storey-name', text: storey.key || 'no storey' }),
          el('span', { className: 'muted count', text: number(storey.elements) }),
          el('span', { className: 'elevation', text: storey.elevation === null ? '' : metres(storey.elevation) }),
        ),
      );
    }),
  );
}

function applyVisibility() {
  const discipline = new Map(shown.manifests.map((manifest) => [manifest.model_id, manifest.discipline]));
  view.applyVisibility(
    (chunk) =>
      !hiddenDisciplines.has(discipline.get(chunk.modelId)) &&
      !hiddenStoreys.has(chunk.storey) &&
      (chunk.kind === 'elements' || $('spaces').checked),
  );
  $('all-storeys').hidden = hiddenStoreys.size === 0;
}

$('all-storeys').addEventListener('click', () => {
  hiddenStoreys.clear();
  for (const box of $('storeys').querySelectorAll('input')) box.checked = true;
  applyVisibility();
});

$('spaces').addEventListener('change', async () => {
  if ($('spaces').checked && !shown.spacesLoaded) {
    shown.spacesLoaded = true;
    try {
      await load('spaces', generation);
      summarise();
    } catch (error) {
      fail(error);
    }
  }
  applyVisibility();
});

/** Light one element and open its details; null clears the selection. */
async function select(picked) {
  selection = picked;
  viewer.highlighted = view.select(picked ? [[picked.modelId, picked.globalId]] : []);
  $('fit-selection').disabled = !picked;
  $('only-selection').disabled = !picked;
  if (!picked) {
    view.showOnly(false);
    $('only-selection').setAttribute('aria-pressed', 'false');
    details.close();
    $('caption').hidden = true;
    return;
  }
  caption(picked.globalId, null);
  details.loading(picked.globalId);
  try {
    const detail = await api.element(picked.modelId, picked.globalId);
    if (selection !== picked) return;
    caption(picked.globalId, detail);
    details.show(detail);
  } catch (error) {
    if (selection === picked) details.failed(picked.globalId, String(error.message ?? error));
  }
}

function caption(globalId, detail) {
  const box = $('caption');
  box.hidden = false;
  if (!detail) {
    box.replaceChildren(el('div', { className: 'kicker', text: 'Selected' }), el('div', { text: globalId }));
    return;
  }
  const where = [detail.storey_name, detail.space_number && `room ${detail.space_number}`].filter(Boolean).join(', ');
  box.replaceChildren(
    el('div', { className: 'kicker', text: `${detail.ifc_class} · ${detail.discipline}` }),
    el('div', { className: 'name', text: detail.name ?? globalId }),
    where ? el('div', { className: 'muted', text: where }) : null,
  );
}

view.addEventListener('pick', (event) => select(event.detail));
$('fit').addEventListener('click', () => view.fit());
$('fit-selection').addEventListener('click', () => view.fitSelection());
$('only-selection').addEventListener('click', () => {
  const only = !view.only;
  view.showOnly(only);
  $('only-selection').setAttribute('aria-pressed', String(only));
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') select(null);
});

start();
