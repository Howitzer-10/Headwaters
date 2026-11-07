/**
 * The commands (HDW-507).
 *
 * Every command returns a {@link CommandResult} instead of returning `void`: `executeCommand`
 * hands it back, so an extension-host test can assert what a command actually did, and the same
 * words are what the user sees in the notification. Nothing here reports success it did not get
 * from the daemon.
 *
 * Two honesty rules the commands obey:
 *  - Pin is real but narrow: `headwaters_pin_model` writes the daemon's `mcp.pinned_model`, which
 *    affects *front-door MCP tool calls only*. Gateway OpenAI/Anthropic traffic pins per request
 *    (`x-headwaters-pin`). Every string that mentions the pin says which one it changes.
 *  - There is no "open handoff": nothing can read a stored packet back (`headwaters/mcp/tools/
 *    handoff.py` only writes). The command is "Create Handoff Packet" and it says where the packet
 *    went.
 */

import * as vscode from 'vscode';
import { readDaemon } from './daemon';
import { DoctorRun, runDoctor } from './doctor';
import { clientFor, McpClient } from './mcpClient';
import { openExternal } from './openers';
import { postScope, readSnapshot, Scope, SCOPES, Snapshot } from './statusClient';

export const REFRESH_STATUS_COMMAND = 'headwaters.refreshStatus';
export const SET_SCOPE_COMMAND = 'headwaters.setScope';
export const TOGGLE_PRIVATE_COMMAND = 'headwaters.togglePrivateLock';
export const PIN_COMMAND = 'headwaters.pinModel';
export const HANDOFF_COMMAND = 'headwaters.createHandoff';
export const DOCTOR_COMMAND = 'headwaters.runDoctor';
export const DASHBOARD_COMMAND = 'headwaters.openDashboard';

/** What a command did. `ok: false` is a real refusal, never a silent no-op. */
export interface CommandResult {
  readonly ok: boolean;
  readonly message: string;
  readonly detail?: string;
  readonly documentUri?: string;
}

export interface CommandDeps {
  readonly log: vscode.OutputChannel;
  /** Ask the status bar to re-read; every write goes through it so the bar never lags. */
  readonly refresh: () => Promise<unknown>;
  readonly env?: NodeJS.ProcessEnv;
  /** Test seam for `headwaters doctor`; production uses the CLI spawn. */
  readonly doctor?: (cwd: string) => Promise<DoctorRun>;
}

const PIN_SCOPE_NOTE =
  'This pins front-door MCP tool calls only (the headwaters_* tools). Traffic through the ' +
  "gateway's OpenAI and Anthropic routes is not affected: those pin per request with the " +
  'x-headwaters-pin header and the model named explicitly.';

const SCOPE_DESCRIPTIONS: Record<Scope, string> = {
  auto: 'Auto — the router decides from the request and the rules',
  free: 'Free — free tiers only, no paid tier',
  local: 'Local — this machine only, nothing leaves it',
  cloud: 'Cloud — remote routes, paid tier included',
};

