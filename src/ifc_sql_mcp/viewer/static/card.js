// The answer card's current answer: the question, the answer in words, the legend of its
// groups, and how it was answered. In the demos the question and the answer are the
// script's; in the local app the page has only Claude's call, so the question is the call
// in a sentence and the big line its count (words.js). Everything here is text from the
// network, set with textContent (dom.js).

import { el, number } from './dom.js';
import { OUTCOMES, TITLES, argumentWords, headline, howLine, listed, sentence } from './words.js';

/**
 * Fill `root` with one answer.
 * @param {HTMLElement} root
 * @param {object} a
 * @param {object|null} a.entry The call shown, or null before the first.
 * @param {'demo'|'scripted'|'local'} a.mode
 * @param {string} a.empty What the card says before the first call.
 * @param {boolean} a.lit The view shows this call's answer now.
 * @param {{drawn: number, classes: Set<string>}} a.drawn What the view drew of it.
 * @param {string[]} a.hidden The storeys hidden above it.
 * @param {number|null} a.group The legend's group shown alone, if one.
 * @param {boolean} a.howOpen "How it was answered" is open.
 * @param {number[]} a.elsewhere Models of the answer that are not shown.
 * @param {{index: number, total: number, previous: boolean, next: boolean}|null} a.stepper
 * @param {object} on The card's buttons: previous, next, group(index), relight, showAll,
 *   and how(open) when "How it was answered" opens or closes. Each button has a data-key,
 *   so that the page can give the focus back to its replacement.
 */
export function renderCurrent(root, a, on) {
  const { entry } = a;
  if (!entry) {
    root.replaceChildren(el('p', { className: 'intro', text: a.empty }));
    return;
  }
  const step = a.mode !== 'local' ? entry.step : null;
  const found = entry.selection;
  const parts = [question(entry, step, a.stepper, on)];

  const big = step ? step.headline : headline(entry, a.drawn);
  if (big) {
    const detail = step ? step.detail : '';
    parts.push(el('p', { className: 'big' }, big, detail ? el('small', { text: detail }) : null));
  } else if (!found) {
    parts.push(el('p', { className: 'note', text: 'Nothing to light: this call finds no objects, so the view stays as it was.' }));
  }
  if (!step && entry.outcome !== 'ok' && found?.note) parts.push(el('p', { className: 'note', text: found.note }));

  if (found && found.count > 0 && !a.lit) {
    const again = el('button', { className: 'link', text: 'Light it again', attrs: { type: 'button', 'data-key': 'relight' } });
    again.addEventListener('click', on.relight);
    parts.push(el('p', { className: 'stale' }, el('span', { text: 'This answer is not lit now.' }), again));
  }
  if (found && a.lit) {
    const legend = legendOf(found, a.group, on);
    if (legend) parts.push(legend);
  }
  if (a.lit && a.hidden.length) {
    const all = el('button', { className: 'link', text: 'Show all', attrs: { type: 'button', 'data-key': 'show-all' } });
    all.addEventListener('click', on.showAll);
    parts.push(el('p', { className: 'note' }, `${listed(a.hidden)} hidden · `, all));
  }
  parts.push(how(entry, a, on));
  root.replaceChildren(...parts);
}

function question(entry, step, stepper, on) {
  const line = el(
    'div',
    { className: 'qline' },
    el('span', { className: 'num', text: step ? step.index : entry.id, attrs: { 'aria-hidden': 'true' } }),
    el('h2', { className: 'q', text: step ? step.title : sentence(entry) }),
  );
  if (step && stepper) {
    const previous = el('button', { text: '‹', attrs: { type: 'button', 'aria-label': 'The question before', 'data-key': 'previous' } });
    const next = el('button', { text: '›', attrs: { type: 'button', 'aria-label': 'The question after', 'data-key': 'next' } });
    previous.disabled = !stepper.previous;
    next.disabled = !stepper.next;
    previous.addEventListener('click', on.previous);
    next.addEventListener('click', on.next);
    line.append(
      el('span', { className: 'stepper' }, previous, el('span', { text: `${stepper.index}/${stepper.total}`, attrs: { 'aria-label': `question ${stepper.index} of ${stepper.total}` } }), next),
    );
  }
  return line;
}

