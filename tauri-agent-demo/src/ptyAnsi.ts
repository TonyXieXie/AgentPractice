const OSC_RE = /\x1b\][^\x07]*(?:\x07|\x1b\\)/g;
const ANSI_RE = /[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[@-~]/g;
const CTRL_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;
const COMPLETION_MARKER_RE = /__PTY_COMPLETION_[A-Za-z0-9_.:-]+__/g;

export const sanitizePtyChunk = (chunk: string): string => {
  const value = String(chunk || '');
  if (!value) return '';
  return value.replace(COMPLETION_MARKER_RE, '');
};

export const stripAnsiForDisplay = (input: string): string => {
  if (!input) return '';
  let output = sanitizePtyChunk(input);
  output = output.replace(OSC_RE, '');
  output = output.replace(ANSI_RE, '');
  output = output.replace(/\r\n/g, '\n');
  output = output
    .split('\n')
    .map((line) => {
      const parts = line.split('\r');
      return parts[parts.length - 1] || '';
    })
    .join('\n');
  output = output.replace(CTRL_RE, '');
  return output;
};
