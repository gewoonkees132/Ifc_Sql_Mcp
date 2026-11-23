// The page's one seam for data. The live source reads `ifc-sql view`'s routes with the
// token, and its feed of calls as server-sent events; Phase 5 adds a static source that
// reads exported files instead.

const TOKEN_KEY = 'ifc-sql-view-token';

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