/** The legend of the groups, each a button that shows only its group; null for one plain group. */
function legendOf(found, alone, on) {
  const groups = found.groups;
  if (!(groups.length > 1 || (groups.length === 1 && groups[0].label))) return null;
  return el(
    'ul',
    { className: 'legend', attrs: { 'aria-label': 'What the answer lights, by group' } },
    ...groups.map((group, index) => {
      const swatch = el('span', { className: 'sw', attrs: { 'aria-hidden': 'true' } });
      swatch.style.setProperty('--swatch', group.colour);
      const button = el(
        'button',
        { attrs: { type: 'button', 'aria-pressed': String(alone === index), title: 'Show only this group', 'data-key': `group-${index}` } },
        swatch,
        el('span', { className: 't', text: group.label ?? '(none)' }),
        el('span', { className: 'c', text: number(group.count) }),
      );
      button.addEventListener('click', () => on.group(index));
      return el('li', {}, button);
    }),
  );
}

/** "How it was answered": the tool and its arguments in words, the SQL, the counts. */
function how(entry, a, on) {
  const found = entry.selection;
  const body = el('div', { className: 'how-body' });
  const facts = [['Tool', `${TITLES[entry.tool] ?? entry.tool} (${entry.tool})`], ...argumentWords(entry)];
  if (entry.outcome !== 'ok') facts.push(['Ended', OUTCOMES[entry.outcome] ?? entry.outcome]);
  if (entry.rows !== null && entry.rows !== undefined) facts.push(['Rows', number(entry.rows)]);
  body.append(factList(facts));
  if (entry.sql) {
    const clipped = entry.sql.text.length < entry.sql.chars;
    const note = clipped
      ? `The query, ${number(entry.sql.chars)} characters; the feed carries its first ${number(entry.sql.text.length)}:`
      : `The query, ${number(entry.sql.chars)} characters:`;
    body.append(el('p', { text: note }), el('pre', { className: 'sql', text: entry.sql.text }));
  }
  if (found) {
    const counts = [`${number(found.count)} found`];
    if (a.lit) {
      const without = Math.max(found.count - a.drawn.drawn - found.openings - found.not_drawn, 0);
      counts.push(`${number(a.drawn.drawn)} drawn`, `${number(without)} without shape`);
    }
    if (found.openings) counts.push(`${number(found.openings)} openings, never drawn`);
    if (found.not_drawn) counts.push(`${number(found.not_drawn)} storeys or buildings, not drawn`);
    if (found.truncated) counts.push('the first 20,000 lit');
    body.append(el('p', { text: counts.join(' · ') }));
    if (found.note && !(entry.outcome !== 'ok' && a.mode === 'local')) body.append(el('p', { text: found.note }));
  }
  if (a.elsewhere.length) {
    const models = a.elsewhere.length === 1 ? `Model ${a.elsewhere[0]} is` : `Models ${listed(a.elsewhere)} are`;
    body.append(el('p', { text: `${models} not shown here: the Model panel picks the project.` }));
  }
  if (a.mode === 'local' || !entry.step) body.append(el('p', { className: 'muted', text: `Asked through ${entry.source}.` }));
  const details = el(
    'details',
    { className: 'how' },
    el('summary', { attrs: { 'data-key': 'how' } }, el('span', { text: 'How it was answered' }), el('span', { className: 'muted', text: howLine(entry) })),
    body,
  );
  details.open = a.howOpen;
  details.addEventListener('toggle', () => on.how(details.open));
  return details;
}

function factList(pairs) {
  const list = el('dl', { className: 'facts' });
  for (const [label, value] of pairs) list.append(el('dt', { text: label }), el('dd', { text: value }));
  return list;
}
