// The page's one seam for data, with two sources. The live source reads `ifc-sql view`'s
// routes with the token, and its feed of calls as server-sent events. The static source
// reads the files `ifc-sql demo --export` wrote beside the page, and plays the recorded
// calls back a pause apart (ADR 14, decision 7). Both give the page the same objects.

const TOKEN_KEY = 'ifc-sql-view-token';

/** Seconds from the models drawn to the first call played back. */
const FIRST_CALL_S = 1.5;

/** True on the static copy: `ifc-sql demo --export` marks its page so. */
export function isStatic() {
  return document.documentElement.dataset.source === 'static';
}

/** An error from the server, with its status and the message its JSON body gave. */
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * Take the token from the URL's fragment (`#t=…`) into sessionStorage, and drop the
 * fragment from the address bar and the history. A fragment never reaches the server, a
 * log or a Referer; sessionStorage is per tab and per origin, port included.
 * @returns {string|null}
 */
export function takeToken() {
  const match = /^#t=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
  if (match) {
    sessionStorage.setItem(TOKEN_KEY, match[1]);
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  return sessionStorage.getItem(TOKEN_KEY);
}

/** The live source: `fetch` with `Authorization: Bearer`, ids through encodeURIComponent. */
export function liveSource(token) {
  async function get(path) {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        message = (await response.json()).error ?? message;
      } catch {
        // not JSON: keep the status line
      }
      throw new ApiError(response.status, message);
    }
    return response;
  }
  const model = (id) => `/api/models/${encodeURIComponent(id)}`;
  return {
    models: () => get('/api/models').then((r) => r.json()),
    scene: (modelId) => get(`${model(modelId)}/scene`).then((r) => r.json()),
    // The chunk's ETag is its SHA-256: a reload revalidates it and gets a 304.
    chunk: (modelId, chunkNo) =>
      get(`${model(modelId)}/scene/${encodeURIComponent(chunkNo)}.glb`).then((r) => r.arrayBuffer()),
    element: (modelId, globalId) =>
      get(`${model(modelId)}/objects/${encodeURIComponent(globalId)}`).then((r) => r.json()),
    feed: () => get('/api/feed').then((r) => r.json()),
    /**
     * The calls as they arrive. EventSource cannot send a header, so this one route takes
     * the token in its query; the browser resumes with Last-Event-ID by itself.
     */
    events(handlers) {
      const source = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
      source.addEventListener('open', () => handlers.open());
      source.addEventListener('status', (event) => handlers.status(JSON.parse(event.data)));
      source.addEventListener('call', (event) => handlers.call(JSON.parse(event.data)));
      source.addEventListener('error', () => handlers.error(source.readyState === EventSource.CLOSED));
      return source;
    },
  };
}

/**
 * The static source: the files `ifc-sql demo --export` wrote under `data/`, beside the
 * page, by relative paths, so that the copy works below any folder (GitHub Pages serves
 * it below the repository's name). The file names are ids; ids through encodeURIComponent.
 */
export function staticSource() {
  async function get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new ApiError(response.status, `${path}: ${response.status} ${response.statusText}`);
    return response;
  }
  const json = (path) => get(path).then((r) => r.json());
  // Each read once, when first needed.
  const once = (read) => {
    let promise = null;
    return () => (promise ??= read());
  };
  const recording = once(() => json('data/events.json'));
  const elements = once(() => json('data/elements.json'));
  const details = once(() => json('data/details.json'));
  const scene = (modelId) => `data/scenes/${encodeURIComponent(modelId)}`;
  const own = (object, key) => (object && Object.hasOwn(object, key) ? object[key] : undefined);
  return {
    models: () => json('data/models.json'),
    scene: (modelId) => json(`${scene(modelId)}.json`),
    chunk: (modelId, chunkNo) => get(`${scene(modelId)}/${encodeURIComponent(chunkNo)}.glb`).then((r) => r.arrayBuffer()),
    /**
     * `get_element` for the objects the calls looked up; for any other a brief record,
     * its class, name and storey, with the note that says why.
     */
    async element(modelId, globalId) {
      const full = own(own((await details()).models, modelId), globalId);
      if (full) return full;
      const all = await elements();
      const model = own(all.models, modelId);
      const brief = own(model?.objects, globalId);
      if (!brief) throw new ApiError(404, `this copy holds no object ${globalId} of model ${modelId}`);
      return { ...brief, brief: true, note: all.note, model_id: Number(modelId), discipline: model.discipline, global_id: globalId };
    },
    /** The recording's title and credit, for the sidebar. */
    about: () => recording().then(({ title, credit }) => ({ title, credit })),
    feed: () => recording().then(({ status, entries }) => ({ status, entries })),
    /** The recorded calls, played back one every `pause_seconds`, as the demo asked them. */
    events(handlers) {
      const timers = [];
      recording().then(
        ({ status, entries, pause_seconds: pause }) => {
          handlers.open();
          handlers.status(status);
          entries.forEach((entry, index) => {
            timers.push(setTimeout(() => handlers.call(entry), (FIRST_CALL_S + index * pause) * 1000));
          });
        },
        (error) => handlers.error(true, `the recorded calls could not be read: ${error.message ?? error}`),
      );
      return { close: () => timers.forEach(clearTimeout) };
    },
  };
}
