// The feed: every MCP call that ifc-sql serve published, and what each one found
// (ADR 14, decision 4). The server checks and resolves every call; this module lists
// them, says whether calls arrive, and hands the page a call's selection to light.
// Everything in a call is text from the network: it is set with textContent only.

import { el, number } from './dom.js';

const KEEP = 200;

const STATES = {
  off: 'off',
  connecting: 'connecting…',
  live: 'live',
  interrupted: 'interrupted',
};

/** The time of an ISO 8601 instant, as hh:mm:ss in the browser's own zone. */
function clock(iso) {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString(undefined, { hour12: false });
}

/** The count of a call's selection, as the list shows it: "21", "–" for nothing. */
export function lit(entry) {
  if (!entry.selection) return '–';
  return number(entry.selection.count);
}

export class FeedPanel {
  /**
   * @param {{state: HTMLElement, note: HTMLElement, list: HTMLElement}} nodes
   * @param {(entry: object) => void} onApply Lights a call's selection.
   */
  constructor(nodes, onApply) {
    this.nodes = nodes;
    this.onApply = onApply;
    this.entries = new Map(); // id → entry
    this.status = { state: 'off', received: 0, dropped: {}, demo: false, database: null };
    this.applied = null;
    this.connected = false;
    this.render();
  }

  /** Listen to the server's stream of calls. */
  listen(api) {
    this.source = api.events({
      status: (status) => {
        this.status = status;
        this.render();
      },
      call: (entry) => this.add(entry),
      error: (closed) => {
        this.connected = !closed;
        if (closed) this.status = { ...this.status, state: 'off', message: 'the page lost the server: reload it, or open the URL ifc-sql view printed' };
        this.render();
      },
      open: () => {
        this.connected = true;
      },
    });
  }

  add(entry) {
    this.entries.set(entry.id, entry);
    while (this.entries.size > KEEP) this.entries.delete(this.entries.keys().next().value);
    this.render();
    // The newest call is lit as it arrives; a call that lights nothing leaves the view.
    if (entry.selection) this.apply(entry);
  }

  apply(entry) {
    this.applied = entry.id;
    this.onApply(entry);
    this.render();
  }

  render() {
    const { state, note, list } = this.nodes;
    const status = this.status;
    state.textContent = STATES[status.state] ?? status.state;
    state.dataset.state = status.state;
    const parts = [];
    if (status.demo) parts.push('Scripted demo, no LLM: ifc-sql demo asks through ifc-sql serve.');
    if (status.state === 'interrupted' || status.state === 'off') parts.push(status.message ?? '');
    if (status.state === 'live' && this.entries.size === 0) {
      parts.push(
        `Listening on ${status.database ?? 'the database'}. No call yet: run an MCP client whose ifc-sql serve has IFC_SQL_VIEWER_FEED=1 and the same database, or ifc-sql demo.`,
      );
    }
    const dropped = Object.values(status.dropped ?? {}).reduce((sum, n) => sum + n, 0);
    if (dropped) parts.push(`${number(dropped)} notifications refused: not calls of ifc-sql serve.`);
    note.textContent = parts.filter(Boolean).join(' ');
    note.hidden = note.textContent === '';

    const rows = [...this.entries.values()].reverse().map((entry) => this.row(entry));
    list.replaceChildren(...rows);
  }

  row(entry) {
    const failed = entry.outcome !== 'ok';
    const button = el(
      'button',
      {
        className: `call${entry.id === this.applied ? ' applied' : ''}${failed ? ' failed' : ''}`,
        attrs: { type: 'button', 'aria-pressed': String(entry.id === this.applied) },
        title: entry.source,
      },
      el('span', { className: 'when', text: clock(entry.received_at) }),
      el('span', { className: 'what', text: entry.step ? `${entry.step.index}. ${entry.step.title}` : entry.summary }),
      el('span', { className: 'count', text: failed ? entry.outcome : lit(entry) }),
    );
    button.disabled = !entry.selection;
    button.addEventListener('click', () => this.apply(entry));
    const item = el('li', {}, button);
    if (entry.sql) {
      const sql = el('details', { className: 'sql' }, el('summary', { text: `SQL, ${number(entry.sql.chars)} characters` }), el('pre', { text: entry.sql.text }));
      item.append(sql);
    }
    return item;
  }
}
