/**
 * Test fixtures for the extension tests (HDW-506).
 *
 * The extension finds the daemon through `HEADWATERS_HOME` (the same override
 * `headwaters/core/paths.py` honours), so a test can point it at a temp directory and never touch
 * the developer's real `%LOCALAPPDATA%\Headwaters`. The state files written here have the same shape
 * as the ones the daemon writes.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

export class TempHome {
  readonly dir: string;

  constructor() {
    this.dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hdw506-'));
  }

  get stateDir(): string {
    return path.join(this.dir, 'state');
  }

  /** Write `daemon.json` and `daemon.token` the way a running daemon would. */
  writeDaemon(port: number | null, token: string, version = '9.9.9-mock'): void {
    fs.mkdirSync(this.stateDir, { recursive: true });
    const info = {
      schema: 1,
      pid: process.pid,
      started_at: '2026-01-01T00:00:00Z',
      version,
      host: '127.0.0.1',
      mcp_path: '/mcp',
      ports: { api: port, gateway: port, dashboard: port },
    };
    fs.writeFileSync(path.join(this.stateDir, 'daemon.json'), JSON.stringify(info, null, 2), 'utf8');
    fs.writeFileSync(path.join(this.stateDir, 'daemon.token'), `${token}\n`, 'utf8');
  }

  cleanup(): void {
    fs.rmSync(this.dir, { recursive: true, force: true });
  }
}

export function tempHomeWithoutState(): TempHome {
  return new TempHome();
}

export async function delay(ms: number): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, ms));
}
