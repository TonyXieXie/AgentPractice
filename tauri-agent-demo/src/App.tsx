import AppShell, { type AppShellWindowMode } from './AppShell';

function resolveWindowMode(): AppShellWindowMode {
  const params = new URLSearchParams(window.location.search);
  return params.get('window') === 'branch' ? 'branch' : 'main';
}

export default function App() {
  return <AppShell windowMode={resolveWindowMode()} />;
}
