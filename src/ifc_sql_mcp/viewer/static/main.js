// The page: the models of a project, every discipline together, storey and discipline
// toggles, an element's details on a click, and what each MCP call found (ADR 14,
// decisions 4 and 6). The server decides what can be decided there (the projects, the
// set shown first, the counts and their wording, what a call lights and in which
// colours); this module only lays it out. The static copy of the demo (decision 7) reads
// the same from files: api.js is the one seam.

import { ApiError, isStatic, liveSource, staticSource, takeToken } from './api.js';
import { Details } from './details.js';
import { el, metres, number } from './dom.js';
import { FeedPanel } from './feed.js';
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
  call: null, // the feed's id of the call lit last
  stats: () => view.stats(),
  measure: (seconds) => view.measure(seconds),
};
window.ifcViewer = viewer;

let api;
let catalogue;
let generation = 0; // a newer show() makes the loads of an older one stop
const shown = { models: [], manifests: [], loaded: new Set(), layers: new Set(), bytes: 0 };
const hiddenDisciplines = new Set();
const hiddenStoreys = new Set();
const storeyBoxes = new Map(); // storey name → { elevation, box }
let selection = null;
let lastCall = null;
let zoomed = false; // the last call moved the camera to one object
const ZOOM_MARGIN_M = 4;
const feed = new FeedPanel({ state: $('feed-state'), note: $('feed-note'), list: $('feed') }, (entry) => lightCall(entry));

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
  if (isStatic()) {
    api = staticSource();
    about();
  } else {
    const token = takeToken();
    if (!token) {
      fail(new Error('This page needs the token in the URL that ifc-sql view printed. Open that URL.'));
      return;
    }
    api = liveSource(token);
  }
  try {
    catalogue = await api.models();
  } catch (error) {
    fail(error);
    return;
  }
  fillProjects(catalogue.shown);
  await show(catalogue.shown);
  feed.listen(api);
}

/** The static copy: the recording's title, and the credit its files' licence asks for. */
async function about() {
  let recorded;
  try {
    recorded = await api.about();
  } catch {
    return; // the feed says the recording could not be read
  }
  $('tagline').textContent = recorded.title;
  const { text, source_url: source, licence_url: licence } = recorded.credit;
  // replaceChildren would turn a null into the text "null".
  const links = [link('source', source), link('licence', licence)].filter(Boolean);
  $('credit').replaceChildren(text, ...links.flatMap((a, index) => [index ? ' · ' : ' ', a]));
  $('credit').hidden = false;
}

