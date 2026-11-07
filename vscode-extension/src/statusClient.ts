/**
 * The status bar's two read paths (HDW-507).
 *
 * There is deliberately no third source and no aggregate endpoint:
 *
 *  - `GET /v1/status` on the API port carries the `tray` block (`headwaters/daemon/service.py`
 *    `tray_status()`): scope, effective scope, tier of the last routed model, warm model, active
 *    wall kinds, last status, `below_floor`. It carries **no savings**.
 *  - The MCP tool `headwaters_status` (`headwaters/mcp/tools/status.py`) is the only JSON surface
 *    that carries savings (`engine.registry.dashboard_summary()`), the pinned model, and the wall
 *    details. A disabled ledger feature yields savings `null`/`available:false`, never an error.
 *
 * The two are read independently on purpose: an MCP failure must not blank the tier, and a tray
 * failure must not be papered over with an old number. Every failure returns a value that says so.
 */

import { DaemonSurfaces, GatewayUnavailable, readDaemon } from './daemon';
import { clientFor } from './mcpClient';

/** `headwaters/core/rules.py` `Scope`. */
export const SCOPES = ['auto', 'free', 'local', 'cloud'] as const;
export type Scope = (typeof SCOPES)[number];

/** The three settings `POST /v1/scope` accepts (`rules_edit.EDITABLE_KEYS`). */
export type EditableKey = 'default_scope' | 'private_lock' | 'strategy';

const STATUS_TIMEOUT_MS = 10_000;

/**
 * Savings as the bar may show it. `unavailable` and `$0.00` are different facts and stay apart:
 * "the ledger has no estimate" is not "you saved nothing".
 */
export interface SavingsView {
  readonly state: 'available' | 'unavailable';
  /** The estimate in USD, or null when the daemon reported none. */
  readonly amountUsd: number | null;
  /** The daemon's own label, or the reason it has no estimate. */
  readonly label: string;
  /** The string the bar shows: `$12.34`, `$0.00`, `< $0.01`, or an em dash. */
  readonly text: string;
}

export interface LiveSnapshot {
  readonly kind: 'live';
  readonly tier: string | null;
  readonly warmModel: string | null;
  readonly walls: readonly string[];
  readonly lastStatus: string | null;
  readonly scope: string | null;
  readonly effectiveScope: string | null;
  readonly privateLock: boolean;
  readonly airplane: boolean;
  readonly pinnedModel: string | null;
  readonly savings: SavingsView;
  /** Where the number did (or did not) come from, in one clause for the tooltip. */
  readonly savingsNote: string;
  readonly readAt: number;
}

export interface UnavailableSnapshot {
  readonly kind: 'unavailable';
  /** Stable code for tests and the report: `no-state-file`, `unreachable`, `unauthorized`, ... */
  readonly reason: string;
  readonly detail: string;
  readonly readAt: number;
}

export type Snapshot = LiveSnapshot | UnavailableSnapshot;

/** What `POST /v1/scope` answers (`headwaters/daemon/service.py`). */
export interface ScopeReply {
  readonly ok: boolean;
  readonly key: string;
  readonly value: unknown;
  readonly previous: unknown;
  readonly defaultScope: string | null;
  readonly privateLock: boolean | null;
  readonly airplane: boolean | null;
  readonly applied: boolean;
  readonly reason: string;
}

/**
 * Read both surfaces. Resolves to an {@link UnavailableSnapshot} when the daemon itself cannot be
 * reached — that is the state the bar must show instead of its last reading.
 */
