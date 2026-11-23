// The one way the page makes elements. Text from an IFC file, or from the server, goes in
// as text nodes and textContent, never as HTML (ADR 14, decision 3).

/**
 * Make an element.
 * @param {string} tag
 * @param {{className?: string, text?: unknown, title?: string, attrs?: Record<string, string>}} [options]
 * @param {...(Node|string|null|undefined)} children Strings become text nodes.
 * @returns {HTMLElement}
 */
export function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
  if (options.title) node.title = options.title;
  for (const [name, value] of Object.entries(options.attrs ?? {})) {
    // An event handler is code: the page adds them with addEventListener, never as attributes.
    if (/^on/i.test(name)) throw new Error(`refusing the attribute ${name}`);
    node.setAttribute(name, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

/** Format a number for reading: grouped, with at most `digits` decimals, at least `fixed`. */
export function number(value, digits = 3, fixed = 0) {
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: fixed, maximumFractionDigits: digits });
}

/** An elevation or a level, in metres to the millimetre, as list_storeys gives them. */
export function metres(value) {
  return `${number(value, 3, 3)} m`;
}
