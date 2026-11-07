/**
 * Where the Headwaters daemon says it is (HDW-506).
 *
 * The daemon writes `<state>/daemon.json` and `<state>/daemon.token` once it is listening; see
 * `headwaters/core/daemon_info.py`, which is the schema this file reads. Nothing here guesses a
 * port: the file is the only source of truth, because the daemon may bind a port other than the
 * configured one and `ports.gateway` stays null until it is bound. The token is read from its
 * sibling file because every gateway route except `GET /health` requires it
 * (`headwaters/gateway/server.py`).
 *
 * The state directory is resolved the same way `headwaters/core/paths.py` resolves it, including
 * the `HEADWATERS_HOME` override so a test can point the extension at a temp directory.
 */

import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import * as path from 'node:path';

/** The `schema` value this extension understands (daemon_info's current schema). */
const STATE_SCHEMA = 1;

const APP_DIR = 'Headwaters';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);

/** `daemon.json` writes `mcp_path`; this is the documented default (`headwaters/core/daemon_info.py`). */
const DEFAULT_MCP_PATH = '/mcp';

/** An address the extension will talk to: loopback only, never anything else. */
export interface Gateway {
  /** e.g. `http://127.0.0.1:41337`, always loopback. */
  readonly url: string;
  /** The daemon's own bearer token. Never logged, never sent anywhere but loopback. */
  readonly token: string;
  /** The daemon version from the state file, used as the models' opaque `version` string. */
  readonly version: string;
}

/**
 * The other daemon listeners (HDW-507), read from the same `daemon.json`.
 *
 * The API port carries `GET /v1/status`, `POST /v1/scope` and the MCP endpoint (`headwaters/
 * gateway/server.py` mounts the MCP app on that port); the dashboard port carries the browser page.
 * Either can be unbound, which is why they are nullable here instead of throwing: a status bar has
 * to say "not bound" rather than pretend the daemon is missing.
 */
export interface DaemonSurfaces {
  /** The loopback host from the state file, e.g. `127.0.0.1`. */
  readonly host: string;
  /** e.g. `http://127.0.0.1:41338`, or null when `ports.api` is not bound. */
  readonly apiUrl: string | null;
  /** e.g. `http://127.0.0.1:41339/`, or null when `ports.dashboard` is not bound. */
  readonly dashboardUrl: string | null;
  /** The MCP path on the API port (`daemon.json`'s `mcp_path`, `/mcp` by default). */
  readonly mcpPath: string;
  /** The daemon's own bearer token, shared by every loopback route. */
  readonly token: string;
  readonly version: string;
}

/** Why the extension cannot reach a gateway right now. Codes are stable; text is for humans. */
export type UnavailableReason =
  | 'no-state-file'
  | 'unreadable-state-file'
  | 'unsupported-schema'
  | 'non-loopback-host'
  | 'gateway-not-bound'
  | 'no-token';

export class GatewayUnavailable extends Error {
  constructor(
    readonly reason: UnavailableReason,
    message: string,
  ) {
    super(message);
    this.name = 'GatewayUnavailable';
  }
}

/**
 * The state directory, mirroring `paths.get_paths()`:
 * `HEADWATERS_HOME/state`, else `%LOCALAPPDATA%\Headwaters` on Windows,
 * `~/Library/Application Support/Headwaters` on macOS, and
 * `$XDG_STATE_HOME/Headwaters` (or `~/.local/state/Headwaters`) elsewhere.
 */
export function stateDirectory(env: NodeJS.ProcessEnv = process.env): string {
  const override = env.HEADWATERS_HOME?.trim();
  if (override) {
    return path.join(path.resolve(override), 'state');
  }
  const localAppData = env.LOCALAPPDATA?.trim();
  switch (process.platform) {
    case 'win32':
      return path.join(localAppData || path.join(homedir(), 'AppData', 'Local'), APP_DIR);
    case 'darwin':
      return path.join(homedir(), 'Library', 'Application Support', APP_DIR);
    default:
      return path.join(env.XDG_STATE_HOME?.trim() || path.join(homedir(), '.local', 'state'), APP_DIR);
  }
}

/**
 * Read the daemon state and token. Throws {@link GatewayUnavailable} with a reason code when the
 * daemon is not running, has not bound its gateway port yet, or wrote something unusable.
 *
 * Callers must treat every reason as "no gateway right now": no prompts, no modals, no retries.
 */