export async function readSnapshot(
  env: NodeJS.ProcessEnv = process.env,
  signal?: AbortSignal,
): Promise<Snapshot> {
  const readAt = Date.now();
  let daemon: DaemonSurfaces;
  try {
    daemon = await readDaemon(env);
  } catch (error) {
    if (error instanceof GatewayUnavailable) {
      return { kind: 'unavailable', reason: error.reason, detail: error.message, readAt };
    }
    return { kind: 'unavailable', reason: 'unreadable-state-file', detail: describe(error), readAt };
  }
  if (daemon.apiUrl === null) {
    return {
      kind: 'unavailable',
      reason: 'api-not-bound',
      detail: 'The daemon has not bound its API port, so there is no status to read.',
      readAt,
    };
  }

  let tray: TrayView;
  try {
    tray = await readTray(daemon, signal);
  } catch (error) {
    return { kind: 'unavailable', reason: failureReason(error), detail: describe(error), readAt };
  }

  const savings = await readSavings(daemon, signal);

  return {
    kind: 'live',
    tier: tray.tier,
    warmModel: tray.warmModel,
    walls: tray.walls,
    lastStatus: tray.lastStatus,
    scope: tray.scope,
    effectiveScope: tray.effectiveScope,
    privateLock: tray.privateLock,
    airplane: tray.airplane,
    pinnedModel: savings.pinnedModel,
    savings: savings.view,
    savingsNote: savings.note,
    readAt,
  };
}

/** `POST /v1/scope` — the same route the tray writes through, so the reload happens daemon-side. */
export async function postScope(
  env: NodeJS.ProcessEnv,
  key: EditableKey,
  value: string | boolean,
  signal?: AbortSignal,
): Promise<ScopeReply> {
  const daemon = await readDaemon(env);
  if (daemon.apiUrl === null) {
    throw new Error('The daemon has not bound its API port, so no setting can be written.');
  }
  const timeout = AbortSignal.timeout(STATUS_TIMEOUT_MS);
  const response = await fetch(`${daemon.apiUrl}/v1/scope`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${daemon.token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ key, value }),
    signal: signal === undefined ? timeout : AbortSignal.any([signal, timeout]),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(
      `The daemon refused ${key} with ${String(response.status)}` +
        (text.trim() === '' ? '.' : `: ${text.trim()}`),
    );
  }
  const parsed: unknown = text.trim() === '' ? {} : JSON.parse(text);
  const record = isRecord(parsed) ? parsed : {};
  return {
    ok: record['ok'] === true,
    key: textOf(record['key']) ?? key,
    value: record['value'],
    previous: record['previous'],
    defaultScope: textOf(record['default_scope']),
    privateLock: booleanOf(record['private_lock']),
    airplane: booleanOf(record['airplane']),
    applied: record['applied'] !== false,
    reason: textOf(record['reason']) ?? '',
  };
}

/** `$0.00` for a real zero, an em dash for "no estimate", `< $0.01` for a nonzero rounding to zero. */
export function formatSavings(amountUsd: number | null): string {
  if (amountUsd === null || !Number.isFinite(amountUsd)) {
    return '—';
  }
  if (amountUsd === 0) {
    return '$0.00';
  }
  const sign = amountUsd < 0 ? '-' : '';
  const magnitude = Math.abs(amountUsd);
  if (magnitude < 0.01) {
    return `${sign}< $0.01`;
  }
  return `${sign}$${magnitude.toFixed(2)}`;
}

interface TrayView {
  readonly tier: string | null;
  readonly warmModel: string | null;
  readonly walls: readonly string[];
  readonly lastStatus: string | null;
  readonly scope: string | null;
  readonly effectiveScope: string | null;
  readonly privateLock: boolean;
  readonly airplane: boolean;
}

async function readTray(daemon: DaemonSurfaces, signal?: AbortSignal): Promise<TrayView> {
  const timeout = AbortSignal.timeout(STATUS_TIMEOUT_MS);
  const response = await fetch(`${daemon.apiUrl}/v1/status`, {
    headers: { authorization: `Bearer ${daemon.token}` },
    signal: signal === undefined ? timeout : AbortSignal.any([signal, timeout]),
  });
  if (!response.ok) {
    throw new HttpFailure(response.status, await safeText(response));
  }
  const parsed: unknown = await response.json();
  const body = isRecord(parsed) ? parsed : {};
  const tray = isRecord(body['tray']) ? body['tray'] : {};
  return {
    tier: textOf(tray['tier']),
    warmModel: textOf(tray['warm_model']),
    walls: stringList(tray['walls']),
    lastStatus: textOf(tray['last_status']),
    scope: textOf(tray['scope']) ?? textOf(body['scope']),
    effectiveScope: textOf(tray['effective_scope']),
    privateLock: booleanOf(body['private_lock']) ?? false,
    airplane: booleanOf(body['airplane']) ?? false,
  };
}