export function registerCommands(context: vscode.ExtensionContext, deps: CommandDeps): void {
  const env = (): NodeJS.ProcessEnv => deps.env ?? process.env;

  const register = (
    name: string,
    handler: (...args: unknown[]) => Promise<CommandResult>,
  ): void => {
    context.subscriptions.push(
      vscode.commands.registerCommand(name, async (...args: unknown[]) => {
        const result = await handler(...args);
        deps.log.appendLine(
          `[${name}] ${result.ok ? 'ok' : 'refused'}: ${result.message}${
            result.detail === undefined ? '' : ` — ${result.detail}`
          }`,
        );
        await notify(result);
        return result;
      }),
    );
  };

  register(REFRESH_STATUS_COMMAND, async () => {
    const snapshot = await deps.refresh();
    return {
      ok: true,
      message:
        snapshot !== undefined && (snapshot as Snapshot).kind === 'unavailable'
          ? 'Status refreshed: the daemon is not reachable.'
          : 'Status refreshed.',
    };
  });

  register(SET_SCOPE_COMMAND, async (scopeArg) => {
    const current = await readSnapshot(env());
    const chosen = typeof scopeArg === 'string' ? scopeArg : await pickScope(current);
    if (chosen === undefined) {
      return { ok: false, message: 'No scope chosen; nothing was written.' };
    }
    if (!isScope(chosen)) {
      return {
        ok: false,
        message: `"${chosen}" is not a scope. The daemon accepts ${SCOPES.join(', ')}.`,
      };
    }
    try {
      const reply = await postScope(env(), 'default_scope', chosen);
      if (!reply.applied) {
        return {
          ok: false,
          message: `The daemon did not apply ${chosen}: ${reply.reason || 'no reason given'}.`,
        };
      }
      await deps.refresh();
      return { ok: true, message: `Scope is now ${reply.defaultScope ?? chosen}.` };
    } catch (error) {
      return { ok: false, message: `Could not set the scope: ${describe(error)}` };
    }
  });

  register(TOGGLE_PRIVATE_COMMAND, async () => {
    const current = await readSnapshot(env());
    if (current.kind !== 'live') {
      return { ok: false, message: `The daemon is not reachable: ${current.detail}` };
    }
    const next = !current.privateLock;
    try {
      const reply = await postScope(env(), 'private_lock', next);
      if (!reply.applied) {
        return {
          ok: false,
          message: `The daemon did not change Private Mode: ${reply.reason || 'no reason given'}.`,
        };
      }
      await deps.refresh();
      return {
        ok: true,
        message: next
          ? 'Private Mode is on: nothing leaves this machine.'
          : 'Private Mode is off: normal routing is back.',
      };
    } catch (error) {
      return { ok: false, message: `Could not change Private Mode: ${describe(error)}` };
    }
  });

  register(PIN_COMMAND, async (modelArg) => {
    const typed =
      typeof modelArg === 'string'
        ? modelArg
        : await vscode.window.showInputBox({
            title: 'Pin a model for front-door MCP tool calls',
            prompt:
              'A model id the scope already allows. Leave empty to clear the pin. ' +
              'Gateway OpenAI/Anthropic traffic is not affected.',
            placeHolder: 'e.g. headwaters/auto',
          });
    if (typed === undefined) {
      return { ok: false, message: 'Cancelled; the pin is unchanged.' };
    }
    const model = typed.trim();
    try {
      const client = await mcpFor(env());
      const result = await client.callTool('headwaters_pin_model', {
        model: model === '' ? null : model,
      });
      const value = result.value;
      if (value === undefined) {
        return { ok: false, message: 'The daemon answered without a pin result.' };
      }
      if (value['ok'] !== true) {
        const error = isRecord(value['error']) ? value['error'] : {};
        const message = typeof error['message'] === 'string' ? error['message'] : result.text;
        return {
          ok: false,
          message: `The daemon refused the pin: ${message || 'no reason given'}`,
          detail: PIN_SCOPE_NOTE,
        };
      }
      await deps.refresh();
      return {
        ok: true,
        message:
          model === ''
            ? `Pin cleared. ${PIN_SCOPE_NOTE}`
            : `Pinned ${model}. ${PIN_SCOPE_NOTE}`,
        detail: typeof value['note'] === 'string' ? value['note'] : undefined,
      };
    } catch (error) {
      return { ok: false, message: `Could not pin a model: ${describe(error)}` };
    }
  });

  register(HANDOFF_COMMAND, async (goalArg) => {
    const typed =
      typeof goalArg === 'string'
        ? goalArg
        : await vscode.window.showInputBox({
            title: 'Create a handoff packet',
            prompt: 'What is the work for? The daemon stores the packet and gives back a packet id.',
            validateInput: (value) => (value.trim() === '' ? 'A goal is required.' : undefined),
          });
    if (typed === undefined || typed.trim() === '') {
      return { ok: false, message: 'Cancelled; no packet was created.' };
    }
    const paths = openFilePaths();
    try {
      const client = await mcpFor(env());
      const result = await client.callTool('headwaters_handoff', {
        goal: typed.trim(),
        paths,
      });
      const value = result.value;
      if (value === undefined || value['ok'] !== true) {
        const error = isRecord(value?.['error']) ? value?.['error'] : {};
        const message = isRecord(error) && typeof error['message'] === 'string' ? error['message'] : '';
        return {
          ok: false,
          message: `The daemon did not create a packet: ${message || result.text || 'no reason given'}`,
        };
      }
      const packetId = typeof value['packet_id'] === 'string' ? value['packet_id'] : 'unknown';
      const document = await openUntitled(JSON.stringify(value, null, 2), 'json');
      return {
        ok: true,
        message: `Handoff packet ${packetId} created and stored by the daemon.`,
        detail:
          'Nothing can re-open a stored packet: the daemon only writes them, so the packet id is ' +
          'the handle to keep.',
        documentUri: document,
      };
    } catch (error) {
      return { ok: false, message: `Could not create a handoff packet: ${describe(error)}` };
    }
  });

  register(DOCTOR_COMMAND, async () => {
    const run = await (deps.doctor ?? ((cwd: string) => runDoctor(cwd)))(cwdForDoctor(env()));
    if (run.kind === 'cli-missing') {
      return {
        ok: false,
        message:
          'The headwaters CLI is not on PATH, so headwaters doctor could not run. There is no ' +
          'HTTP endpoint for doctor: install the CLI (pip install headwaters) or run it in a ' +
          'terminal where it is on PATH.',
        detail: run.text,
      };
    }
    const document = await openUntitled(doctorText(run), 'markdown');
    return {
      ok: run.kind === 'ok',
      message:
        run.kind === 'ok'
          ? 'Doctor finished; the output is open in an untitled editor.'
          : `Doctor reported a problem (exit ${String(run.exitCode ?? 'unknown')}); the output is open.`,
      detail: run.text,
      documentUri: document,
    };
  });

  register(DASHBOARD_COMMAND, async () => {
    try {
      const daemon = await readDaemon(env());
      if (daemon.dashboardUrl === null) {
        return {
          ok: false,
          message:
            'The daemon has not bound its dashboard port (daemon.json ports.dashboard is null), ' +
            'so there is no page to open.',
        };
      }
      const opened = await openExternal(daemon.dashboardUrl);
      return opened
        ? { ok: true, message: `Opened ${daemon.dashboardUrl}` }
        : { ok: false, message: `VS Code could not open ${daemon.dashboardUrl}.` };
    } catch (error) {
      return { ok: false, message: `Could not open the dashboard: ${describe(error)}` };
    }
  });
}

