// The page: the models of a project full screen, every discipline together, an answer card
// over them, Ask Claude, the Model panel, an element's details on a click, and what each
// MCP call found (ADR 14, decisions 4, 6 and 8). One page serves ifc-sql view, ifc-sql
// demo and the static copy of the demo (decision 7): api.js is the one seam for data, and
// the page's mode follows the feed's status:
// - demo: the static copy. The visitor picks the recorded questions; nothing plays itself.
// - scripted: ifc-sql demo. The script asks, at its own pace; the page follows.
// - local: ifc-sql view. Claude's calls arrive from the feed and light up as they come.
// The server decides what can be decided there (the projects, the set shown first, what a
// call lights and in which colours); this module lays it out.

import { ApiError, isStatic, liveSource, staticSource, takeToken } from './api.js';
import { AskPanel } from './ask.js';
import { renderCurrent } from './card.js';
import { Details } from './details.js';
import { el, number } from './dom.js';
import { Explainer } from './explainer.js';
import { renderDisciplines, renderStoreys } from './model.js';
import { SceneView } from './scene.js';
import { headline, sentence } from './words.js';

const LOADS_AT_ONCE = 4;
const ZOOM_MARGIN_M = 4;

const $ = (id) => document.getElementById(id);
const phone = window.matchMedia('(max-width: 759px)');
const view = new SceneView($('canvas'));
const details = new Details(
  $('drawer'),
  () => {
    select(null);
    home().focus();
  },
  () => sides(),
);
const explainer = new Explainer();

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
  mode: null,
  stats: () => view.stats(),
  measure: (seconds) => view.measure(seconds),
};
window.ifcViewer = viewer;

let api;
let catalogue;
let generation = 0; // a newer show() makes the loads of an older one stop
const shown = { set: null, models: [], manifests: [], loaded: new Set(), layers: new Set(), bytes: 0 };
const hiddenDisciplines = new Set();
const hiddenStoreys = new Set();
let storeyBoxes = new Map(); // storey name → { elevation, box }
let picked = null; // the element clicked
let zoomed = false; // the last call moved the camera to one object

/** What the card and the panel show: the calls, the questions, the one shown, what is lit. */
const state = {
  mode: 'local',
  status: null,
  entries: new Map(), // id → call
  order: [], // the ids asked, in order: the history
  current: null, // the call the card shows
  lit: null, // the call whose answer the view shows now
  group: null, // the legend's group shown alone
  hidden: [], // the storeys hidden above the answer
  opened: false, // the demo has shown its first question
  how: null, // the call whose "How it was answered" is open
};

const ask = new AskPanel({
  pick: (index) => pick(index),
  show: (entry) => (entry.step && state.mode !== 'local' ? pick(entry.step.index) : show(entry)),
  help: (section) => explain(section),
  toggled: () => {},
});

// --------------------------------------------------------------------------- start

function status(text, ...more) {
  $('status').replaceChildren(text ?? '', ...more);
}

function toast(text) {
  $('toast-text').textContent = text ?? '';
  $('toast').hidden = !text;
}

$('toast-close').addEventListener('click', () => toast(null));

function fail(error) {
  const text =
    error instanceof ApiError && error.status === 401
      ? 'The token is missing or wrong: ifc-sql view was probably started again. Open the URL it printed.'
      : String(error.message ?? error);
  viewer.error = text;
  toast(text);
  status('');
}

async function start() {
  if (isStatic()) {
    api = staticSource();
    setMode('demo');
    credit();
  } else {
    const token = takeToken();
    if (!token) {
      fail(new Error('This page needs the token in the URL that ifc-sql view printed. Open that URL.'));
      return;
    }
    api = liveSource(token);
  }
  render();
  status('Reading the models…');
  try {
    catalogue = await api.models();
  } catch (error) {
    fail(error);
    return;
  }
  fillProjects(catalogue.shown);
  listen();
  await showModels(catalogue.shown);
}

/** The static copy: the credit its files' licence asks for, linked, at the bottom right. */
async function credit() {
  let recorded;
  try {
    recorded = await api.about();
  } catch {
    return; // the toast says the recording could not be read
  }
  const { work, author, licence, text, source_url: source, licence_url: licenceUrl } = recorded.credit;
  // replaceChildren would turn a null into the text "null".
  $('credit').replaceChildren(link(work, source) ?? work, ` · ${author} · `, link(licence, licenceUrl) ?? licence);
  $('credit').title = text;
  $('credit').hidden = false;
  $('explainer-credit').textContent = text;
}

