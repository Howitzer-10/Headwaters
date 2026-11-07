/**
 * Every contributed command, executed for real (HDW-507).
 *
 * `vscode.commands.executeCommand` runs the registered handler in this extension host, against the
 * loopback mock daemon in `test/mockGateway.ts`; the assertions are on what the *daemon received*
 * (`gateway.bodiesFor`) and on what the handler returned, which is what the user sees in the
 * notification. Nothing here calls a handler directly, so a command that is not registered — or
 * registered under another id — fails the suite.
 *
 * The two seams are deliberate: `headwaters doctor` would need the real CLI on PATH, and
 * `openDashboard` must not actually launch a browser in a test run. Both are `activate`-side
 * injection points, not stubs of the logic under test.
 */

import * as assert from 'node:assert/strict';
import * as path from 'node:path';

import * as vscode from 'vscode';

import { CommandResult } from '../src/commands';
import { DoctorRun } from '../src/doctor';
import { HeadwatersExtensionApi } from '../src/extension';
import { forgetClients } from '../src/mcpClient';
import { PRIVATE_LOCK_COLOR } from '../src/statusBar';
import { delay, TempHome } from './fixture';
import { defaultState, MOCK_TOKEN, MockGateway } from './mockGateway';

const EXTENSION_ID = 'headwaters.headwaters-vscode';

const COMMANDS = [
  'headwaters.refreshModels',
  'headwaters.refreshStatus',
  'headwaters.setScope',
  'headwaters.togglePrivateLock',
  'headwaters.pinModel',
  'headwaters.createHandoff',
  'headwaters.runDoctor',
  'headwaters.openDashboard',
];

/** The mock's starting state, to put back between tests. */
function resetDaemon(gateway: MockGateway): void {
  Object.assign(gateway.state, defaultState());
  gateway.authBroken = false;
  gateway.sessionMode = 'ok';
}

async function waitFor(condition: () => boolean, what: string, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) {
      return;
    }
    await delay(50);
  }
  assert.fail(`timed out waiting for ${what}`);
}