/** A link to an https address, opened without telling it where from; null for any other. */
function link(text, href) {
  if (!/^https:\/\//.test(href)) return null;
  return el('a', { text, attrs: { href, rel: 'noopener noreferrer' } });
}

let sets = []; // the picker's sets of models, in its order

/** Fill the project picker with every project, and `current` picked. */
function fillProjects(current) {
  const picker = $('project');
  sets = [...catalogue.projects];
  if (!sets.some((set) => sameSet(set, current))) sets.unshift(current);
  picker.replaceChildren(
    ...sets.map((set, index) => {
      const option = el('option', { text: set.label, attrs: { value: String(index) } });
      option.selected = sameSet(set, current);
      return option;
    }),
  );
  picker.disabled = sets.length < 2;
}

$('project').addEventListener('change', () => show(sets[Number($('project').value)]));

/** Two sets of the same models, in the same order: the picker names them as the project. */
function sameSet(a, b) {
  return a.model_ids.join() === b.model_ids.join();
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
  shown.loaded = new Set();
  shown.layers = new Set();
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
    shown.loaded.add('elements');
    await load('elements', mine);
  } catch (error) {
    if (mine === generation) fail(error);
    return;
  }
  if (mine !== generation) return;
  if (!view.moved) view.fit();
  summarise();
  ready();
  if (lastCall) await lightCall(lastCall);
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
  storeyBoxes.clear();
  $('storeys').replaceChildren(
    ...rows.map((storey) => {
      const box = el('input', { attrs: { type: 'checkbox' } });
      box.checked = true;
      storeyBoxes.set(storey.key, { elevation: storey.elevation, box });
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
      (chunk.kind === 'elements' || (chunk.kind === 'spaces' && $('spaces').checked) || shown.layers.has(chunk.kind)),
  );
  $('all-storeys').hidden = hiddenStoreys.size === 0;
}

function showAllStoreys() {
  hiddenStoreys.clear();
  for (const { box } of storeyBoxes.values()) box.checked = true;
  applyVisibility();
}

$('all-storeys').addEventListener('click', showAllStoreys);

/** Load the chunks of `kind` (spaces, their copies, Revit Areas) once, for every shown model. */
async function ensureLoaded(kind) {
  if (shown.loaded.has(kind)) return;
  shown.loaded.add(kind);
  await load(kind, generation);
  summarise();
}

$('spaces').addEventListener('change', async () => {
  try {
    if ($('spaces').checked) await ensureLoaded('spaces');
  } catch (error) {
    fail(error);
  }
  applyVisibility();
});

// --------------------------------------------------------------------------- the feed's calls

/**
 * Light what one call found: its groups in their colours and the rest grey, the layers of
 * spaces it holds, the storeys above a selection on one storey hidden, and a caption.
 */
async function lightCall(entry) {
  lastCall = entry;
  const found = entry.selection;
  if (!found || !viewer.ready) return;
  // A call about a model that is not shown shows its project; show() then lights the call.
  const elsewhere = await projectOf(found);
  if (lastCall !== entry) return;
  if (elsewhere) {
    fillProjects(elsewhere);
    await show(elsewhere);
    return;
  }
  select(null);
  try {
    for (const kind of found.layers) await ensureLoaded(kind);
  } catch (error) {
    fail(error);
    return;
  }
  if (lastCall !== entry) return;
  shown.layers = new Set(found.layers);
  const groups = found.groups.map((group) => ({
    colour: group.colour,
    keys: group.models.flatMap((model) => model.global_ids.map((globalId) => [model.model_id, globalId])),
  }));
  const drawn = view.highlight(groups, true);
  const hidden = hideAbove(found);
  viewer.highlighted = drawn;
  viewer.call = entry.id;
  $('fit-selection').disabled = drawn === 0;
  $('only-selection').disabled = drawn === 0;
  // Rooms are inside the walls: a call that lights only spaces shows nothing else.
  const only = drawn > 0 && found.spaces === found.count;
  view.showOnly(only);
  $('only-selection').setAttribute('aria-pressed', String(only));
  if (found.zoom && drawn) {
    view.fitSelection(ZOOM_MARGIN_M);
    zoomed = true;
    const [group] = groups;
    const [modelId, globalId] = group.keys[0];
    showDetails(modelId, globalId);
  } else if (zoomed && drawn) {
    view.fit();
    zoomed = false;
  }
  callCaption(entry, drawn, hidden);
}

/**
 * The models to show for a selection that lies wholly outside those shown: the project of
 * its first model, as the picker lists it, or that model alone (a replaced one). Null when
 * a model of the selection is shown, when it holds nothing, or when the server lists none
 * of its models. A model loaded since the page opened is found by asking for the list again.
 */
async function projectOf(found) {
  const ids = [...new Set(found.groups.flatMap((group) => group.models.map((model) => model.model_id)))];
  const shownIds = new Set(shown.models.map((model) => model.model_id));
  if (ids.length === 0 || ids.some((id) => shownIds.has(id))) return null;
  const [first] = ids;
  const listed = () => catalogue.models.find((model) => model.model_id === first);
  if (!listed()) {
    try {
      catalogue = await api.models();
    } catch {
      return null;
    }
  }
  const model = listed();
  if (!model) return null;
  return (
    catalogue.projects.find((set) => set.model_ids.includes(first)) ?? {
      label: `${model.file_name} (model ${first})`,
      project_group: null,
      model_ids: [first],
    }
  );
}

/** Open the drawer with an object's details, as a click does, without changing the caption. */
async function showDetails(modelId, globalId) {
  details.loading(globalId);
  try {
    details.show(await api.element(modelId, globalId));
  } catch (error) {
    details.failed(globalId, String(error.message ?? error));
  }
}

/**
 * Show every storey again, then hide those above a selection that lies on one storey;
 * return their names.
 */
function hideAbove(found) {
  hiddenStoreys.clear();
  for (const { box } of storeyBoxes.values()) box.checked = true;
  if (found.storey_elevation_m === null) {
    applyVisibility();
    return [];
  }
  const above = [...storeyBoxes.entries()]
    .filter(([name, { elevation }]) => name && elevation !== null && elevation > found.storey_elevation_m + 1e-6)
    .sort((a, b) => a[1].elevation - b[1].elevation);
  for (const [name, { box }] of above) {
    hiddenStoreys.add(name);
    box.checked = false;
  }
  applyVisibility();
  return above.map(([name]) => name);
}

function callCaption(entry, drawn, hidden) {
  const found = entry.selection;
  const shownIds = new Set(shown.models.map((model) => model.model_id));
  const elsewhere = [...new Set(found.groups.flatMap((group) => group.models.map((model) => model.model_id)))].filter((id) => !shownIds.has(id));
  const without = Math.max(found.count - drawn - found.openings - found.not_drawn, 0);
  const counts = [`${number(found.count)} found`, `${number(drawn)} shown`, `${number(without)} without shape`];
  if (found.openings) counts.push(`${number(found.openings)} openings not drawn`);
  if (found.not_drawn) counts.push(`${number(found.not_drawn)} storeys or buildings, not drawn`);
  if (found.truncated) counts.push('the first 20,000');

  const box = $('caption');
  box.hidden = false;
  const kicker = entry.step ? `Step ${entry.step.index} of ${entry.step.steps} · ${entry.source}` : entry.source;
  const lines = [
    el('div', { className: 'kicker', text: kicker }),
    el('div', { className: 'name', text: entry.step ? entry.step.title : entry.summary }),
  ];
  if (entry.step) lines.push(el('div', { className: 'muted', text: entry.summary }));
  if (entry.step) lines.push(el('div', { className: 'figure', text: entry.step.figure }));
  lines.push(el('div', { className: 'counts', text: counts.join(' · ') }));
  if (found.note) lines.push(el('div', { className: 'muted', text: found.note }));
  if (elsewhere.length) {
    lines.push(el('div', { className: 'muted', text: `Model ${elsewhere.join(', ')} is not shown here: pick its project above.` }));
  }
  if (hidden.length) {
    const all = el('button', { className: 'link', text: 'show all', attrs: { type: 'button' } });
    all.addEventListener('click', showAllStoreys);
    lines.push(el('div', { className: 'muted' }, `${hidden.join(', ')} hidden · `, all));
  }
  if (found.groups.length > 1 || (found.groups.length === 1 && found.groups[0].label)) {
    lines.push(
      el(
        'ul',
        { className: 'legend' },
        ...found.groups.map((group) => {
          const swatch = el('span', { className: 'swatch' });
          swatch.style.setProperty('--swatch', group.colour);
          return el('li', {}, swatch, el('span', { text: group.label ?? '(none)' }), el('span', { className: 'muted', text: number(group.count) }));
        }),
      ),
    );
  }
  box.replaceChildren(...lines);
}

/** Light one element and open its details; null clears the selection. */
async function select(picked) {
  selection = picked;
  viewer.call = null;
  if (shown.layers.size) {
    shown.layers = new Set(); // the spaces a call showed go with its selection
    applyVisibility();
  }
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
