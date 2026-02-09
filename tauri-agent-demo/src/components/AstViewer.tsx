import { Fragment } from 'react';
import type { AstPayload, AstSymbol, AstNode } from '../types';

type OutlineNode = {
    id: string;
    symbol: AstSymbol;
    children: OutlineNode[];
};

type AstViewerProps = {
    payload: AstPayload;
    expanded?: boolean;
    rawVisible?: boolean;
    onToggleRaw?: () => void;
    onOpenWorkFile?: (filePath: string, line?: number, column?: number) => void;
};

const formatAstRange = (node?: { start?: [number, number]; end?: [number, number] }) => {
    if (!node?.start || !node?.end) return '';
    const [sLine, sCol] = node.start;
    const [eLine, eCol] = node.end;
    if (!sLine || !sCol || !eLine || !eCol) return '';
    return `L${sLine}:${sCol}-L${eLine}:${eCol}`;
};

const formatAstNodeLabel = (node: AstNode) => {
    const parts: string[] = [];
    if (node.type) parts.push(node.type);
    if (node.name) parts.push(node.name);
    if (node.attr) parts.push(`.${node.attr}`);
    if (node.value) parts.push(`= ${node.value}`);
    if (node.text) parts.push(`"${node.text}"`);
    return parts.join(' ');
};

const buildOutlineTree = (symbols: AstSymbol[] = []) => {
    const nodes: OutlineNode[] = symbols.map((symbol, index) => ({
        id: `${symbol.kind || 'symbol'}-${symbol.name || 'anon'}-${index}`,
        symbol,
        children: []
    }));
    const byName = new Map<string, OutlineNode[]>();
    nodes.forEach((node) => {
        const name = node.symbol.name;
        if (!name) return;
        const bucket = byName.get(name) || [];
        bucket.push(node);
        byName.set(name, bucket);
    });
    const roots: OutlineNode[] = [];
    nodes.forEach((node) => {
        const parentName = node.symbol.parent;
        if (parentName) {
            const parentBucket = byName.get(parentName);
            if (parentBucket && parentBucket.length > 0) {
                parentBucket[0].children.push(node);
                return;
            }
        }
        roots.push(node);
    });
    return roots;
};

