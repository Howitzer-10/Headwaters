/**
 * The status bar, both halves (HDW-507).
 *
 * The first suite is pure: `presentBar` and `formatSavings` are functions of a snapshot, so every
 * field can be pinned without a daemon. The second suite runs the real controller against the real
 * loopback mock in `test/mockGateway.ts` — no stubbed `readSnapshot` — because the failure this item
 * most often gets wrong is keeping the last reading on screen after the daemon stops answering, and
 * only an actual socket closing can prove it does not.
 */

import * as assert from 'node:assert/strict';

import * as vscode from 'vscode';

import { forgetClients } from '../src/mcpClient';
import { formatSavings, LiveSnapshot, Snapshot } from '../src/statusClient';
import {
  PRIVATE_LOCK_COLOR,
  presentBar,
  READING_TEXT,
  StatusBarController,
  tierLabel,
  UNAVAILABLE_TEXT,
} from '../src/statusBar';
import { TempHome, tempHomeWithoutState } from './fixture';
import { defaultState, MOCK_TOKEN, MockGateway } from './mockGateway';

function live(overrides: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    kind: 'live',
    tier: 'paid',
    warmModel: 'llama3:8b',
    walls: [],
    lastStatus: 'ok',
    scope: 'auto',
    effectiveScope: 'auto',
    privateLock: false,
    airplane: false,
    pinnedModel: null,
    savings: {
      state: 'available',
      amountUsd: 12.34,
      label: 'Savings: 10 of 10 requests priced.',
      text: '$12.34',
    },
    savingsNote: 'Savings: 10 of 10 requests priced.',
    readAt: Date.now(),
    ...overrides,
  };
}

const NO_ESTIMATE = {
  state: 'unavailable' as const,
  amountUsd: null,
  label: 'the ledger feature is off',
  text: '—',
};

/** A money figure on the item, e.g. `$12.34` or `< $0.01` — distinct from the `$(icon)` syntax. */
function hasFigure(text: string): boolean {
  return /(?:\$[\d]|<\s*\$)/.test(text);
}

