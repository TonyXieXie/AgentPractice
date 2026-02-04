import { useEffect, useMemo, useRef, useState } from 'react';
import MarkdownIt from 'markdown-it';
import { readDir, readFile, readTextFile, type DirEntry } from '@tauri-apps/plugin-fs';
import './WorkDirBrowser.css';

type FileKind = 'text' | 'markdown' | 'image' | 'unknown';

type OpenFile = {
  path: string;
  name: string;
  ext: string;
  kind: FileKind;
  content?: string;
  url?: string;
  loading?: boolean;
  error?: string;
};

type WorkDirBrowserProps = {
  open: boolean;
  rootPath: string;
  onClose?: () => void;
  onPickWorkPath?: () => void;
  onOpenInExplorer?: () => void;
  mode?: 'overlay' | 'window';
  showActions?: boolean;
  showClose?: boolean;
};

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico']);
const MARKDOWN_EXTENSIONS = new Set(['md', 'markdown', 'mdx']);

const getFileExt = (name: string) => {
  const trimmed = name.trim();
  const idx = trimmed.lastIndexOf('.');
  if (idx <= 0 || idx === trimmed.length - 1) return '';
  return trimmed.slice(idx + 1).toLowerCase();
};

const getFileKind = (ext: string): FileKind => {
  if (!ext) return 'text';
  if (MARKDOWN_EXTENSIONS.has(ext)) return 'markdown';
  if (IMAGE_EXTENSIONS.has(ext)) return 'image';
  return 'text';
};

const getMimeType = (ext: string) => {
  switch (ext) {
    case 'png':
      return 'image/png';
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg';
    case 'gif':
      return 'image/gif';
    case 'bmp':
      return 'image/bmp';
    case 'webp':
      return 'image/webp';
    case 'svg':
      return 'image/svg+xml';
    case 'ico':
      return 'image/x-icon';
    default:
      return 'application/octet-stream';
  }
};

const joinPath = (base: string, child: string) => {
  if (!base) return child;
  const separator = base.includes('\\') ? '\\' : '/';
  if (base.endsWith('\\') || base.endsWith('/')) {
    return `${base}${child}`;
  }
  return `${base}${separator}${child}`;
};

const getBaseName = (path: string) => {
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
  if (idx < 0) return trimmed;
  return trimmed.slice(idx + 1) || trimmed;
};

const formatError = (error: unknown) => {
  if (!error) return 'Unknown error';
  if (error instanceof Error) return error.message || 'Unknown error';
  return String(error);
};

