import type { DebugFocusRequest, LLMCall } from './types';

type NormalizedLLMCallDebugInfo = {
    graphRunId: string;
    graphId: string;
    nodeId: string;
    nodeType: string;
    profileId: string;
};

const isRecord = (value: unknown): value is Record<string, any> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);

const readString = (value: unknown) => {
    if (typeof value !== 'string') return '';
    const trimmed = value.trim();
    return trimmed || '';
};

const readFromSources = (sources: Array<Record<string, any> | null | undefined>, keys: string[]) => {
    for (const source of sources) {
        if (!isRecord(source)) continue;
        for (const key of keys) {
            const value = readString(source[key]);
            if (value) return value;
        }
    }
    return '';
};

export const getLLMCallDebugInfo = (call: LLMCall): NormalizedLLMCallDebugInfo => {
    const requestJson = isRecord(call.request_json) ? call.request_json : null;
    const processedJson = isRecord(call.processed_json) ? call.processed_json : null;
    const sources: Array<Record<string, any> | null | undefined> = [
        isRecord(call.debug) ? (call.debug as Record<string, any>) : null,
        isRecord(processedJson?._debug) ? (processedJson?._debug as Record<string, any>) : null,
        isRecord(requestJson?._debug) ? (requestJson?._debug as Record<string, any>) : null,
        processedJson,
        requestJson,
    ];

    return {
        graphRunId: readFromSources(sources, ['graphRunId', 'graph_run_id']),
        graphId: readFromSources(sources, ['graphId', 'graph_id']),
        nodeId: readFromSources(sources, ['nodeId', 'node_id']),
        nodeType: readFromSources(sources, ['nodeType', 'node_type']),
        profileId: readFromSources(sources, ['profileId', 'profile_id']),
    };
};

export const resolveLLMCallFocusTarget = (
    llmCalls: LLMCall[],
    target?: DebugFocusRequest | null
): LLMCall | undefined => {
    if (!target) return undefined;

    if (typeof target.callId === 'number') {
        return llmCalls.find((item) => item.id === target.callId);
    }

    let candidates = llmCalls.filter((item) => {
        if (typeof target.messageId === 'number' && item.message_id !== target.messageId) {
            return false;
        }
        if (typeof target.iteration === 'number' && item.iteration !== target.iteration) {
            return false;
        }
        return true;
    });

    if (!candidates.length) return undefined;

    const narrowBy = (field: keyof NormalizedLLMCallDebugInfo, expected?: string) => {
        if (!expected) return;
        const matches = candidates.filter((item) => getLLMCallDebugInfo(item)[field] === expected);
        if (matches.length > 0) {
            candidates = matches;
        }
    };

    narrowBy('graphRunId', target.graphRunId);
    narrowBy('nodeId', target.nodeId);
    narrowBy('profileId', target.profileId);
    narrowBy('graphId', target.graphId);
    narrowBy('nodeType', target.nodeType);

    if (typeof target.occurrenceIndex === 'number' && target.occurrenceIndex >= 0) {
        const indexed = candidates[target.occurrenceIndex];
        if (indexed) {
            return indexed;
        }
    }

    return candidates[0];
};