suite('Headwaters status bar presentation', () => {
  test('a real zero and "no estimate" never collapse into one another', () => {
    assert.equal(formatSavings(0), '$0.00', 'a zero is a fact and is printed as money');
    assert.equal(formatSavings(null), '—', 'no estimate is an em dash');
    assert.equal(formatSavings(12.34), '$12.34');
    assert.equal(formatSavings(0.004), '< $0.01', 'nonzero but rounds to zero cents');
    assert.equal(formatSavings(-3.2), '-$3.20');
    assert.equal(formatSavings(Number.NaN), '—', 'a broken number is no estimate');
  });

  test('the item renders — when the daemon reports no estimate and $0.00 when it reports zero', () => {
    const noEstimate = presentBar(live({ savings: NO_ESTIMATE }));
    assert.ok(noEstimate.text.includes('savings —'), noEstimate.text);
    assert.ok(!noEstimate.text.includes('$0.00'), 'no estimate must not read as zero savings');
    assert.ok(noEstimate.tooltip.value.includes('not the same as `$0.00`'));

    const zero = presentBar(
      live({
        savings: { state: 'available', amountUsd: 0, label: 'nothing saved yet', text: '$0.00' },
      }),
    );
    assert.ok(zero.text.includes('savings $0.00'), zero.text);
  });

  test('the four fields come from the two surfaces the ticket names', () => {
    const bar = presentBar(live());
    assert.equal(bar.text, '$(pulse) paid tier · warm llama3:8b · no walls · savings $12.34');
    assert.equal(bar.colorId, null, 'no lock, no colour');
  });

  test('active walls are counted, and one wall is not "1 walls"', () => {
    assert.ok(presentBar(live({ walls: ['rate_limit'] })).text.includes('1 wall ·'));
    const two = presentBar(live({ walls: ['rate_limit', 'exhausted'] }));
    assert.ok(two.text.includes('2 walls'), two.text);
    assert.ok(two.tooltip.value.includes('`rate_limit`, `exhausted`'), 'the kinds are in the tooltip');
  });

  test('Private Mode appends the lock and paints the item charts.purple', () => {
    const bar = presentBar(live({ privateLock: true, effectiveScope: 'local' }));
    assert.ok(bar.text.endsWith('· $(lock) Private'), bar.text);
    assert.equal(bar.colorId, PRIVATE_LOCK_COLOR);
    assert.equal(PRIVATE_LOCK_COLOR, 'charts.purple');
    assert.ok(bar.tooltip.value.includes('nothing leaves this machine'));
  });

  test('Airplane Mode does not fake a lock, it explains the effective scope', () => {
    const bar = presentBar(live({ airplane: true, effectiveScope: 'local' }));
    assert.equal(bar.colorId, null);
    assert.ok(!bar.text.includes('$(lock)'));
    assert.ok(bar.tooltip.value.includes('Airplane Mode is on'));
  });

  test('before the first read there are no numbers, only that it is reading', () => {
    const bar = presentBar(undefined);
    assert.equal(bar.text, READING_TEXT);
    assert.ok(!hasFigure(bar.text), 'no amount is on the item yet');
    assert.ok(!bar.text.includes('savings'));
    assert.ok(bar.tooltip.value.includes('No numbers are shown until a read succeeds'));
  });

  test('an unavailable daemon shows no tier, no walls and no savings', () => {
    const snapshot: Snapshot = {
      kind: 'unavailable',
      reason: 'unreachable',
      detail: 'fetch failed: connect ECONNREFUSED 127.0.0.1:41338',
      readAt: Date.now(),
    };
    const bar = presentBar(snapshot);
    assert.equal(bar.text, UNAVAILABLE_TEXT);
    assert.equal(bar.text, '$(error) Headwaters — daemon not reachable');
    assert.ok(!hasFigure(bar.text), 'no savings figure survives a failed read');
    assert.ok(!bar.text.includes('tier'), 'no tier survives a failed read');
    assert.ok(bar.tooltip.value.includes('`unreachable`'));
    assert.ok(bar.tooltip.value.includes('the previous reading is not reused'));
  });

  test('tier words follow D19, which forbids a bare "paid"', () => {
    assert.equal(tierLabel('paid'), 'paid tier');
    assert.equal(tierLabel('free_cloud'), 'free cloud');
    assert.equal(tierLabel('local'), 'local');
    assert.equal(tierLabel('other'), 'other tier');
    assert.equal(tierLabel(null), 'no tier yet');
  });
});

