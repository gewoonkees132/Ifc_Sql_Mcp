// The page's words: what each tool is called, the disciplines, the nouns of IFC classes,
// and a call in a plain sentence. Pure functions of what the feed and the scenes carry.
// Every value they put into a sentence (a storey's name, a type) came from the network:
// the callers set the sentences as text (dom.js), never as HTML.

import { number } from './dom.js';

/**
 * Each tool's title, as the MCP server gives it (`server/app.py`). A unit test pins this
 * table to the server's tools, so that a twelfth tool cannot go unnamed here.
 */
export const TITLES = {
  list_models: 'List models',
  list_storeys: 'List storeys',
  find_elements: 'Find elements',
  get_element: 'Get element',
  quantity_takeoff: 'Quantity takeoff',
  space_schedule: 'Space schedule',
  schedule: 'Schedule',
  data_quality_report: 'Data quality report',
  compare_models: 'Compare models',
  describe_schema: 'Describe schema',
  run_sql: 'Run SQL',
};

/** The disciplines in words; their tints are scene.js's. A code not listed is shown as it is. */
export const DISCIPLINES = {
  ARC: { name: 'Architecture', colour: 'in their material colours' },
  MEP: { name: 'Mechanical', colour: 'tinted blue' },
  ELE: { name: 'Electrical', colour: 'tinted amber' },
  PLU: { name: 'Plumbing', colour: 'tinted green' },
  STR: { name: 'Structure', colour: 'in their material colours' },
};

export function discipline(code) {
  return Object.hasOwn(DISCIPLINES, code) ? DISCIPLINES[code] : { name: code, colour: 'in their material colours' };
}

/** Nouns for the classes an answer lights most often, by class without its IFC2x3 suffix. */
const NOUNS = {
  IfcWall: ['wall', 'walls'],
  IfcCurtainWall: ['curtain wall', 'curtain walls'],
  IfcDoor: ['door', 'doors'],
  IfcWindow: ['window', 'windows'],
  IfcSlab: ['slab', 'slabs'],
  IfcRoof: ['roof', 'roofs'],
  IfcStair: ['stair', 'stairs'],
  IfcStairFlight: ['stair flight', 'stair flights'],
  IfcRamp: ['ramp', 'ramps'],
  IfcRailing: ['railing', 'railings'],
  IfcColumn: ['column', 'columns'],
  IfcBeam: ['beam', 'beams'],
  IfcMember: ['member', 'members'],
  IfcPlate: ['plate', 'plates'],
  IfcCovering: ['covering', 'coverings'],
  IfcFooting: ['footing', 'footings'],
  IfcFurnishingElement: ['piece of furniture', 'pieces of furniture'],
  IfcFurniture: ['piece of furniture', 'pieces of furniture'],
  IfcSpace: ['space', 'spaces'],
  IfcOpeningElement: ['opening', 'openings'],
  IfcDuctSegment: ['duct', 'ducts'],
  IfcPipeSegment: ['pipe', 'pipes'],
  IfcFlowSegment: ['segment', 'segments'],
  IfcFlowFitting: ['fitting', 'fittings'],
  IfcFlowTerminal: ['terminal', 'terminals'],
  IfcLightFixture: ['light', 'lights'],
  IfcSanitaryTerminal: ['sanitary fitting', 'sanitary fittings'],
  IfcBuildingElementProxy: ['proxy element', 'proxy elements'],
};

/** The class an IFC2x3 export may write with a suffix: IfcWallStandardCase is an IfcWall. */
export function classBase(ifcClass) {
  return String(ifcClass).replace(/(StandardCase|ElementedCase)$/, '');
}

/** "21 walls", "1 wall"; null for a class without a noun here. */
export function counted(count, ifcClass) {
  const noun = NOUNS[classBase(ifcClass)];
  if (!noun) return null;
  return `${number(count)} ${count === 1 ? noun[0] : noun[1]}`;
}

/** "walls" for IfcWall; "IfcFlowController elements" for a class without a noun. */
export function plural(ifcClass) {
  if (!ifcClass) return 'elements';
  const noun = NOUNS[classBase(ifcClass)];
  return noun ? noun[1] : `${ifcClass} elements`;
}

const GROUPS = {
  revit_type: 'Revit type',
  revit_family: 'Revit family',
  storey: 'storey',
  storey_name: 'storey',
  ifc_class: 'class',
  material: 'material',
};

const METRICS = { count: 'count', length_m: 'length', area_m2: 'area', volume_m3: 'volume' };