/**
 * Savings and the pinned model from `headwaters_status`. An MCP failure leaves the tier and walls
 * on screen (they came from the tray) and states why savings are missing in the tooltip.
 */
async function readSavings(
  daemon: DaemonSurfaces,
  signal?: AbortSignal,
): Promise<{ view: SavingsView; note: string; pinnedModel: string | null }> {
  if (daemon.apiUrl === null) {
    return { ...unavailableSavings('the API port is not bound'), pinnedModel: null };
  }
  const client = clientFor({ apiUrl: daemon.apiUrl, token: daemon.token, mcpPath: daemon.mcpPath });
  let status: Record<string, unknown>;
  try {
    const result = await client.callTool('headwaters_status', {}, signal);
    if (result.value === undefined) {
      return {
        ...unavailableSavings('the daemon sent no structured status'),
        pinnedModel: null,
      };
    }
    status = result.value;
  } catch (error) {
    return {
      ...unavailableSavings(`the MCP status call failed: ${describe(error)}`),
      pinnedModel: null,
    };
  }
  return { ...savingsFrom(status['savings']), pinnedModel: textOf(status['pinned_model']) };
}

function savingsFrom(raw: unknown): { view: SavingsView; note: string } {
  // `savings` is null when the ledger feature is off (`dashboard_summary()` returns None).
  if (raw === null || raw === undefined) {
    return {
      view: { state: 'unavailable', amountUsd: null, label: 'no savings ledger', text: '—' },
      note: 'no savings estimate: the ledger feature is not contributing a summary',
    };
  }
  if (!isRecord(raw)) {
    return {
      view: { state: 'unavailable', amountUsd: null, label: 'unreadable savings', text: '—' },
      note: 'no savings estimate: the daemon sent an unreadable savings block',
    };
  }
  if (raw['available'] === false) {
    const reason = textOf(raw['reason']) ?? 'the daemon reported no estimate';
    const label = textOf(raw['label']) ?? reason;
    return {
      view: { state: 'unavailable', amountUsd: null, label, text: '—' },
      note: `no savings estimate: ${reason}`,
    };
  }
  const totals = isRecord(raw['totals']) ? raw['totals'] : {};
  const amount = numberOf(totals['estimated_savings_usd']) ?? numberOf(raw['estimated_savings_usd']);
  const label = textOf(raw['label']) ?? 'savings estimate';
  return {
    view: { state: 'available', amountUsd: amount, label, text: formatSavings(amount) },
    note: amount === null ? `no estimate for this window: ${label}` : label,
  };
}

function unavailableSavings(reason: string): { view: SavingsView; note: string } {
  return {
    view: { state: 'unavailable', amountUsd: null, label: reason, text: '—' },
    note: reason,
  };
}

class HttpFailure extends Error {
  constructor(
    readonly status: number,
    readonly body: string,
  ) {
    super(`HTTP ${String(status)}${body === '' ? '' : `: ${body.slice(0, 200)}`}`);
    this.name = 'HttpFailure';
  }
}

function failureReason(error: unknown): string {
  if (error instanceof HttpFailure) {
    if (error.status === 401 || error.status === 403) {
      return 'unauthorized';
    }
    return `http-${String(error.status)}`;
  }
  return 'unreachable';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textOf(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function booleanOf(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((entry): entry is string => typeof entry === 'string' && entry !== '');
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return '';
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
