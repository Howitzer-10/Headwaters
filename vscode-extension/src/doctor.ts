/**
 * `headwaters doctor` from the extension (HDW-507).
 *
 * There is no HTTP route for doctor (`headwaters/cli/cmd_doctor.py` is the only entry point), so
 * the honest path is to run the CLI the user already has. When the CLI is not on `PATH` this
 * returns `cli-missing` and the command says exactly that — it does not pretend to have checked
 * anything.
 */

import { execFile } from 'node:child_process';

const DOCTOR_TIMEOUT_MS = 30_000;
const MAX_BUFFER = 4 * 1024 * 1024;

export interface DoctorRun {
  readonly kind: 'ok' | 'failed' | 'cli-missing';
  /** The command's own output (stdout, then stderr) — never invented. */
  readonly text: string;
  readonly exitCode: number | null;
}

/** Run `headwaters doctor --json` and classify the result. */
export function runDoctor(cwd: string, timeoutMs: number = DOCTOR_TIMEOUT_MS): Promise<DoctorRun> {
  return new Promise((resolve) => {
    execFile(
      'headwaters',
      ['doctor', '--json'],
      // A shell on purpose: a pip/uv console script on Windows may be a `.cmd` shim, which
      // CreateProcess cannot start directly. The arguments are literals, so no quoting risk.
      { cwd, timeout: timeoutMs, maxBuffer: MAX_BUFFER, windowsHide: true, shell: true },
      (error, stdout, stderr) => {
        const text = [stdout.trim(), stderr.trim()].filter((part) => part !== '').join('\n');
        if (error === null) {
          resolve({ kind: 'ok', text, exitCode: 0 });
          return;
        }
        const code = typeof error.code === 'number' ? error.code : null;
        const missing =
          (error as { code?: unknown }).code === 'ENOENT' ||
          /not recognized|command not found|not found|no such file/i.test(text);
        resolve({ kind: missing ? 'cli-missing' : 'failed', text, exitCode: code });
      },
    );
  });
}
