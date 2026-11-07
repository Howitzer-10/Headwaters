/**
 * Daemon discovery tests (HDW-506): the extension must read the port and the token from the state
 * file, and must be able to say precisely why it cannot reach a gateway.
 */

import * as assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { GatewayUnavailable, readGateway, stateDirectory } from '../src/daemon';
import { TempHome } from './fixture';

const TOKEN = 't0ken-for-tests';

async function reasonOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    return error instanceof GatewayUnavailable ? error.reason : 'not-unavailable';
  }
  return 'resolved';
}

suite('daemon state', () => {
  let home: TempHome;

  setup(() => {
    home = new TempHome();
  });

  teardown(() => {
    home.cleanup();
  });

  test('HEADWATERS_HOME moves the state directory', () => {
    const env = { HEADWATERS_HOME: home.dir };
    assert.equal(stateDirectory(env), path.join(path.resolve(home.dir), 'state'));
  });

  test('reads the gateway port and the token from the state files', async () => {
    home.writeDaemon(41337, TOKEN);
    const gateway = await readGateway({ HEADWATERS_HOME: home.dir });
    assert.equal(gateway.url, 'http://127.0.0.1:41337');
    assert.equal(gateway.token, TOKEN, 'the token file is trimmed, and is required');
    assert.equal(gateway.version, '9.9.9-mock');
  });

  test('never guesses port 4000 when ports.gateway is unset', async () => {
    home.writeDaemon(null, TOKEN);
    assert.equal(await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })), 'gateway-not-bound');
  });

  test('a missing state file is a quiet, named reason', async () => {
    assert.equal(await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })), 'no-state-file');
  });

  test('a state file that is not JSON is rejected, not guessed at', async () => {
    fs.mkdirSync(home.stateDir, { recursive: true });
    fs.writeFileSync(path.join(home.stateDir, 'daemon.json'), 'not json', 'utf8');
    assert.equal(
      await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })),
      'unreadable-state-file',
    );
  });

  test('an unknown schema is refused', async () => {
    home.writeDaemon(41337, TOKEN);
    fs.writeFileSync(
      path.join(home.stateDir, 'daemon.json'),
      JSON.stringify({ schema: 2, host: '127.0.0.1', ports: { gateway: 41337 } }),
      'utf8',
    );
    assert.equal(await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })), 'unsupported-schema');
  });

  test('a non-loopback host is refused', async () => {
    home.writeDaemon(41337, TOKEN);
    fs.writeFileSync(
      path.join(home.stateDir, 'daemon.json'),
      JSON.stringify({ schema: 1, host: '10.1.2.3', ports: { gateway: 41337 } }),
      'utf8',
    );
    assert.equal(await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })), 'non-loopback-host');
  });

  test('a missing token file is refused even when the port is known', async () => {
    home.writeDaemon(41337, TOKEN);
    fs.rmSync(path.join(home.stateDir, 'daemon.token'));
    assert.equal(await reasonOf(readGateway({ HEADWATERS_HOME: home.dir })), 'no-token');
  });
});