function AstViewer({
    payload,
    expanded = true,
    rawVisible = false,
    onToggleRaw,
    onOpenWorkFile
}: AstViewerProps) {
    const renderAstImports = (imports?: any[]) => {
        if (!imports || imports.length === 0) return null;
        const formatImport = (imp: any) => {
            if (!imp) return '';
            if (typeof imp === 'string') return imp;
            if (imp.text) return String(imp.text);
            if (imp.module) {
                const names = Array.isArray(imp.names) ? imp.names.join(', ') : '';
                const level = typeof imp.level === 'number' && imp.level > 0 ? '.'.repeat(imp.level) : '';
                const moduleText = `${level}${imp.module}`;
                if (names) return `from ${moduleText} import ${names}`;
                if (imp.as) return `import ${moduleText} as ${imp.as}`;
                return `import ${moduleText}`;
            }
            if (imp.name) return String(imp.name);
            return JSON.stringify(imp);
        };
        return (
            <div className="ast-section">
                <div className="ast-section-title">Imports</div>
                <ul className="ast-list">
                    {imports.map((imp, idx) => (
                        <li key={`import-${idx}`}>{formatImport(imp)}</li>
                    ))}
                </ul>
            </div>
        );
    };

    const renderOutlineNode = (node: OutlineNode, depth: number) => {
        const symbol = node.symbol || {};
        const labelParts = [symbol.kind, symbol.name].filter(Boolean) as string[];
        const signature = symbol.signature ? ` ${symbol.signature}` : '';
        const bases = symbol.bases && symbol.bases.length ? ` bases: ${symbol.bases.join(', ')}` : '';
        const range = formatAstRange(symbol);
        const meta = [range, bases].filter(Boolean).join(' ');
        const content = (
            <div className="ast-node-row">
                <span className="ast-node-label">{labelParts.join(' ') || '(symbol)'}{signature}</span>
                {meta && <span className="ast-node-meta">{meta}</span>}
            </div>
        );
        if (node.children.length === 0) {
            return (
                <div key={node.id} className="ast-node ast-leaf" style={{ marginLeft: depth * 12 }}>
                    {content}
                </div>
            );
        }
        return (
            <details key={node.id} className="ast-node" open={depth < 1}>
                <summary>{content}</summary>
                <div className="ast-children">
                    {node.children.map((child) => renderOutlineNode(child, depth + 1))}
                </div>
            </details>
        );
    };

    const renderAstSymbols = (symbols?: AstSymbol[]) => {
        if (!symbols || symbols.length === 0) {
            return <div className="ast-empty">No symbols found.</div>;
        }
        const roots = buildOutlineTree(symbols);
        return (
            <div className="ast-section">
                <div className="ast-section-title">Symbols</div>
                <div className="ast-tree">
                    {roots.map((node) => renderOutlineNode(node, 0))}
                </div>
            </div>
        );
    };

    const renderAstTree = (node: AstNode, depth: number) => {
        const label = formatAstNodeLabel(node) || node.type || '(node)';
        const range = formatAstRange(node);
        const hasChildren = Array.isArray(node.children) && node.children.length > 0;
        const summary = (
            <div className="ast-node-row">
                <span className="ast-node-label">{label}</span>
                {range && <span className="ast-node-meta">{range}</span>}
            </div>
        );
        if (!hasChildren) {
            return (
                <div key={`${label}-${depth}`} className="ast-node ast-leaf" style={{ marginLeft: depth * 12 }}>
                    {summary}
                </div>
            );
        }
        return (
            <details key={`${label}-${depth}`} className="ast-node" open={depth < 1}>
                <summary>{summary}</summary>
                <div className="ast-children">
                    {node.children!.map((child, index) => (
                        <Fragment key={`${child.type || 'node'}-${index}`}>
                            {renderAstTree(child, depth + 1)}
                        </Fragment>
                    ))}
                </div>
            </details>
        );
    };

    const renderAstFile = (item: AstPayload, fileIndex: number) => {
        const filePath = item.path || `File ${fileIndex + 1}`;
        const symbolCount = item.symbols ? item.symbols.length : 0;
        const language = item.language || 'unknown';
        const mode = item.mode || 'outline';
        const truncated = item.truncated;
        return (
            <details key={`ast-file-${fileIndex}`} className="ast-file" open={fileIndex === 0}>
                <summary>
                    <div className="ast-file-header">
                        {onOpenWorkFile && item.path ? (
                            <button
                                type="button"
                                className="ast-file-path clickable"
                                onClick={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    void onOpenWorkFile(item.path || filePath);
                                }}
                            >
                                {filePath}
                            </button>
                        ) : (
                            <span className="ast-file-path">{filePath}</span>
                        )}
                        <span className="ast-file-meta">
                            {language} · {mode}{symbolCount ? ` · ${symbolCount} symbols` : ''}
                            {truncated ? ' · truncated' : ''}
                        </span>
                    </div>
                </summary>
                <div className="ast-file-body">
                    {item.error && <div className="ast-error">{item.error}</div>}
                    {!item.error && item.mode === 'outline' && (
                        <>
                            {renderAstImports(item.imports)}
                            {renderAstSymbols(item.symbols)}
                        </>
                    )}
                    {!item.error && item.mode === 'full' && item.ast && (
                        <div className="ast-tree">{renderAstTree(item.ast, 0)}</div>
                    )}
                    {!item.error && item.mode === 'full' && !item.ast && (
                        <div className="ast-empty">No AST tree found.</div>
                    )}
                </div>
            </details>
        );
    };

    const fileCount = payload.files ? payload.files.length : 0;
    const summaryParts = [
        payload.path ? `path: ${payload.path}` : null,
        payload.language ? `lang: ${payload.language}` : null,
        payload.mode ? `mode: ${payload.mode}` : null,
        fileCount ? `files: ${fileCount}` : null,
        payload.truncated ? 'truncated' : null
    ].filter(Boolean);

    return (
        <div className="ast-view">
            <div className="ast-header">
                <div className="ast-title">AST Viewer</div>
                {onToggleRaw && (
                    <div className="ast-actions">
                        <button type="button" className="ast-action-btn" onClick={onToggleRaw}>
                            {rawVisible ? 'Hide raw' : 'Show raw'}
                        </button>
                    </div>
                )}
            </div>
            {!expanded && summaryParts.length > 0 && (
                <div className="ast-summary">{summaryParts.join(' · ')}</div>
            )}
            {expanded && payload.error && <div className="ast-error">{payload.error}</div>}
            {expanded && !payload.error && payload.files && payload.files.length > 0 && (
                <div className="ast-files">
                    {payload.files.map((file, idx) => renderAstFile(file, idx))}
                </div>
            )}
            {expanded && !payload.error && !payload.files && payload.mode === 'outline' && (
                <>
                    {renderAstImports(payload.imports)}
                    {renderAstSymbols(payload.symbols)}
                </>
            )}
            {expanded && !payload.error && !payload.files && payload.mode === 'full' && payload.ast && (
                <div className="ast-tree">{renderAstTree(payload.ast, 0)}</div>
            )}
            {expanded && !payload.error && !payload.files && payload.mode === 'full' && !payload.ast && (
                <div className="ast-empty">No AST tree found.</div>
            )}
            {rawVisible && (
                <pre className="ast-raw">{JSON.stringify(payload, null, 2)}</pre>
            )}
        </div>
    );
}

export default AstViewer;
