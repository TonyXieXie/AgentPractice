import { WebviewWindow } from '@tauri-apps/api/webviewWindow';

export const GRAPH_STUDIO_WINDOW_LABEL = 'graph-studio';
const GRAPH_STUDIO_BOUNDS_KEY = 'graphStudioWindowBounds';
const GRAPH_STUDIO_DEFAULT_WIDTH = 1480;
const GRAPH_STUDIO_DEFAULT_HEIGHT = 920;

type GraphStudioBounds = {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
};

const getGraphStudioWindowBounds = (): GraphStudioBounds | null => {
    try {
        const raw = localStorage.getItem(GRAPH_STUDIO_BOUNDS_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw) as Partial<GraphStudioBounds> | null;
        if (!parsed || typeof parsed !== 'object') return null;
        const next: GraphStudioBounds = {};
        if (Number.isFinite(parsed.width)) next.width = Math.max(1100, Math.round(parsed.width as number));
        if (Number.isFinite(parsed.height)) next.height = Math.max(720, Math.round(parsed.height as number));
        if (Number.isFinite(parsed.x)) next.x = Math.round(parsed.x as number);
        if (Number.isFinite(parsed.y)) next.y = Math.round(parsed.y as number);
        return next;
    } catch {
        return null;
    }
};

export async function openGraphStudioWindow() {
    const existing = await WebviewWindow.getByLabel(GRAPH_STUDIO_WINDOW_LABEL);
    if (existing) {
        try {
            await existing.show();
            await existing.setFocus();
        } catch {
            // ignore focus errors
        }
        return;
    }

    const bounds = getGraphStudioWindowBounds();
    const win = new WebviewWindow(GRAPH_STUDIO_WINDOW_LABEL, {
        title: 'Graph Studio',
        url: '/?window=graph-studio',
        width: bounds?.width ?? GRAPH_STUDIO_DEFAULT_WIDTH,
        height: bounds?.height ?? GRAPH_STUDIO_DEFAULT_HEIGHT,
        x: bounds?.x,
        y: bounds?.y,
        decorations: true,
        resizable: true,
    });

    win.once('tauri://error', (event) => {
        console.error('Failed to create graph studio window:', event);
    });
}