async function notify(result: CommandResult): Promise<void> {
  const text = result.detail === undefined ? result.message : `${result.message} ${result.detail}`;
  if (result.ok) {
    void vscode.window.setStatusBarMessage(`Headwaters: ${result.message}`, 5000);
    return;
  }
  // Not awaited: the command's result must not wait for a toast to be dismissed.
  void vscode.window.showWarningMessage(`Headwaters: ${text}`);
}

async function pickScope(snapshot: Snapshot): Promise<string | undefined> {
  const current = snapshot.kind === 'live' ? snapshot.scope : null;
  const items: (vscode.QuickPickItem & { value: Scope })[] = SCOPES.map((value) => ({
    label: value,
    description: value === current ? 'current' : '',
    detail: SCOPE_DESCRIPTIONS[value],
    value,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    title: 'Headwaters scope',
    placeHolder:
      current === null
        ? 'The current scope could not be read; picking one still writes it.'
        : `Current scope: ${current}`,
  });
  return picked?.value;
}

async function mcpFor(env: NodeJS.ProcessEnv): Promise<McpClient> {
  const daemon = await readDaemon(env);
  if (daemon.apiUrl === null) {
    throw new Error('the daemon has not bound its API port, so MCP tools cannot be called');
  }
  return clientFor({ apiUrl: daemon.apiUrl, token: daemon.token, mcpPath: daemon.mcpPath });
}

/** The open file, relative to its workspace folder, as the handoff tool wants it (names only). */
function openFilePaths(): string[] {
  const editor = vscode.window.activeTextEditor;
  if (editor === undefined || editor.document.uri.scheme !== 'file') {
    return [];
  }
  const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
  return [
    folder === undefined
      ? editor.document.uri.fsPath
      : vscode.workspace.asRelativePath(editor.document.uri, false),
  ];
}

function cwdForDoctor(env: NodeJS.ProcessEnv): string {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder !== undefined) {
    return folder.uri.fsPath;
  }
  return env.HEADWATERS_HOME?.trim() || process.cwd();
}

function doctorText(run: DoctorRun): string {
  const header =
    run.kind === 'ok'
      ? '# headwaters doctor'
      : `# headwaters doctor (exit ${String(run.exitCode ?? 'unknown')})`;
  return `${header}\n\n\`\`\`\n${run.text}\n\`\`\`\n`;
}

async function openUntitled(content: string, language: string): Promise<string> {
  const document = await vscode.workspace.openTextDocument({ content, language });
  await vscode.window.showTextDocument(document, { preview: false });
  return document.uri.toString();
}

function isScope(value: string): value is Scope {
  return (SCOPES as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
