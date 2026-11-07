/**
 * The MCP transport, including the case the transport gets wrong (HDW-507).
 *
 * `/mcp` is streamable HTTP with a session id: `initialize` returns one in the `mcp-session-id`
 * header, every later request must carry it, and a daemon restart (or a session idle timeout)
 * invalidates it. The extension must recover from that exactly once — a fresh `initialize` and a
 * single retry — and then say so, because a retry loop against a broken daemon is a polling storm
 * dressed up as resilience.
 *
 * The mock in `test/mockGateway.ts` implements the real handshake, including the 406 a request gets
 * without `text/event-stream` and the 400 `Missing session ID` a request gets with a dead session.
 */

import * as assert from 'node:assert/strict';

import { clientFor, forgetClients } from '../src/mcpClient';
import { readSnapshot } from '../src/statusClient';
import { TempHome } from './fixture';
import { MOCK_TOKEN, MockGateway } from './mockGateway';

function initializes(gateway: MockGateway): number {
  return gateway.bodiesFor('POST', '/mcp').filter((body) => messageMethod(body) === 'initialize')
    .length;
}

function messageMethod(body: unknown): string | undefined {
  if (typeof body !== 'object' || body === null) {
    return undefined;
  }
  const method = (body as Record<string, unknown>)['method'];
  return typeof method === 'string' ? method : undefined;
}

suite('Headwaters MCP session handling', () => {
  let gateway: MockGateway;
  let home: TempHome;

  function env(): NodeJS.ProcessEnv {
    return { ...process.env, HEADWATERS_HOME: home.dir };
  }

  function client(): ReturnType<typeof clientFor> {
    return clientFor({ apiUrl: gateway.url, token: MOCK_TOKEN, mcpPath: '/mcp' });
  }

  suiteSetup(async function () {
    this.timeout(60000);
    gateway = new MockGateway();
    await gateway.start();
    home = new TempHome();
    home.writeDaemon(Number(new URL(gateway.url).port), MOCK_TOKEN);
  });

  suiteTeardown(async () => {
    forgetClients();
    await gateway.stop();
    home.cleanup();
  });

  setup(() => {
    forgetClients();
    gateway.sessionMode = 'ok';
  });

  test('one handshake serves later calls instead of a new session per request', async () => {
    await client().callTool('headwaters_status', {});
    await client().callTool('headwaters_status', {});
    const calls = gateway.bodiesFor('POST', '/mcp').filter((b) => messageMethod(b) === 'tools/call');
    assert.equal(calls.length, 2, 'both tool calls reached the daemon');
    assert.equal(initializes(gateway), 1, 'the second call reused the session');
  });

  test('the result is read out of the JSON-RPC reply, text and structuredContent alike', async () => {
    const result = await client().callTool('headwaters_status', {});
    assert.equal(result.value?.['ok'], true);
    assert.ok(result.text.includes('"pinned_model"'), result.text);
  });

  test('a rejected session is re-initialized once and the call still succeeds', async () => {
    await client().callTool('headwaters_status', {});
    const before = initializes(gateway);
    gateway.sessionMode = 'expire-once';

    const result = await client().callTool('headwaters_status', {});
    assert.equal(result.value?.['ok'], true, 'the retry returned real data, not a failure');
    assert.equal(initializes(gateway) - before, 1, 'exactly one new session was negotiated');
  });

  test('an expired session mid-read does not lose the tray fields it already had', async () => {
    await readSnapshot(env());
    gateway.sessionMode = 'expire-once';
    const snapshot = await readSnapshot(env());
    assert.equal(snapshot.kind, 'live');
    assert.equal(snapshot.kind === 'live' ? snapshot.savings.state : '', 'available');
  });

  test('a session that keeps being rejected fails once, with a stated error and no loop', async () => {
    // A session that worked first, which is the only way this path is reachable: a daemon that
    // restart-loops under an extension that already had a session.
    await client().callTool('headwaters_status', {});
    gateway.sessionMode = 'always-reject';
    const before = initializes(gateway);
    await assert.rejects(
      () => client().callTool('headwaters_status', {}),
      /rejected the MCP session twice/,
      'the error names the failure instead of retrying forever',
    );
    assert.equal(
      initializes(gateway) - before,
      1,
      'exactly one re-initialize, and no more',
    );
  });

  test('a handshake the daemon disowns immediately is reported, not retried', async () => {
    gateway.sessionMode = 'always-reject';
    const before = initializes(gateway);
    await assert.rejects(
      () => client().callTool('headwaters_status', {}),
      /answered 400 at \/mcp/,
      'there is no session to re-establish, so a retry would be the same request twice',
    );
    assert.equal(initializes(gateway) - before, 1);
  });

  test('the status bar says savings are missing when the MCP call is the thing that failed', async () => {
    const first = await readSnapshot(env());
    assert.equal(first.kind === 'live' ? first.savings.state : '', 'available');
    // The daemon restarts its MCP sessions while the tray stays up: the tier must survive, the
    // savings must not.
    gateway.sessionMode = 'always-reject';
    const snapshot = await readSnapshot(env());
    assert.equal(snapshot.kind, 'live', 'the tray read is independent of the MCP read');
    assert.ok(snapshot.kind === 'live');
    assert.equal(snapshot.savings.state, 'unavailable');
    assert.equal(snapshot.savings.text, '—');
    assert.ok(snapshot.savingsNote.includes('rejected the MCP session twice'), snapshot.savingsNote);
    assert.equal(snapshot.tier, 'paid', 'the tier still came from the tray');
  });
});