/** A link to an https address, opened without telling it where from; null for any other. */
function link(text, href) {
  if (!/^https:\/\//.test(href)) return null;
  return el('a', { text, attrs: { href, rel: 'noopener noreferrer', target: '_blank' } });
}

// --------------------------------------------------------------------------- the feed's calls

function setMode(mode) {
  if (state.mode === mode && viewer.mode) return;
  state.mode = mode;
  viewer.mode = mode;
  document.body.dataset.mode = mode;
}

/** Listen to the calls: live, as they arrive; on the static copy, every recorded one at once. */
function listen() {
  api.events({
    open: () => {},
    status: (next) => {
      state.status = next;
      if (next.state === 'recorded') setMode('demo');
      else if (next.questions?.length) setMode('scripted');
      else setMode('local');
      render();
    },
    call: (entry) => arrive(entry),
    error: (closed, message) => {
      if (!closed) return;
      state.status = { ...(state.status ?? {}), state: 'off', message: message ?? 'the page lost the server: reload it, or open the URL ifc-sql view printed' };
      if (state.mode === 'demo') toast(state.status.message);
      render();
    },
  });
}

function arrive(entry) {
  state.entries.set(entry.id, entry);
  if (state.mode === 'demo') {
    // The recorded calls are the answers to pick from; the demo opens on question 1.
    if (!state.opened && entry.step?.index === 1) {
      state.opened = true;
      pick(1);
    }
    render();
    return;
  }
  state.order.push(entry.id);
  if (state.mode === 'scripted' && entry.step) {
    show(entry); // the script asks: each question stands alone
  } else if (entry.selection) {
    show(entry); // the newest call is lit as it arrives
  } else if (!state.current) {
    show(entry); // nothing lit yet: the card says what came
  } else {
    render(); // it lights nothing by nature: the view stays as it is
  }
}

/** The questions of the script, and the call that answers each, by number. */
function questions() {
  const listed = state.status?.questions ?? [];
  const byStep = new Map();
  for (const entry of state.entries.values()) {
    if (entry.step) byStep.set(entry.step.index, entry);
  }
  return { listed, byStep };
}

/** In a demo, show the answer to question `index`, if it has one yet. */
function pick(index) {
  const { byStep } = questions();
  const entry = byStep.get(index);
  if (!entry) return;
  if (state.mode === 'demo' && !state.order.includes(entry.id)) state.order.push(entry.id);
  show(entry);
}

/** Show a call in the card, and light its answer. */
function show(entry) {
  state.current = entry;
  state.group = null;
  const step = state.mode !== 'local' ? entry.step : null;
  if (entry.selection) {
    light(entry).then(() => announce(entry));
  } else if (state.mode !== 'local') {
    unlight(); // in a demo each question stands alone: "What is loaded?" lights nothing
  }
  if (step || !entry.selection) announce(entry);
  render();
}

/** Say a new answer to a screen reader, politely. */
function announce(entry) {
  const step = state.mode !== 'local' ? entry.step : null;
  if (step) {
    $('announce').textContent = `Question ${step.index}: ${step.title} ${step.answer}.`;
    return;
  }
  const big = headline(entry, view.lit);
  $('announce').textContent = big ? `${sentence(entry)}: ${big}.` : `${sentence(entry)}.`;
}

/**
 * Draw the card and the panel again. The focus stays on the button it was on, which the
 * new one replaces (each carries its data-key).
 */
function render() {
  const focused = $('panel').contains(document.activeElement) ? document.activeElement.dataset.key : undefined;
  draw();
  if (focused) $('panel').querySelector(`[data-key="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  if (state.current !== revealed) {
    revealed = state.current;
    reveal();
  }
}

let revealed = null; // the call the thread last scrolled to

/**
 * Scroll the history and the answer, which scroll as one, to the answer: its top in view,
 * and as much of the history above it as fits.
 */
function reveal() {
  $('thread').scrollTop = $('current').offsetTop - 8;
}

// Again once the panel has opened or closed, or the phone's sheet shown the history.
$('panel').addEventListener('transitionend', (event) => {
  if (event.propertyName === 'height' && (event.target === $('panel') || event.target === $('history'))) reveal();
});

function draw() {
  const { listed, byStep } = questions();
  const asked = new Set();
  for (const id of state.order) {
    const step = state.entries.get(id)?.step;
    if (step) asked.add(step.index);
  }
  if (state.current?.step && state.mode !== 'local') asked.add(state.current.step.index);
  const history = state.order.filter((id) => id !== state.current?.id).map((id) => state.entries.get(id));
  ask.render({ mode: state.mode, status: state.status, questions: listed, byStep, history, asked, current: state.current });
  renderCard(listed, byStep);
  pillLabels();
}

function renderCard(listed, byStep) {
  const entry = state.current;
  const step = state.mode !== 'local' ? entry?.step : null;
  let stepper = null;
  if (step) {
    const pickable = listed.map((q) => q.index).filter((index) => byStep.has(index));
    const at = pickable.indexOf(step.index);
    stepper = { index: step.index, total: step.steps, previous: at > 0, next: at >= 0 && at < pickable.length - 1, pickable, at };
  }
  const empty = {
    local: 'No question yet. Ask Claude in Claude Desktop or Claude Code, and what it finds lights up here.',
    scripted: 'No question yet: the script asks its first in a moment.',
    demo: 'Reading the recorded answers…',
  }[state.mode];
  const lit = state.lit !== null && state.lit === entry;
  renderCurrent(
    $('current'),
    {
      entry,
      mode: state.mode,
      empty,
      lit,
      drawn: lit ? view.lit : { drawn: 0, classes: new Set() },
      hidden: state.hidden,
      group: state.group,
      howOpen: state.how !== null && state.how === entry?.id,
      elsewhere: lit ? elsewhereOf(entry.selection) : [],
      stepper,
    },
    {
      previous: () => pick(stepper.pickable[stepper.at - 1]),
      next: () => pick(stepper.pickable[stepper.at + 1]),
      group: (index) => lightGroup(index),
      how: (open) => {
        state.how = open ? entry.id : null;
      },
      relight: () => light(entry).then(render),
      showAll: () => {
        showAllStoreys();
        render();
      },
    },
  );
}

/** The labels of the view pill follow what is lit: an answer, or one element clicked. */
function pillLabels() {
  const element = picked !== null;
  $('fit-selection-label').textContent = element ? 'Fit element' : 'Fit answer';
  $('only-selection-label').textContent = element ? 'Only this element' : 'Only the answer';
  const any = viewer.highlighted > 0;
  $('fit-selection').disabled = !any;
  $('only-selection').disabled = !any;
  if (!any) $('only-selection').setAttribute('aria-pressed', 'false');
}

// --------------------------------------------------------------------------- the models

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

$('project').addEventListener('change', () => showModels(sets[Number($('project').value)]));

/** Two sets of the same models, in the same order: the picker names them as the project. */
function sameSet(a, b) {
  return a.model_ids.join() === b.model_ids.join();
}

/** Show one set of models: the scenes of those that have one, each at its offset. */
async function showModels(set) {
  const mine = ++generation;
  viewer.ready = false;
  viewer.ready_ms = null;
  viewer.highlighted = 0;
  select(null, { keep: true });
  view.clear();
  toast(null);
  state.lit = null;
  hiddenDisciplines.clear();
  hiddenStoreys.clear();
  $('spaces').checked = false;
  const byId = new Map(catalogue.models.map((model) => [model.model_id, model]));
  shown.set = set;
  shown.models = set.model_ids.map((id) => byId.get(id)).filter(Boolean);
  shown.manifests = [];
  shown.loaded = new Set();
  shown.layers = new Set();
  shown.bytes = 0;
  listDisciplines();
  listStoreys();

  const drawn = shown.models.filter((model) => model.scene);
  if (drawn.length === 0) {
    if (shown.models.length === 0) {
      status('No model is loaded yet: load one with ifc-sql ingest <file> --discipline ARC --viewer.');
    } else {
      const open = el('button', { className: 'link', text: 'Open the Model panel', attrs: { type: 'button' } });
      open.addEventListener('click', () => openModel());
      status('None of these models has 3D yet: the Model panel says how to build it. ', open);
    }
    ready();
    render();
    return;
  }
  status(`Reading ${set.label}…`);
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
  status('');
  ready();
  if (state.current?.selection) await light(state.current);
  render();
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
      if (kind === 'elements') status(`Drawing ${shown.set.label}… ${done} of ${queue.length} parts`);
    }
  };
  await Promise.all(Array.from({ length: Math.min(LOADS_AT_ONCE, queue.length) }, worker));
}

function listDisciplines() {
  renderDisciplines($('disciplines'), shown.models, hiddenDisciplines, (code, visible) => {
    if (visible) hiddenDisciplines.delete(code);
    else hiddenDisciplines.add(code);
    applyVisibility();
    listDisciplines();
    $(`discipline-${code}`)?.focus();
  });
}

function listStoreys() {
  storeyBoxes = renderStoreys($('storeys'), shown.manifests, hiddenStoreys, (key, visible) => {
    if (visible) hiddenStoreys.delete(key);
    else hiddenStoreys.add(key);
    state.hidden = state.hidden.filter((name) => hiddenStoreys.has(name));
    applyVisibility();
    render();
  });
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
  state.hidden = [];
  for (const { box } of storeyBoxes.values()) box.checked = true;
  applyVisibility();
}

$('all-storeys').addEventListener('click', () => {
  showAllStoreys();
  render();
});

/** Load the chunks of `kind` (spaces, their copies, Revit Areas) once, for every shown model. */
async function ensureLoaded(kind) {
  if (shown.loaded.has(kind)) return;
  shown.loaded.add(kind);
  await load(kind, generation);
}

$('spaces').addEventListener('change', async () => {
  try {
    if ($('spaces').checked) await ensureLoaded('spaces');
  } catch (error) {
    fail(error);
  }
  applyVisibility();
});

// --------------------------------------------------------------------------- lighting an answer

/**
 * Light what one call found: its groups in their colours and the rest grey, the layers of
 * spaces it holds, and the storeys above a selection on one storey hidden.
 */
async function light(entry) {
  const found = entry.selection;
  if (!found || !viewer.ready) {
    state.lit = found ? entry : null;
    return;
  }
  // A call about a model that is not shown shows its project; showModels() then lights the call.
  const elsewhere = await projectOf(found);
  if (state.current !== entry) return;
  if (elsewhere) {
    fillProjects(elsewhere);
    await showModels(elsewhere);
    return;
  }
  select(null, { keep: true });
  try {
    for (const kind of found.layers) await ensureLoaded(kind);
  } catch (error) {
    fail(error);
    return;
  }
  if (state.current !== entry) return;
  shown.layers = new Set(found.layers);
  state.lit = entry;
  state.group = null;
  const drawn = view.highlight(groupsOf(found), true);
  state.hidden = hideAbove(found);
  viewer.highlighted = drawn;
  viewer.call = entry.id;
  // Rooms are inside the walls: a call that lights only spaces shows nothing else.
  const only = drawn > 0 && found.spaces === found.count;
  view.showOnly(only);
  $('only-selection').setAttribute('aria-pressed', String(only));
  if (found.zoom && drawn) {
    view.fitSelection(ZOOM_MARGIN_M);
    zoomed = true;
    const [modelId, globalId] = groupsOf(found)[0].keys[0];
    showDetails(modelId, globalId);
  } else if (zoomed && drawn) {
    view.fit();
    zoomed = false;
  }
  render();
}

/** The groups of a selection, as the view lights them. */
function groupsOf(found) {
  return found.groups.map((group) => ({
    colour: group.colour,
    keys: group.models.flatMap((model) => model.global_ids.map((globalId) => [model.model_id, globalId])),
  }));
}

/** The legend: show only one group of the answer, or all of them again. */
function lightGroup(index) {
  const entry = state.lit;
  if (!entry) return;
  state.group = state.group === index ? null : index;
  const groups = groupsOf(entry.selection);
  viewer.highlighted = view.highlight(state.group === null ? groups : [groups[state.group]], true);
  render();
}

/** Clear what an answer lit: every element in its own colour, every storey shown. */
function unlight() {
  select(null, { keep: true });
  state.lit = null;
  viewer.highlighted = view.highlight([]);
  view.showOnly(false);
  if (shown.layers.size) {
    shown.layers = new Set();
  }
  showAllStoreys();
  if (zoomed) {
    view.fit();
    zoomed = false;
  }
}

/** The models of a selection that the page does not show. */
function elsewhereOf(found) {
  const shownIds = new Set(shown.models.map((model) => model.model_id));
  return [...new Set(found.groups.flatMap((group) => group.models.map((model) => model.model_id)))].filter((id) => !shownIds.has(id));
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

// --------------------------------------------------------------------------- a click

/** Open an object's details, as a click does, without changing the card. */
async function showDetails(modelId, globalId) {
  closeModel();
  details.loading(globalId);
  try {
    details.show(await api.element(modelId, globalId));
  } catch (error) {
    details.failed(globalId, String(error.message ?? error));
  }
}

/**
 * Light one element teal and open its details; null clears it. A click replaces an
 * answer's highlight, and the card then says its answer is no longer lit. With `keep`,
 * the answer's state is left to the caller (a new answer is about to be lit).
 */
async function select(element, { keep = false } = {}) {
  picked = element;
  viewer.call = null;
  if (!keep) state.lit = null;
  if (shown.layers.size) {
    shown.layers = new Set(); // the spaces a call showed go with its selection
    applyVisibility();
  }
  viewer.highlighted = view.select(element ? [[element.modelId, element.globalId]] : []);
  if (!element) {
    view.showOnly(false);
    $('only-selection').setAttribute('aria-pressed', 'false');
    details.close();
    if (!keep) render();
    return;
  }
  render();
  closeModel();
  details.loading(element.globalId);
  try {
    const detail = await api.element(element.modelId, element.globalId);
    if (picked !== element) return;
    details.show(detail);
  } catch (error) {
    if (picked === element) details.failed(element.globalId, String(error.message ?? error));
  }
}

// A click on nothing clears a clicked element, but leaves an answer lit: a stray tap on a
// phone does not throw it away.
view.addEventListener('pick', (event) => {
  if (event.detail || picked) select(event.detail);
});

// --------------------------------------------------------------------------- the pill, the panels

$('fit').addEventListener('click', () => view.fit());
$('fit-round').addEventListener('click', () => view.fit());
$('fit-selection').addEventListener('click', () => view.fitSelection());
$('only-selection').addEventListener('click', () => {
  const only = !view.only;
  view.showOnly(only);
  $('only-selection').setAttribute('aria-pressed', String(only));
});

/** The right side holds one panel: the Model panel or an element's details. */
function sides() {
  const open = !$('model-panel').hidden || details.open;
  document.body.classList.toggle('side-open', open);
}

function openModel() {
  details.close();
  $('model-panel').hidden = false;
  for (const button of [$('model-open'), $('model-open-phone')]) button.setAttribute('aria-expanded', 'true');
  sides();
  $('model-close').focus();
}

function closeModel({ focus = false } = {}) {
  if ($('model-panel').hidden) return;
  $('model-panel').hidden = true;
  for (const button of [$('model-open'), $('model-open-phone')]) button.setAttribute('aria-expanded', 'false');
  sides();
  if (focus) (phone.matches ? $('model-open-phone') : $('model-open')).focus();
}

for (const button of [$('model-open'), $('model-open-phone')]) {
  button.addEventListener('click', () => ($('model-panel').hidden ? openModel() : closeModel({ focus: true })));
}
$('model-close').addEventListener('click', () => closeModel({ focus: true }));

/** The explainer; `section` names a box to go to: how to connect, how to ask your own. */
function explain(section) {
  ask.closeList();
  explainer.show(state.mode, section);
}

for (const button of [$('help'), $('help-phone')]) button.addEventListener('click', () => explain());

/** Where the focus goes when the panel that had it closes: the Ask Claude button or box. */
function home() {
  if (!phone.matches && !ask.isOpen) return $('ask-open');
  if (!ask.input.disabled) return ask.input;
  return phone.matches ? $('help-phone') : $('ask-close');
}

/**
 * Esc closes one thing: the list of questions; else the Ask Claude panel when the focus is
 * in it; else, the newest first, a clicked element and its details, the Model panel, the
 * Ask Claude panel, and last what an answer lit.
 */
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (explainer.open) return; // the dialog closes itself
  if (ask.listOpen || ($('panel').contains(document.activeElement) && ask.isOpen)) {
    if (ask.escape()) return;
  }
  if (details.open || picked) {
    const focus = $('drawer').contains(document.activeElement);
    select(null);
    if (focus) home().focus();
    return;
  }
  if (!$('model-panel').hidden) {
    closeModel({ focus: true });
    return;
  }
  if (ask.escape()) return;
  if (state.lit) {
    unlight();
    render();
  }
});

// On a phone, the round fit button sits above the sheet.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--sheet-h', `${Math.round($('panel').getBoundingClientRect().height)}px`);
}).observe($('panel'));

start();