export async function readGateway(env: NodeJS.ProcessEnv = process.env): Promise<Gateway> {
  const state = await readState(env);
  const port = portNamed(state.info['ports'], 'gateway');
  if (port === undefined) {
    throw new GatewayUnavailable(
      'gateway-not-bound',
      'The daemon has not bound its gateway port yet; the state file has no ports.gateway.',
    );
  }
  return {
    url: originOf(state.host, port),
    token: await readToken(path.join(state.directory, 'daemon.token')),
    version: state.version,
  };
}

/**
 * Read the API and dashboard listeners (HDW-507). Throws the same {@link GatewayUnavailable} as
 * {@link readGateway} when the daemon is not running or wrote something unusable, but an unbound
 * `ports.api`/`ports.dashboard` is reported as null rather than thrown: the daemon is up, that
 * listener is not, and the status bar has to say so.
 *
 * This is the same file and the same token as {@link readGateway} — one state source, not two.
 */
export async function readDaemon(env: NodeJS.ProcessEnv = process.env): Promise<DaemonSurfaces> {
  const state = await readState(env);
  const api = portNamed(state.info['ports'], 'api');
  const dashboard = portNamed(state.info['ports'], 'dashboard');
  const rawPath = state.info['mcp_path'];
  const mcpPath =
    typeof rawPath === 'string' && rawPath.startsWith('/') ? rawPath : DEFAULT_MCP_PATH;
  return {
    host: state.host,
    apiUrl: api === undefined ? null : originOf(state.host, api),
    dashboardUrl: dashboard === undefined ? null : `${originOf(state.host, dashboard)}/`,
    mcpPath,
    token: await readToken(path.join(state.directory, 'daemon.token')),
    version: state.version,
  };
}

interface DaemonState {
  readonly directory: string;
  readonly info: Record<string, unknown>;
  readonly host: string;
  readonly version: string;
}

/** The validated state file every reader above shares: schema, loopback host, version. */
async function readState(env: NodeJS.ProcessEnv): Promise<DaemonState> {
  const directory = stateDirectory(env);
  const info = await readStateFile(path.join(directory, 'daemon.json'));

  const schema = info['schema'];
  if (schema !== undefined && schema !== STATE_SCHEMA) {
    throw new GatewayUnavailable(
      'unsupported-schema',
      `The daemon state file has schema ${String(schema)}; this extension understands ${STATE_SCHEMA}.`,
    );
  }

  const rawHost = info['host'];
  const host = typeof rawHost === 'string' && rawHost.trim() !== '' ? rawHost.trim() : '127.0.0.1';
  if (!LOOPBACK_HOSTS.has(host)) {
    throw new GatewayUnavailable(
      'non-loopback-host',
      `The daemon state file points at ${host}, which is not a loopback address.`,
    );
  }

  return {
    directory,
    info,
    host,
    version: typeof info['version'] === 'string' ? info['version'] : '',
  };
}

async function readStateFile(file: string): Promise<Record<string, unknown>> {
  let text: string;
  try {
    text = await readFile(file, 'utf8');
  } catch (error) {
    if (isNotFound(error)) {
      throw new GatewayUnavailable('no-state-file', `There is no daemon state file at ${file}.`);
    }
    throw new GatewayUnavailable(
      'unreadable-state-file',
      `The daemon state file at ${file} is unreadable.`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new GatewayUnavailable(
      'unreadable-state-file',
      `The daemon state file at ${file} is not JSON.`,
    );
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new GatewayUnavailable(
      'unreadable-state-file',
      `The daemon state file at ${file} is not an object.`,
    );
  }
  return parsed as Record<string, unknown>;
}

function portNamed(ports: unknown, name: string): number | undefined {
  if (typeof ports !== 'object' || ports === null) {
    return undefined;
  }
  const value = (ports as Record<string, unknown>)[name];
  return typeof value === 'number' && Number.isInteger(value) && value > 0 && value <= 65535
    ? value
    : undefined;
}

function originOf(host: string, port: number): string {
  return `http://${host === '::1' ? '[::1]' : host}:${port}`;
}

async function readToken(file: string): Promise<string> {
  let text: string;
  try {
    text = await readFile(file, 'utf8');
  } catch {
    throw new GatewayUnavailable('no-token', `There is no daemon token at ${file}.`);
  }
  const token = text.trim();
  if (token === '') {
    throw new GatewayUnavailable('no-token', `The daemon token at ${file} is empty.`);
  }
  return token;
}

function isNotFound(error: unknown): boolean {
  return (
    typeof error === 'object' && error !== null && (error as { code?: unknown }).code === 'ENOENT'
  );
}