suite('Headwaters commands', () => {
  let gateway: MockGateway;
  let home: TempHome;
  let previousHome: string | undefined;
  let api: HeadwatersExtensionApi;
  let port: number;
  let opened: string[];
  let doctorRuns: string[];

  function run<T = CommandResult>(command: string, ...args: unknown[]): Thenable<T> {
    return vscode.commands.executeCommand<T>(command, ...args);
  }

  async function readBar(): Promise<void> {
    await api.refreshStatus();
  }

  /** The item's theme colour id, or null when it is unpainted. */
  function colorId(): string | null {
    const color = api.statusBar.item.color;
    if (color === undefined || typeof color === 'string') {
      return null;
    }
    return color.id;
  }

  /** The item's tooltip as plain markdown, empty before the first render. */
  function tooltipText(): string {
    const tooltip = api.statusBar.item.tooltip;
    if (tooltip === undefined) {
      return '';
    }
    return typeof tooltip === 'string' ? tooltip : tooltip.value;
  }

  suiteSetup(async function () {
    this.timeout(60000);
    gateway = new MockGateway();
    await gateway.start();
    port = Number(new URL(gateway.url).port);
    home = new TempHome();
    home.writeDaemon(port, MOCK_TOKEN);

    previousHome = process.env.HEADWATERS_HOME;
    process.env.HEADWATERS_HOME = home.dir;

    const extension = vscode.extensions.getExtension<HeadwatersExtensionApi>(EXTENSION_ID);
    assert.ok(extension, `${EXTENSION_ID} is loaded in this test host`);
    api = await extension.activate();
  });

  suiteTeardown(async () => {
    if (previousHome === undefined) {
      delete process.env.HEADWATERS_HOME;
    } else {
      process.env.HEADWATERS_HOME = previousHome;
    }
    forgetClients();
    await gateway.stop();
    home.cleanup();
  });

  setup(() => {
    resetDaemon(gateway);
    forgetClients();
    opened = [];
    doctorRuns = [];
    api.setExternalOpener(async (url) => {
      opened.push(url);
      return true;
    });
    api.setDoctorRunner(async (cwd): Promise<DoctorRun> => {
      doctorRuns.push(cwd);
      return { kind: 'ok', text: '{"ok":true,"checks":[]}', exitCode: 0 } satisfies DoctorRun;
    });
  });

  test('every contributed command is registered in this host', async () => {
    const registered = new Set(await vscode.commands.getCommands(true));
    for (const command of COMMANDS) {
      assert.ok(registered.has(command), `${command} is registered`);
    }
  });

  test('refreshStatus re-reads the daemon and says so', async () => {
    await readBar();
    const result = await run('headwaters.refreshStatus');
    assert.equal(result.ok, true);
    assert.equal(result.message, 'Status refreshed.');
    assert.ok(api.statusText().includes('paid tier'), api.statusText());
    assert.ok(
      gateway.bodiesFor('GET', '/v1/status').length >= 2,
      'the command caused a real GET /v1/status',
    );
  });

  test('setScope writes default_scope through POST /v1/scope', async () => {
    const result = await run('headwaters.setScope', 'local');
    assert.equal(result.ok, true, result.message);
    assert.equal(result.message, 'Scope is now local.');
    assert.deepEqual(gateway.bodiesFor('POST', '/v1/scope').at(-1), {
      key: 'default_scope',
      value: 'local',
    });
    assert.equal(gateway.state.scope, 'local');
    // The scope is not one of the item's four fields, so it stays in the tooltip — and the command
    // re-reads, so the tooltip must already carry the new value when it returns.
    await waitFor(() => tooltipText().includes('Scope: local'), 'the tooltip to show the new scope');
  });

  test('setScope refuses a scope the daemon does not have, without writing', async () => {
    const before = gateway.bodiesFor('POST', '/v1/scope').length;
    const result = await run('headwaters.setScope', 'turbo');
    assert.equal(result.ok, false);
    assert.ok(result.message.includes('is not a scope'), result.message);
    assert.ok(result.message.includes('auto, free, local, cloud'), 'the accepted values are named');
    assert.equal(gateway.bodiesFor('POST', '/v1/scope').length, before, 'nothing was written');
  });

  test('togglePrivateLock turns Private Mode on, then off, and the item goes purple', async () => {
    const on = await run('headwaters.togglePrivateLock');
    assert.equal(on.ok, true, on.message);
    assert.ok(on.message.includes('nothing leaves this machine'), on.message);
    assert.deepEqual(gateway.bodiesFor('POST', '/v1/scope').at(-1), {
      key: 'private_lock',
      value: true,
    });
    assert.equal(gateway.state.privateLock, true);
    await waitFor(() => colorId() === PRIVATE_LOCK_COLOR, 'the purple lock');
    assert.equal(colorId(), 'charts.purple');
    assert.ok(api.statusText().includes('$(lock) Private'), api.statusText());

    const off = await run('headwaters.togglePrivateLock');
    assert.equal(off.ok, true, off.message);
    assert.ok(off.message.includes('normal routing is back'), off.message);
    assert.equal(gateway.state.privateLock, false);
    await waitFor(() => api.statusBar.item.color === undefined, 'the lock colour to clear');
    assert.ok(!api.statusText().includes('$(lock)'), api.statusText());
  });

  test('togglePrivateLock refuses when the daemon is not reachable, and writes nothing', async () => {
    gateway.authBroken = true;
    const before = gateway.bodiesFor('POST', '/v1/scope').length;
    const result = await run('headwaters.togglePrivateLock');
    assert.equal(result.ok, false);
    assert.ok(result.message.startsWith('The daemon is not reachable:'), result.message);
    assert.equal(gateway.bodiesFor('POST', '/v1/scope').length, before);
  });

  test('pinModel pins a model and states the pin’s real scope in the message', async () => {
    const result = await run('headwaters.pinModel', 'headwaters/free');
    assert.equal(result.ok, true, result.message);
    assert.ok(result.message.startsWith('Pinned headwaters/free.'), result.message);
    assert.ok(
      result.message.includes('This pins front-door MCP tool calls only'),
      'the message says which calls the pin affects',
    );
    assert.ok(result.message.includes('x-headwaters-pin'), 'and which ones it does not');
    const calls = gateway.bodiesFor('POST', '/mcp') as Array<Record<string, unknown>>;
    const pinCall = calls.filter((body) => JSON.stringify(body).includes('headwaters_pin_model'));
    assert.equal(pinCall.length, 1, 'exactly one pin call reached the daemon');
    assert.deepEqual(
      (pinCall[0]['params'] as Record<string, unknown>)['arguments'],
      { model: 'headwaters/free' },
    );
    assert.equal(gateway.state.pinnedModel, 'headwaters/free');
    await waitFor(() => api.statusText().includes('savings'), 'the bar to refresh after the pin');
  });

  test('pinModel reports a refused pin instead of claiming success', async () => {
    const result = await run('headwaters.pinModel', 'not/a-model');
    assert.equal(result.ok, false);
    assert.ok(result.message.includes('The daemon refused the pin'), result.message);
    assert.ok(result.message.includes('is not a model id here'), result.message);
    assert.equal(gateway.state.pinnedModel, null);
  });

  test('pinModel with an empty id clears the pin', async () => {
    gateway.state.pinnedModel = 'headwaters/auto';
    const result = await run('headwaters.pinModel', '');
    assert.equal(result.ok, true, result.message);
    assert.ok(result.message.startsWith('Pin cleared.'), result.message);
    assert.equal(gateway.state.pinnedModel, null);
  });

  test('createHandoff sends the goal and the open file, and opens the packet', async () => {
    const result = await run('headwaters.createHandoff', 'finish HDW-507');
    assert.equal(result.ok, true, result.message);
    assert.equal(result.message, 'Handoff packet pkt_mock_1 created and stored by the daemon.');
    assert.ok(
      result.detail !== undefined && result.detail.includes('Nothing can re-open a stored packet'),
      'the command says what it cannot do',
    );
    assert.ok(result.documentUri !== undefined, 'the packet JSON is open in an editor');
    const calls = gateway.bodiesFor('POST', '/mcp') as Array<Record<string, unknown>>;
    const handoff = calls.filter((body) => JSON.stringify(body).includes('headwaters_handoff'));
    assert.equal(handoff.length, 1);
    const args = (handoff[0]['params'] as Record<string, unknown>)['arguments'] as Record<
      string,
      unknown
    >;
    assert.equal(args['goal'], 'finish HDW-507');
    assert.ok(Array.isArray(args['paths']), 'paths is always an array');
    assert.equal(gateway.state.lastHandoffGoal, 'finish HDW-507');
  });

  test('createHandoff refuses an empty goal without calling the daemon', async () => {
    const before = gateway.bodiesFor('POST', '/mcp').length;
    const result = await run('headwaters.createHandoff', '   ');
    assert.equal(result.ok, false);
    assert.equal(result.message, 'Cancelled; no packet was created.');
    assert.equal(gateway.bodiesFor('POST', '/mcp').length, before);
  });

  test('runDoctor spawns the CLI in the workspace folder and opens the output', async () => {
    const result = await run('headwaters.runDoctor');
    assert.equal(result.ok, true, result.message);
    assert.ok(result.message.includes('the output is open in an untitled editor'), result.message);
    assert.equal(doctorRuns.length, 1);
    assert.equal(path.isAbsolute(doctorRuns[0]), true, doctorRuns[0]);
  });

  test('runDoctor without the CLI on PATH says exactly what is missing and what to do', async () => {
    api.setDoctorRunner(async (): Promise<DoctorRun> => ({
      kind: 'cli-missing',
      text: 'spawn headwaters ENOENT',
      exitCode: null,
    }));
    const result = await run('headwaters.runDoctor');
    assert.equal(result.ok, false);
    assert.ok(result.message.includes('The headwaters CLI is not on PATH'), result.message);
    assert.ok(result.message.includes('There is no HTTP endpoint for doctor'), result.message);
    assert.equal(result.detail, 'spawn headwaters ENOENT');
  });

  test('runDoctor reports a failing doctor as a failure, not a success', async () => {
    api.setDoctorRunner(async (): Promise<DoctorRun> => ({
      kind: 'failed',
      text: '{"ok":false}',
      exitCode: 2,
    }));
    const result = await run('headwaters.runDoctor');
    assert.equal(result.ok, false);
    assert.ok(result.message.includes('exit 2'), result.message);
  });

  test('openDashboard opens the dashboard port from daemon.json', async () => {
    const result = await run('headwaters.openDashboard');
    assert.equal(result.ok, true, result.message);
    assert.deepEqual(opened, [`http://127.0.0.1:${String(port)}/`]);
    assert.equal(result.message, `Opened http://127.0.0.1:${String(port)}/`);
  });

  test('openDashboard refuses plainly when the daemon has not bound the dashboard port', async () => {
    home.writeDaemon(port, MOCK_TOKEN, '9.9.9-mock', { dashboard: null });
    try {
      const result = await run('headwaters.openDashboard');
      assert.equal(result.ok, false);
      assert.ok(result.message.includes('ports.dashboard is null'), result.message);
      assert.deepEqual(opened, [], 'no URL was handed to VS Code');
    } finally {
      home.writeDaemon(port, MOCK_TOKEN);
    }
  });

  test('openDashboard reports a browser VS Code would not open', async () => {
    api.setExternalOpener(async () => false);
    const result = await run('headwaters.openDashboard');
    assert.equal(result.ok, false);
    assert.ok(result.message.includes('could not open'), result.message);
  });
});
