import { useMemo } from 'react';
import './DiffView.css';

type DiffRowType = 'add' | 'del' | 'ctx' | 'hunk' | 'meta';

interface DiffRow {
    type: DiffRowType;
    text: string;
    oldNo?: number | null;
    newNo?: number | null;
}

interface DiffViewProps {
    content: string;
}

function parseUnifiedDiff(content: string): DiffRow[] {
    const rows: DiffRow[] = [];
    const lines = content.replace(/\r\n/g, '\n').split('\n');
    let oldNo: number | null = null;
    let newNo: number | null = null;

    for (const line of lines) {
        if (line.startsWith('@@')) {
            const match = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
            if (match) {
                oldNo = parseInt(match[1], 10);
                newNo = parseInt(match[2], 10);
            } else {
                oldNo = null;
                newNo = null;
            }
            rows.push({ type: 'hunk', text: line });
            continue;
        }

        if (
            line.startsWith('diff --git') ||
            line.startsWith('index ') ||
            line.startsWith('--- ') ||
            line.startsWith('+++ ') ||
            line.startsWith('new file mode') ||
            line.startsWith('deleted file mode')
        ) {
            rows.push({ type: 'meta', text: line });
            continue;
        }

        if (line.startsWith('+') && !line.startsWith('+++')) {
            rows.push({ type: 'add', text: line.slice(1), oldNo: null, newNo });
            if (newNo !== null) newNo += 1;
            continue;
        }

        if (line.startsWith('-') && !line.startsWith('---')) {
            rows.push({ type: 'del', text: line.slice(1), oldNo, newNo: null });
            if (oldNo !== null) oldNo += 1;
            continue;
        }

        if (line.startsWith(' ')) {
            rows.push({ type: 'ctx', text: line.slice(1), oldNo, newNo });
            if (oldNo !== null) oldNo += 1;
            if (newNo !== null) newNo += 1;
            continue;
        }

        rows.push({ type: 'ctx', text: line, oldNo, newNo });
        if (oldNo !== null) oldNo += 1;
        if (newNo !== null) newNo += 1;
    }

    return rows;
}

function formatLineNo(value?: number | null) {
    if (!value) return '';
    return String(value);
}

export default function DiffView({ content }: DiffViewProps) {
    const rows = useMemo(() => parseUnifiedDiff(content), [content]);

    return (
        <div className="diff-view">
            {rows.map((row, index) => (
                <div key={`${row.type}-${index}`} className={`diff-line ${row.type}`}>
                    <span className="diff-lineno old">{formatLineNo(row.oldNo)}</span>
                    <span className="diff-lineno new">{formatLineNo(row.newNo)}</span>
                    <span className="diff-code">{row.text}</span>
                </div>
            ))}
        </div>
    );
}
