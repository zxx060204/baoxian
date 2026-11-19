import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const modules = { api: 'baoxian_api.serve', worker: 'baoxian_api.worker_runtime' };
const moduleName = modules[process.argv[2]];
if (!moduleName) {
  console.error('Usage: python-service.mjs <api|worker>');
  process.exit(2);
}
const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.platform === 'win32'
  ? path.join(workspace, '.venv-python', 'Scripts', 'python.exe')
  : path.join(workspace, '.venv-python', 'bin', 'python');
const isWindowsWorker = process.platform === 'win32' && moduleName === 'baoxian_api.worker_runtime';
const child = spawn(python, ['-m', moduleName], {
  cwd: path.join(workspace, 'apps', 'backend-python'),
  stdio: 'inherit',
  windowsHide: true,
});
for (const name of ['SIGINT', 'SIGTERM', 'SIGBREAK']) {
  process.on(name, () => {
    // Windows console control events are broadcast to the parent and its
    // attached child. Let the Python Worker run its registered shutdown hook.
    if (isWindowsWorker) return;
    if (child.exitCode !== null || child.signalCode !== null) return;
    child.kill(name);
  });
}
child.on('error', error => { console.error('Python 启动失败:', error.message); process.exitCode = 1; });
child.on('exit', (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0); });