suite('Headwaters status bar against the daemon', () => {
  let gateway: MockGateway;
  let home: TempHome;
  let controller: StatusBarController;
  // Never started: these tests drive `refresh()` themselves so no interval races an assertion.
  const LONG_INTERVAL = 24 * 60 * 60 * 1000;

  function env(): NodeJS.ProcessEnv {
    return { ...process.env, HEADWATERS_HOME: home.dir };
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
    controller = new StatusBarController(env(), LONG_INTERVAL);
  });

  teardown(() => {
    controller.dispose();
    forgetClients();
    Object.assign(gateway.state, defaultState());
    gateway.authBroken = false;
  });

  test('a live read paints the daemon’s own numbers onto the item', async () => {
    const snapshot = await controller.refresh();
    assert.equal(snapshot?.kind, 'live');
    assert.equal(
      controller.item.text,
      '$(pulse) paid tier · warm llama3:8b · no walls · savings $12.34',
    );
    assert.equal(controller.item.command, 'headwaters.openDashboard');
    assert.equal(controller.item.name, 'Headwaters');
    assert.equal(controller.item.color, undefined);
  });

  test('private_lock from the tray block turns the item purple', async () => {
    gateway.state.privateLock = true;
    await controller.refresh();
    const color = controller.item.color;
    assert.ok(color instanceof vscode.ThemeColor, 'the item carries a theme colour');
    assert.equal(color.id, 'charts.purple');
    assert.ok(controller.item.text.endsWith('$(lock) Private'));
  });

  test('a dead daemon replaces the numbers with the unavailable state, not a stale reading', async () => {
    await controller.refresh();
    assert.ok(controller.item.text.includes('$12.34'), 'the live reading is on screen first');

    // The daemon is still listening but no longer accepts the token, which is what a restart looks
    // like from here. The next tick must not keep the old figure.
    gateway.authBroken = true;
    const after = await controller.refresh();
    assert.equal(after?.kind, 'unavailable');
    assert.equal(after?.kind === 'unavailable' ? after.reason : '', 'unauthorized');
    assert.equal(controller.item.text, UNAVAILABLE_TEXT);
    assert.ok(!controller.item.text.includes('12.34'), 'the old savings figure is gone');
    assert.ok(!controller.item.text.includes('paid tier'), 'the old tier is gone');

    gateway.authBroken = false;
    const recovered = await controller.refresh();
    assert.equal(recovered?.kind, 'live', 'the next tick is the retry, and it recovers');
    assert.ok(controller.item.text.includes('savings $12.34'));
  });

  test('a daemon with no state file is unavailable on the very first read', async () => {
    const orphans = tempHomeWithoutState();
    const orphanController = new StatusBarController({ ...process.env, HEADWATERS_HOME: orphans.dir }, LONG_INTERVAL);
    try {
      const snapshot = await orphanController.refresh();
      assert.equal(snapshot?.kind, 'unavailable');
      assert.equal(snapshot?.kind === 'unavailable' ? snapshot.reason : '', 'no-state-file');
      assert.equal(orphanController.item.text, UNAVAILABLE_TEXT);
    } finally {
      orphanController.dispose();
      orphans.cleanup();
    }
  });

  test('a switched-off ledger shows — and an empty week shows $0.00', async () => {
    gateway.state.savings = {
      available: false,
      feature: 'ledger',
      kind: 'estimate',
      reference: null,
      reason: 'the ledger feature is disabled',
      label: 'Savings: the ledger feature is disabled. No estimate is shown rather than a guess.',
    };
    await controller.refresh();
    assert.ok(controller.item.text.includes('savings —'), controller.item.text);

    gateway.state.savings = {
      available: true,
      feature: 'ledger',
      kind: 'estimate',
      reference: { model: 'gpt-4o-mini', price_date: '2026-01-01' },
      totals: { requests: 4, priced_requests: 4, estimated_savings_usd: 0 },
      label: 'Savings: 4 of 4 requests priced against gpt-4o-mini at 2026-01-01.',
    };
    await controller.refresh();
    assert.ok(controller.item.text.includes('savings $0.00'), controller.item.text);
  });

  test('overlapping refreshes share one read instead of stacking requests', async () => {
    let reads = 0;
    const slow = async (): Promise<Snapshot> => {
      reads += 1;
      await new Promise((resolve) => setTimeout(resolve, 100));
      return live();
    };
    const [first, second] = await Promise.all([controller.refresh(slow), controller.refresh(slow)]);
    assert.equal(reads, 1, 'the second call joined the in-flight read');
    assert.equal(first?.kind, 'live');
    assert.equal(second?.kind, 'live', 'the joined call still reports the reading');
  });

  test('a read that throws becomes the unavailable state rather than an unhandled rejection', async () => {
    const snapshot = await controller.refresh(() => Promise.reject(new Error('socket hang up')));
    assert.equal(snapshot?.kind, 'unavailable');
    assert.equal(snapshot?.kind === 'unavailable' ? snapshot.reason : '', 'read-failed');
    assert.equal(controller.item.text, UNAVAILABLE_TEXT);
  });
});
