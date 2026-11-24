// Element details, on the right when an element is clicked: everything `get_element` says
// about it, or on the static copy, for an element it holds no details of, its class, name
// and storey. It shares the right side with the Model panel (main.js). Every value from
// the IFC file is set as text (dom.js), never parsed as HTML.

import { el, metres, number } from './dom.js';

export class Details {
  /**
   * @param {HTMLElement} root The panel: #drawer-title, #drawer-body and #drawer-close in it.
   * @param {() => void} onClose Its ×.
   * @param {(open: boolean) => void} onToggle Told when it opens or closes.
   */
  constructor(root, onClose, onToggle) {
    this.root = root;
    this.onToggle = onToggle;
    this.title = root.querySelector('#drawer-title');
    this.body = root.querySelector('#drawer-body');
    root.querySelector('#drawer-close').addEventListener('click', onClose);
  }

  get open() {
    return !this.root.hidden;
  }

  close() {
    if (this.root.hidden) return;
    this.root.hidden = true;
    this.onToggle(false);
  }

  reveal() {
    if (!this.root.hidden) return;
    this.root.hidden = false;
    this.onToggle(true);
  }

  loading(globalId) {
    this.reveal();
    this.title.replaceChildren(el('div', { className: 'kicker', text: 'Reading the element…' }), el('div', { className: 'name', text: globalId }));
    this.body.replaceChildren();
  }

  failed(globalId, message) {
    this.reveal();
    this.title.replaceChildren(el('div', { className: 'kicker', text: 'Not found' }), el('div', { className: 'name', text: globalId }));
    this.body.replaceChildren(el('p', { className: 'error', text: message }));
  }

  /** Show `detail`, an ElementDetail of `get_element`, or the static copy's brief record. */
  show(detail) {
    this.reveal();
    const where = [detail.storey_name, detail.space_number && `room ${detail.space_number}`].filter(Boolean).join(', ');
    this.title.replaceChildren(
      el('div', { className: 'kicker', text: `${detail.ifc_class} · ${detail.discipline}` }),
      el('h2', { className: 'name', text: detail.name ?? '(no name)' }),
      where ? el('div', { className: 'where', text: where }) : '',
    );
    if (detail.brief) {
      const storey = section('Location', facts([['Storey', detail.storey_name]]));
      this.body.replaceChildren(identity(detail), storey, el('p', { className: 'muted', text: detail.note }));
      return;
    }
    const sections = [
      identity(detail),
      location(detail),
      quantities(detail.quantities),
      propertySets(detail.property_sets, detail.properties_truncated),
      materials(detail.materials),
      classifications(detail.classifications),
      findings(detail.data_quality),
    ];
    // replaceChildren would turn a null into the text "null".
    this.body.replaceChildren(...sections.filter(Boolean));
  }
}

function section(title, ...content) {
  return el('section', { className: 'detail-section' }, el('h3', { text: title }), ...content);
}

/** A definition list of the [label, value] pairs whose value is set. */
function facts(pairs) {
  const list = el('dl', { className: 'facts' });
  for (const [label, value] of pairs) {
    if (value === null || value === undefined || value === '') continue;
    list.append(el('dt', { text: label }), el('dd', {}, value instanceof Node ? value : String(value)));
  }
  return list;
}

function identity(detail) {
  const copy = el('button', { className: 'link', text: 'copy', attrs: { type: 'button' } });
  copy.addEventListener('click', () => {
    navigator.clipboard.writeText(detail.global_id).then(
      () => {
        copy.textContent = 'copied';
      },
      () => {
        copy.textContent = 'not copied';
      },
    );
  });
  const globalId = el('span', {}, el('code', { text: detail.global_id }), ' ', copy);
  return section(
    'Identity',
    facts([
      ['GlobalId', globalId],
      ['Model', `${detail.model_id} (${detail.discipline})`],
      ['Class', !detail.ifc_class_base || detail.ifc_class === detail.ifc_class_base ? detail.ifc_class : `${detail.ifc_class} (${detail.ifc_class_base})`],
      ['Revit family', detail.revit_family],
      ['Revit type', detail.revit_type],
      ['Revit ElementId', detail.revit_element_id],
      ['IFC type', detail.type_name],
      ['Predefined type', detail.predefined_type],
      ['Tag', detail.tag],
      ['Long name', detail.long_name],
    ]),
  );
}

