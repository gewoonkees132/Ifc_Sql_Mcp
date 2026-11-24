// The "?" explainer: a modal dialog in the shape of the CV site's project pop-up, with two
// tabs, What it does and How it's built. Its text is the page's own (index.html); this
// module opens and closes it, switches the tabs, and shows the demo's or the local app's
// version of the boxes that differ. The dialog keeps the focus inside while it is open,
// Esc closes it, and the focus goes back to the "?" that opened it.

const $ = (id) => document.getElementById(id);

const TABS = ['does', 'built'];

export class Explainer {
  constructor() {
    this.dialog = $('explainer');
    this.opener = null;
    $('explainer-close').addEventListener('click', () => this.dialog.close());
    // A click on the backdrop, outside the dialog's box, closes it too.
    this.dialog.addEventListener('click', (event) => {
      if (event.target === this.dialog) this.dialog.close();
    });
    this.dialog.addEventListener('close', () => {
      this.opener?.focus({ preventScroll: true });
      this.opener = null;
    });
    for (const name of TABS) {
      const tab = $(`tab-${name}`);
      tab.addEventListener('click', () => this.tab(name));
      tab.addEventListener('keydown', (event) => {
        const at = TABS.indexOf(name);
        const to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: TABS.length - 1 }[event.key];
        if (to === undefined) return;
        event.preventDefault();
        const next = TABS[(to + TABS.length) % TABS.length];
        this.tab(next);
        $(`tab-${next}`).focus();
      });
    }
  }

  get open() {
    return this.dialog.open;
  }

  /**
   * Open the explainer.
   * @param {'demo'|'scripted'|'local'} mode Which version of its boxes to show.
   * @param {'connect'|'own'|undefined} section A box to go to: how to connect Claude, or
   *   how to ask about your own models.
   */
  show(mode, section) {
    const demo = mode === 'demo';
    for (const node of this.dialog.querySelectorAll('[data-for]')) node.hidden = (node.dataset.for === 'demo') !== demo;
    this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    this.tab('does');
    if (!this.dialog.open) this.dialog.showModal();
    this.dialog.scrollTop = 0;
    if (section) {
      const box = this.dialog.querySelector(`.last-box[data-for="${demo ? 'demo' : 'local'}"]`);
      box?.scrollIntoView({ block: 'nearest' });
      (box?.querySelector('a') ?? $('tab-does')).focus();
    } else {
      $('tab-does').focus();
    }
  }

  tab(name) {
    for (const other of TABS) {
      const selected = other === name;
      const tab = $(`tab-${other}`);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      $(`tab-${other}-panel`).hidden = !selected;
      $(`hero-${other}`).hidden = !selected;
    }
  }
}