function list(values) {
  const words = values.map(String);
  if (words.length < 2) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`;
}

function groupWords(value) {
  const keys = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
  return list(keys.map((key) => (Object.hasOwn(GROUPS, key) ? GROUPS[key] : key)));
}

const quoted = (value) => `“${value}”`;

/**
 * A call in a plain sentence, from its tool and arguments: "Found walls on Level 1". The
 * server sees Claude's calls, not the question typed into Claude, so this is what the
 * local page can say of a question.
 */
export function sentence(entry) {
  const a = entry.arguments ?? {};
  switch (entry.tool) {
    case 'list_models':
      return 'Listed the models';
    case 'list_storeys':
      return 'Listed the storeys';
    case 'find_elements': {
      const parts = [`Found ${plural(a.ifc_class)}`];
      if (a.revit_family) parts.push(`of family ${quoted(a.revit_family)}`);
      if (a.revit_type) parts.push(`of type ${quoted(a.revit_type)}`);
      if (a.name_contains) parts.push(`named like ${quoted(a.name_contains)}`);
      if (a.storey_name) parts.push(`on ${a.storey_name}`);
      return parts.join(' ');
    }
    case 'schedule': {
      const on = a.storey_name ? ` on ${a.storey_name}` : '';
      return `Scheduled the ${plural(a.ifc_class)} by ${groupWords(a.group_by)}${on}`;
    }
    case 'quantity_takeoff': {
      const metric = Object.hasOwn(METRICS, a.metric) ? METRICS[a.metric] : a.metric;
      return `Totalled the ${metric} by ${groupWords(a.group_by)}`;
    }
    case 'get_element':
      return 'Looked up one element';
    case 'space_schedule':
      return 'Listed the rooms and their areas';
    case 'data_quality_report':
      return a.rule_id ? `Checked the rule ${a.rule_id}` : 'Read the data quality report';
    case 'compare_models':
      return a.old_model_id === null || a.old_model_id === undefined
        ? `Compared model ${a.new_model_id} with the export it replaces`
        : `Compared model ${a.new_model_id} with model ${a.old_model_id}`;
    case 'describe_schema':
      return a.relation ? `Described ${a.relation}` : 'Described the database';
    case 'run_sql':
      return 'Ran a query of its own';
    default:
      return TITLES[entry.tool] ?? String(entry.tool);
  }
}

/** How a call ended, in words, for one that did not end ok. */
export const OUTCOMES = {
  invalid: 'Arguments refused',
  refused: 'Refused',
  timeout: 'Timed out',
  database_error: 'Refused by the database',
  unavailable: 'Database unreachable',
  error: 'Failed',
};

/** The line beside "How it was answered": "1 SQL query, read-only", "Find elements, read-only". */
export function howLine(entry) {
  const ended = entry.outcome === 'ok' ? 'read-only' : (OUTCOMES[entry.outcome] ?? entry.outcome).toLowerCase();
  const what = entry.tool === 'run_sql' ? '1 SQL query' : (TITLES[entry.tool] ?? entry.tool);
  return `${what}, ${ended}`;
}

const ARGUMENTS = {
  model_id: 'Model',
  new_model_id: 'Newer model',
  old_model_id: 'Older model',
  object_id: 'Object',
  global_id: 'GlobalId',
  ifc_class: 'Class',
  storey_name: 'Storey',
  revit_family: 'Revit family',
  revit_type: 'Revit type',
  name_contains: 'Name contains',
  group_by: 'Grouped by',
  fields: 'Fields',
  metric: 'Totals',
  include_openings: 'Openings counted',
  rule_id: 'Rule',
  severity: 'Severity',
  scope: 'Scope',
  relation: 'Table or view',
  include_replaced: 'Replaced models too',
  samples_per_rule: 'Examples per rule',
  samples_per_change: 'Examples per change',
  max_rows: 'At most rows',
  limit: 'At most',
  offset: 'Skipping',
};

/** A call's arguments as [label, value] pairs, in words; the unset ones left out. */
export function argumentWords(entry) {
  const pairs = [];
  for (const [name, value] of Object.entries(entry.arguments ?? {})) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value) && value.length === 0) continue;
    const label = Object.hasOwn(ARGUMENTS, name) ? ARGUMENTS[name] : name;
    let text;
    if (typeof value === 'boolean') text = value ? 'yes' : 'no';
    else if (name === 'group_by') text = groupWords(value);
    else if (name === 'metric' && Object.hasOwn(METRICS, value)) text = METRICS[value];
    else if (Array.isArray(value)) text = value.map(String).join(', ');
    else if (typeof value === 'object') text = JSON.stringify(value);
    else text = String(value);
    if ((entry.clipped ?? []).includes(name)) text += ' (clipped for the feed)';
    pairs.push([label, text]);
  }
  return pairs;
}

/**
 * The big line of the local app's answer card, from the call and what it lit: "21 walls"
 * when every object lit is drawn and of one class, else "21 found". Null when the call
 * lights nothing by nature.
 * @param {object} entry
 * @param {{drawn: number, classes: Set<string>}} lit What the view drew of it, and their classes.
 */
export function headline(entry, lit) {
  if (entry.outcome !== 'ok') return OUTCOMES[entry.outcome] ?? 'Failed';
  const found = entry.selection;
  if (!found) return null;
  if (found.count === 0) return 'Nothing found';
  const bases = new Set([...lit.classes].map(classBase));
  if (lit.drawn === found.count && bases.size === 1) {
    const named = counted(found.count, [...bases][0]);
    if (named) return named;
  }
  return `${number(found.count)} found`;
}

/** Storeys in a list: "Level 2 and Roof". */
export function listed(names) {
  return list(names);
}