function location(detail) {
  const storey =
    detail.storey_name === null
      ? null
      : detail.storey_elevation_m === null
        ? detail.storey_name
        : `${detail.storey_name} (${metres(detail.storey_elevation_m)})`;
  const space = detail.space_number === null ? null : [detail.space_number, detail.space_name].filter(Boolean).join(' ');
  return section(
    'Location',
    facts([
      ['Storey', storey],
      ['Room', space],
      ['Part of', detail.parent_name ?? detail.parent_ifc_class],
      ['Elevation', detail.elevation_m === null ? null : metres(detail.elevation_m)],
    ]),
  );
}

/** A table of text cells; `widths` gives each column's share of the drawer, in percent. */
function table(head, rows, widths) {
  return el(
    'table',
    { className: 'values' },
    el('colgroup', {}, ...widths.map((width) => el('col', { className: `w${width}` }))),
    el('thead', {}, el('tr', {}, ...head.map((text) => el('th', { text })))),
    el('tbody', {}, ...rows.map((cells) => el('tr', {}, ...cells.map((cell) => el('td', {}, cell))))),
  );
}

function quantities(list) {
  if (list.length === 0) return section('Quantities', el('p', { className: 'muted', text: 'None stored.' }));
  return section(
    'Quantities',
    table(
      ['Quantity', 'Value', 'Source'],
      list.map((q) => [q.name, number(q.value), q.source_ref ? `${q.source}: ${q.source_ref}` : q.source]),
      [30, 20, 50],
    ),
  );
}

function valueText(property) {
  if (property.value === null) return '—';
  if (typeof property.value === 'number') return property.unit ? `${number(property.value, 6)} ${property.unit}` : number(property.value, 6);
  return String(property.value);
}

function propertySets(sets, truncated) {
  const blocks = sets.map((set) => {
    const rows = set.properties.map((p) => [
      p.name,
      valueText(p),
      [p.from_type ? 'from type' : '', p.implausible ? 'implausible' : ''].filter(Boolean).join(', '),
    ]);
    return el(
      'details',
      { className: 'pset' },
      el('summary', { text: `${set.name} (${set.properties.length})` }),
      table(['Property', 'Value', ''], rows, [40, 45, 15]),
    );
  });
  const note = truncated ? el('p', { className: 'muted', text: 'More values are stored than shown.' }) : null;
  if (blocks.length === 0) return section('Property sets', el('p', { className: 'muted', text: 'None stored.' }));
  return section('Property sets', ...blocks, note);
}

function materials(list) {
  if (list.length === 0) return null;
  return section(
    'Materials',
    table(
      ['#', 'Material', 'Thickness'],
      list.map((m) => [String(m.layer_index), m.material, m.thickness_m === null ? '' : `${number(m.thickness_m)} m`]),
      [10, 60, 30],
    ),
  );
}

function classifications(list) {
  if (list.length === 0) return null;
  return section(
    'Classifications',
    table(
      ['System', 'Code', 'Title'],
      list.map((c) => [c.system, c.code, c.title ?? '']),
      [30, 20, 50],
    ),
  );
}

function findings(quality) {
  if (quality.status === 'never_checked') {
    return section('Data quality', el('p', { className: 'muted', text: 'Never checked: run ifc-sql dq.' }));
  }
  if (quality.findings.length === 0) {
    return section('Data quality', el('p', { className: 'muted', text: `No findings in dq run ${quality.dq_run_id}.` }));
  }
  return section(
    'Data quality',
    table(
      ['Severity', 'Rule', 'Message'],
      quality.findings.map((f) => [f.severity, f.rule_id, f.message]),
      [20, 30, 50],
    ),
    quality.truncated ? el('p', { className: 'muted', text: 'More findings are stored than shown.' }) : null,
  );
}
