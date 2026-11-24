// Ask Claude: one panel that changes shape. Closed, the answer card at the top left and
// the Ask Claude button at the bottom left; open, the card grows down to the button, the
// button becomes the question box, and the history slides in above the answer (the CSS
// does the motion, on body.asking). On a phone it is a sheet with the box at the bottom.
//
// How the box behaves depends on where the page runs (main.js tells it the mode):
// - demo, the static copy: it offers the nine recorded questions; typing filters them;
// - scripted, ifc-sql demo: the script's questions, those not asked yet "coming up";
// - local, ifc-sql view: no questions; the box says to ask in Claude Desktop or Code.
// Claude does not answer on this page in any of them, and the page says so.

import { el, number } from './dom.js';
import { OUTCOMES, sentence } from './words.js';

const $ = (id) => document.getElementById(id);

/** The feed's state in words, for the local app's header line. */
const LISTENING = {
  live: 'Listening for Claude',
  connecting: 'Connecting…',
  interrupted: 'Connection lost, retrying',
  off: 'Off',
  recorded: 'Recorded',
};

const SUGGESTIONS = 2;

export class AskPanel {
  /**
   * @param {object} on What the panel asks of the page: pick(stepIndex), show(entry),
   *   help(section), toggled(open).
   */
  constructor(on) {
    this.on = on;
    this.isOpen = false;
    this.listOpen = false;
    this.active = -1; // the option the arrows are on
    this.matches = [];
    this.phone = window.matchMedia('(max-width: 759px)');
    this.panel = $('panel');
    this.input = $('question');

    $('ask-open').addEventListener('click', () => this.open());
    $('ask-close').addEventListener('click', () => this.close());
    $('list-toggle').addEventListener('click', () => (this.listOpen ? this.closeList() : this.openList(true)));
    $('send').addEventListener('click', () => this.ask());
    $('own-more').addEventListener('click', () => on.help('own'));
    this.input.addEventListener('input', () => {
      this.active = -1;
      this.openList(false);
      this.render();
    });
    this.input.addEventListener('keydown', (event) => this.keydown(event));
    this.input.addEventListener('blur', () => {
      // A click on an option comes after the blur: close the list once it is handled.
      setTimeout(() => {
        if (!this.panel.contains(document.activeElement)) this.closeList();
      }, 150);
    });
    this.earlier();
  }

  /** Open the panel: the card and the button merge. */
  open() {
    if (this.isOpen) return;
    this.isOpen = true;
    document.body.classList.add('asking');
    $('ask-open').setAttribute('aria-expanded', 'true');
    this.on.toggled(true);
    // After the box fades in; with reduced motion at once.
    setTimeout(() => (this.input.disabled ? $('ask-close') : this.input).focus({ preventScroll: true }), 200);
  }

