export const GRAPH_STUDIO_SYNC_KEY = 'graphStudioUpdatedAt';

export const markGraphStudioUpdated = () => {
    try {
        localStorage.setItem(GRAPH_STUDIO_SYNC_KEY, String(Date.now()));
    } catch {
        // ignore local storage errors
    }
};

export const readGraphStudioUpdateMarker = () => {
    try {
        return localStorage.getItem(GRAPH_STUDIO_SYNC_KEY) || '';
    } catch {
        return '';
    }
};