function WorkDirBrowser({
  open,
  rootPath,
  onClose,
  onPickWorkPath,
  onOpenInExplorer,
  mode = 'overlay',
  showActions = true,
  showClose,
}: WorkDirBrowserProps) {
  const [dirCache, setDirCache] = useState<Record<string, DirEntry[]>>({});
  const [dirLoading, setDirLoading] = useState<Record<string, boolean>>({});
  const [dirErrors, setDirErrors] = useState<Record<string, string>>({});
  const [expandedDirs, setExpandedDirs] = useState<Record<string, boolean>>({});
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const previousRootRef = useRef<string>('');
  const openFilesRef = useRef<OpenFile[]>([]);

  const rootName = useMemo(() => (rootPath ? getBaseName(rootPath) : ''), [rootPath]);

  const revokeFileUrl = (file: OpenFile) => {
    if (file.url) {
      URL.revokeObjectURL(file.url);
    }
  };

  const resetState = () => {
    setDirCache({});
    setDirLoading({});
    setDirErrors({});
    setExpandedDirs({});
    setOpenFiles((prev) => {
      prev.forEach(revokeFileUrl);
      return [];
    });
    setActivePath(null);
  };

  useEffect(() => {
    if (!open) return;
    if (!rootPath) return;
    if (previousRootRef.current && previousRootRef.current !== rootPath) {
      resetState();
    }
    previousRootRef.current = rootPath;
  }, [open, rootPath]);

  useEffect(() => {
    if (!open || !rootPath) return;
    if (!expandedDirs[rootPath]) {
      setExpandedDirs((prev) => ({ ...prev, [rootPath]: true }));
    }
    if (!dirCache[rootPath] && !dirLoading[rootPath]) {
      void loadDir(rootPath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rootPath]);

  useEffect(() => {
    openFilesRef.current = openFiles;
  }, [openFiles]);

  useEffect(() => {
    return () => {
      openFilesRef.current.forEach(revokeFileUrl);
    };
  }, []);

  const loadDir = async (path: string) => {
    setDirLoading((prev) => ({ ...prev, [path]: true }));
    try {
      const entries = await readDir(path);
      const sorted = [...entries].sort((a, b) => {
        if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
        return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      });
      setDirCache((prev) => ({ ...prev, [path]: sorted }));
      setDirErrors((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
    } catch (error) {
      setDirErrors((prev) => ({ ...prev, [path]: formatError(error) }));
    } finally {
      setDirLoading((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
    }
  };

  const toggleDir = (path: string) => {
    setExpandedDirs((prev) => ({ ...prev, [path]: !prev[path] }));
    if (!dirCache[path] && !dirLoading[path]) {
      void loadDir(path);
    }
  };

  const updateFile = (path: string, update: Partial<OpenFile>) => {
    setOpenFiles((prev) =>
      prev.map((file) => (file.path === path ? { ...file, ...update } : file))
    );
  };

  const openFile = async (path: string, name: string) => {
    const ext = getFileExt(name);
    const kind = getFileKind(ext);
    const newFile: OpenFile = { path, name, ext, kind, loading: true };
    let shouldLoad = true;
    setOpenFiles((prev) => {
      if (prev.some((file) => file.path === path)) {
        shouldLoad = false;
        return prev;
      }
      return [...prev, newFile];
    });
    setActivePath(path);
    if (!shouldLoad) return;

    try {
      if (kind === 'image') {
        const bytes = await readFile(path);
        const url = URL.createObjectURL(new Blob([bytes], { type: getMimeType(ext) }));
        if (!openFilesRef.current.some((file) => file.path === path)) {
          URL.revokeObjectURL(url);
          return;
        }
        updateFile(path, { url, loading: false });
      } else {
        const text = await readTextFile(path);
        if (!openFilesRef.current.some((file) => file.path === path)) return;
        updateFile(path, { content: text, loading: false });
      }
    } catch (error) {
      updateFile(path, { error: formatError(error), loading: false });
    }
  };

  const closeFile = (path: string) => {
    setOpenFiles((prev) => {
      const idx = prev.findIndex((file) => file.path === path);
      if (idx < 0) return prev;
      const next = prev.filter((file) => file.path !== path);
      revokeFileUrl(prev[idx]);
      if (activePath === path) {
        const nextActive = next[idx - 1]?.path || next[idx]?.path || null;
        setActivePath(nextActive);
      }
      return next;
    });
  };

  const activeFile = activePath ? openFiles.find((file) => file.path === activePath) || null : null;

  const renderEntries = (parentPath: string, depth: number) => {
    const entries = dirCache[parentPath];
    if (!entries) return null;

    if (!entries.length) {
      return (
        <div className="workdir-tree-empty" style={{ paddingLeft: depth * 14 + 10 }}>
          (空目录)
        </div>
      );
    }

    return entries.map((entry) => {
      const entryPath = joinPath(parentPath, entry.name);
      const isExpanded = !!expandedDirs[entryPath];
      const isActive = activePath === entryPath;
      const isDir = entry.isDirectory;

      return (
        <div key={entryPath}>
          <div
            className={`workdir-tree-row${isActive ? ' active' : ''}`}
            style={{ paddingLeft: depth * 14 + 6 }}
            onClick={() => {
              if (isDir) {
                toggleDir(entryPath);
              } else {
                void openFile(entryPath, entry.name);
              }
            }}
          >
            <span className={`workdir-tree-toggle${isDir ? '' : ' hidden'}${isExpanded ? ' expanded' : ''}`}>
              ▸
            </span>
            <span className={`workdir-tree-name${isDir ? ' dir' : ''}`}>{entry.name}</span>
          </div>
          {isDir && isExpanded && (
            <div className="workdir-tree-children">
              {dirErrors[entryPath] && (
                <div className="workdir-tree-error" style={{ paddingLeft: (depth + 1) * 14 + 6 }}>
                  读取失败：{dirErrors[entryPath]}
                </div>
              )}
              {dirLoading[entryPath] && (
                <div className="workdir-tree-loading" style={{ paddingLeft: (depth + 1) * 14 + 6 }}>
                  加载中...
                </div>
              )}
              {renderEntries(entryPath, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  if (!open) return null;

  const closeVisible = showClose ?? mode === 'overlay';

  const content = (
    <div
      className={`workdir-window${mode === 'window' ? ' embedded' : ''}`}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="workdir-header">
        <div className="workdir-header-title">
          <h2>工作目录</h2>
          <span className="workdir-path" title={rootPath}>
            {rootPath || '未设置'}
          </span>
        </div>
        <div className="workdir-header-actions">
          {showActions && onOpenInExplorer && rootPath && (
            <button type="button" className="workdir-header-btn" onClick={onOpenInExplorer}>
              在资源管理器打开
            </button>
          )}
          {showActions && onPickWorkPath && (
            <button type="button" className="workdir-header-btn" onClick={onPickWorkPath}>
              重新选择
            </button>
          )}
          {closeVisible && onClose && (
            <button type="button" className="workdir-close-btn" onClick={onClose}>
              ×
            </button>
          )}
        </div>
      </div>

      {!rootPath ? (
        <div className="workdir-empty-state">
          <p>请先选择一个工作目录。</p>
          {showActions && onPickWorkPath && (
            <button type="button" className="workdir-primary-btn" onClick={onPickWorkPath}>
              选择工作目录
            </button>
          )}
        </div>
      ) : (
        <div className="workdir-body">
          <div className="workdir-sidebar">
            <div className="workdir-sidebar-title">资源管理器</div>
            <div className="workdir-tree">
              <div className="workdir-tree-row root" onClick={() => toggleDir(rootPath)}>
                <span className={`workdir-tree-toggle${expandedDirs[rootPath] ? ' expanded' : ''}`}>▸</span>
                <span className="workdir-tree-name dir">{rootName || rootPath}</span>
              </div>
              {dirErrors[rootPath] && (
                <div className="workdir-tree-error" style={{ paddingLeft: 20 }}>
                  读取失败：{dirErrors[rootPath]}
                </div>
              )}
              {dirLoading[rootPath] && (
                <div className="workdir-tree-loading" style={{ paddingLeft: 20 }}>
                  加载中...
                </div>
              )}
              {expandedDirs[rootPath] && renderEntries(rootPath, 1)}
            </div>
          </div>

          <div className="workdir-editor">
            <div className="workdir-tabs">
              {openFiles.length === 0 && <span className="workdir-tabs-empty">尚未打开文件</span>}
              {openFiles.map((file) => (
                <div
                  key={file.path}
                  className={`workdir-tab${file.path === activePath ? ' active' : ''}`}
                  onClick={() => setActivePath(file.path)}
                >
                  <span className="workdir-tab-label">{file.name}</span>
                  <button
                    type="button"
                    className="workdir-tab-close"
                    onClick={(event) => {
                      event.stopPropagation();
                      closeFile(file.path);
                    }}
                    aria-label={`关闭 ${file.name}`}
                    title="关闭"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>

            <div className="workdir-viewer">
              {!activeFile && (
                <div className="workdir-placeholder">
                  <p>从左侧选择文件进行预览。</p>
                </div>
              )}
              {activeFile && activeFile.loading && (
                <div className="workdir-placeholder">
                  <p>正在加载 {activeFile.name} ...</p>
                </div>
              )}
              {activeFile && activeFile.error && (
                <div className="workdir-error">
                  无法打开文件：{activeFile.error}
                </div>
              )}
              {activeFile && !activeFile.loading && !activeFile.error && activeFile.kind === 'image' && (
                <div className="workdir-image-viewer">
                  {activeFile.url ? <img src={activeFile.url} alt={activeFile.name} /> : <span>图片加载失败。</span>}
                </div>
              )}
              {activeFile && !activeFile.loading && !activeFile.error && activeFile.kind === 'markdown' && (
                <div
                  className="workdir-markdown"
                  dangerouslySetInnerHTML={{ __html: markdown.render(activeFile.content || '') }}
                />
              )}
              {activeFile && !activeFile.loading && !activeFile.error && activeFile.kind === 'text' && (
                <pre className="workdir-text-viewer">{activeFile.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  if (mode === 'overlay') {
    return (
      <div className="workdir-overlay" onClick={() => onClose && onClose()}>
        {content}
      </div>
    );
  }

  return <div className="workdir-embedded">{content}</div>;
}

export default WorkDirBrowser;