  /** Close it: the two split again. */
  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.closeList();
    document.body.classList.remove('asking');
    $('ask-open').setAttribute('aria-expanded', 'false');
    this.on.toggled(false);
    if (!this.phone.matches) $('ask-open').focus({ preventScroll: true });
  }

  /** Esc: the list first, then the panel. True when it closed something. */
  escape() {
    if (this.listOpen) {
      this.closeList();
      this.input.focus();
      return true;
    }
    if (this.isOpen && !this.phone.matches) {
      this.close();
      return true;
    }
    return false;
  }

  /**
   * Show the state of the page.
   * @param {object} s
   * @param {'demo'|'scripted'|'local'} s.mode
   * @param {object|null} s.status The feed's status.
   * @param {object[]} s.questions The script's questions (DemoStep), in order.
   * @param {Map<number, object>} s.byStep The recorded or played call of each question.
   * @param {object[]} s.history The calls asked, in order, the current one left out.
   * @param {Set<number>} s.asked The questions asked.
   * @param {object|null} s.current The call the card shows.
   */
  render(s = this.last) {
    if (!s) return;
    this.last = s;
    const { mode } = s;
    const total = s.questions.length;
    const local = mode === 'local';

    // The header's second line, open.
    let state;
    if (mode === 'demo') state = `Ask Claude · ${s.asked.size} of ${total} asked`;
    else if (mode === 'scripted') state = 'Scripted demo · no LLM';
    else state = LISTENING[s.status?.state] ?? 'Connecting…';
    $('ask-state').textContent = state;

    // The history.
    $('history').replaceChildren(...s.history.map((entry) => this.row(entry, mode)));
    this.earlier(s.history.length);

    // The box.
    this.input.disabled = local;
    this.input.placeholder = local ? 'Ask Claude in Claude Desktop or Claude Code' : 'Ask about this model…';
    $('list-toggle').hidden = local;
    $('send').hidden = local;
    $('list-count').textContent = `${total} question${total === 1 ? '' : 's'}`;
    $('send').disabled = local || this.input.value.trim() === '';
    $('question-list-title').textContent = mode === 'scripted' ? 'Questions this script asks' : 'Questions this demo can answer';

    // Next: questions not asked yet, in the demo.
    const next = mode === 'demo' ? s.questions.filter((q) => !s.asked.has(q.index)).slice(0, SUGGESTIONS) : [];
    $('next').replaceChildren(
      ...(next.length ? [el('span', { className: 'muted', text: 'Next:' })] : []),
      ...next.map((q) => {
        const chip = el('button', { className: 'chip', text: q.title, attrs: { type: 'button', 'data-key': `chip-${q.index}` } });
        chip.addEventListener('click', () => this.on.pick(q.index));
        return chip;
      }),
    );

    // The line below the box.
    const badge = $('badge');
    badge.dataset.state = local ? (s.status?.state ?? 'connecting') : 'demo';
    if (mode === 'demo') {
      badge.replaceChildren("Recorded answers · Claude doesn't run on this page.");
    } else if (mode === 'scripted') {
      badge.replaceChildren("Asked by ifc-sql demo through the real tools · Claude doesn't run on this page.");
    } else {
      const connect = el('button', { className: 'link', text: 'How to connect ›', attrs: { type: 'button', 'data-key': 'connect' } });
      connect.addEventListener('click', () => this.on.help('connect'));
      const problem = ['off', 'interrupted'].includes(s.status?.state) && s.status?.message ? `${s.status.message} · ` : '';
      badge.replaceChildren(problem, connect);
    }
    $('ask-note').hidden = mode !== 'demo';

    this.renderList(s);
  }

  /** One row of the history: number, question and answer in one line. */
  row(entry, mode) {
    const step = mode !== 'local' ? entry.step : null;
    const failed = entry.outcome !== 'ok';
    let answer;
    if (step) {
      answer = el('span', { className: 'a', text: step.answer });
    } else {
      const count = failed ? (OUTCOMES[entry.outcome] ?? entry.outcome) : entry.selection ? `${number(entry.selection.count)} found` : 'lights nothing';
      answer = el('span', { className: 'a' }, el('span', { className: 'tool', text: entry.tool }), ` · ${count}`);
    }
    const button = el(
      'button',
      { className: `past${failed && !step ? ' failed' : ''}`, attrs: { type: 'button', 'data-key': `row-${entry.id}` } },
      el('span', { className: 'num', text: step ? step.index : entry.id, attrs: { 'aria-hidden': 'true' } }),
      el('span', { className: 'past-text' }, el('span', { className: 'q', text: step ? step.title : sentence(entry) }), answer),
    );
    // A call that lights nothing by nature has nothing to light again, in the local app.
    button.disabled = mode === 'local' && !entry.selection;
    button.addEventListener('click', () => this.on.show(entry));
    return el('li', {}, button);
  }

  /** The phone's "▴ 2 earlier questions", which shows the history above the answer. */
  earlier(count = 0) {
    const button = $('earlier');
    const up = this.panel.classList.contains('up');
    button.textContent = count === 0 ? '' : up ? '▾ Hide the earlier questions' : `▴ ${count} earlier question${count === 1 ? '' : 's'}`;
    button.setAttribute('aria-expanded', String(up));
    if (!this.earlierWired) {
      this.earlierWired = true;
      let from = null;
      let dragged = false;
      button.addEventListener('pointerdown', (event) => {
        from = event.clientY;
        dragged = false;
      });
      button.addEventListener('pointerup', (event) => {
        if (from === null) return;
        const dy = event.clientY - from;
        from = null;
        if (Math.abs(dy) < 24) return;
        dragged = true;
        this.sheet(dy < 0);
      });
      button.addEventListener('click', () => {
        if (dragged) {
          dragged = false;
          return;
        }
        this.sheet(!this.panel.classList.contains('up'));
      });
    }
  }

  /** Pull the phone's sheet up, to the history, or down again. */
  sheet(up) {
    if (up && $('history').children.length === 0) up = false;
    this.panel.classList.toggle('up', up);
    this.earlier($('history').children.length);
  }

  // ------------------------------------------------------------------ the list of questions

  openList(fromToggle) {
    const s = this.last;
    if (!s || s.mode === 'local' || this.listOpen) return;
    this.listOpen = true;
    $('question-list').hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
    $('list-toggle').setAttribute('aria-expanded', 'true');
    if (this.phone.matches) {
      const box = $('ask').getBoundingClientRect();
      document.documentElement.style.setProperty('--list-bottom', `${Math.round(window.innerHeight - box.top + 8)}px`);
    }
    if (fromToggle) {
      this.active = -1;
      this.input.focus({ preventScroll: true });
    }
    this.renderList(s);
  }

  closeList() {
    if (!this.listOpen) return;
    this.listOpen = false;
    this.active = -1;
    $('question-list').hidden = true;
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    $('list-toggle').setAttribute('aria-expanded', 'false');
  }

  /** The questions the typed words match: every word in the title, in any order. */
  filtered(s) {
    const words = this.input.value.toLowerCase().split(/\s+/).filter(Boolean);
    return s.questions.filter((q) => words.every((word) => q.title.toLowerCase().includes(word)));
  }

  renderList(s) {
    if (!s || s.mode === 'local') return;
    this.matches = this.filtered(s);
    const typed = this.input.value.trim() !== '';
    const options = this.matches.map((q, position) => {
      const asked = s.asked.has(q.index);
      const coming = s.mode === 'scripted' && !s.byStep.has(q.index);
      const tag = asked ? '✓ asked' : coming ? 'coming up' : '';
      const option = el(
        'li',
        { attrs: { role: 'option', id: `question-${q.index}`, 'aria-selected': String(position === this.active) } },
        el('span', { className: 'num', text: q.index, attrs: { 'aria-hidden': 'true' } }),
        el('span', { text: q.title }),
        tag ? el('span', { className: 'tag', text: tag }) : null,
      );
      if (coming) option.setAttribute('aria-disabled', 'true');
      option.addEventListener('mousedown', (event) => event.preventDefault()); // keep the focus
      option.addEventListener('click', () => {
        if (!coming) this.choose(q.index);
      });
      return option;
    });
    $('question-options').replaceChildren(...options);
    $('question-options').hidden = options.length === 0;
    $('question-list-title').hidden = options.length === 0;
    // A question outside them: how to ask your own.
    $('own').classList.toggle('alone', options.length === 0);
    $('own').hidden = typed && options.length > 0;
    if (this.active >= 0 && this.matches[this.active]) {
      this.input.setAttribute('aria-activedescendant', `question-${this.matches[this.active].index}`);
    } else {
      this.input.removeAttribute('aria-activedescendant');
    }
  }

  keydown(event) {
    const s = this.last;
    if (!s || s.mode === 'local') return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      this.openList(false);
      const pickable = this.matches.map((q, i) => (s.mode === 'scripted' && !s.byStep.has(q.index) ? -1 : i)).filter((i) => i >= 0);
      if (pickable.length === 0) return;
      const at = pickable.indexOf(this.active);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      this.active = pickable[(at + step + pickable.length) % pickable.length] ?? pickable[0];
      this.renderList(s);
      document.getElementById(`question-${this.matches[this.active].index}`)?.scrollIntoView({ block: 'nearest' });
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.ask();
    }
  }

  /** The send button, or Enter: the question the arrows are on, else the first match. */
  ask() {
    const s = this.last;
    if (!s || s.mode === 'local') return;
    const pickable = (q) => !(s.mode === 'scripted' && !s.byStep.has(q.index));
    const chosen = this.matches[this.active] ?? (this.input.value.trim() ? this.matches.find(pickable) : null);
    if (chosen && pickable(chosen)) {
      this.choose(chosen.index);
      return;
    }
    // Nothing matches: the list says how to ask your own.
    this.openList(false);
  }

  choose(index) {
    this.input.value = '';
    this.closeList();
    this.on.pick(index);
    this.render();
  }
}
