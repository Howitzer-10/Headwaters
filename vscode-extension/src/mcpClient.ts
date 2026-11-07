/**
 * A minimal MCP client over streamable HTTP (HDW-507).
 *
 * The daemon mounts an MCP app on the API port (`headwaters/gateway/server.py` builds it with
 * `mcp_app=endpoint.app`), so one JSON-RPC session reaches `headwaters_status` — the only JSON
 * surface carrying savings — and the write tools. There is no SDK dependency on purpose: the
 * handshake is three requests, and a dependency would have to be trusted with the daemon token.
 *
 * The handshake, in the order the transport demands:
 *   1. `POST <mcpPath>` with `Accept: application/json, text/event-stream` — a request without
 *      `text/event-stream` is a 406.
 *   2. `notifications/initialized` (202, no body) carrying the `mcp-session-id` we were given.
 *   3. `tools/call` carrying both `mcp-session-id` and `mcp-protocol-version`; omitting either is
 *      a 400 ("Missing session ID").
 *
 * A session can be rejected or expire while the extension is running, so {@link McpClient.callTool}
 * re-initializes exactly once before giving up. It does not loop: a second rejection is reported.
 */

/** The protocol version the daemon's handshake answered during the HDW-507 probe. */
export const MCP_PROTOCOL_VERSION = '2025-11-25';

const SESSION_HEADER = 'mcp-session-id';
const PROTOCOL_HEADER = 'mcp-protocol-version';
const REQUEST_TIMEOUT_MS = 15_000;

/** A JSON-RPC or transport failure. `message` is safe to show: it never contains the token. */
export class McpError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'McpError';
  }
}

/** A tool call the daemon answered. `value` is `structuredContent` when it sent one. */
export interface ToolResult {
  readonly value: Record<string, unknown> | undefined;
  readonly text: string;
}

interface JsonRpcMessage {
  readonly id?: number;
  readonly result?: Record<string, unknown>;
  readonly error?: { code?: number; message?: string };
}

export class McpClient {
  private sessionId: string | undefined;
  private nextId = 1;

  constructor(
    private readonly baseUrl: string,
    private token: string,
    private readonly mcpPath: string,
  ) {}

  get endpoint(): string {
    return `${this.baseUrl}${this.mcpPath}`;
  }

  /** A rotated token means a new daemon, so the old session goes with the old token. */
  setToken(token: string): void {
    if (token !== this.token) {
      this.token = token;
      this.sessionId = undefined;
    }
  }

  /**
   * Call one tool. Re-initializes once when the daemon rejects the session (a restarting daemon
   * forgets every session); a second rejection is thrown with a message saying so.
   */
  async callTool(
    name: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<ToolResult> {
    const hadSession = this.sessionId !== undefined;
    try {
      return await this.callOnce(name, args, signal);
    } catch (error) {
      if (!hadSession || !isSessionRejection(error)) {
        throw error;
      }
      this.sessionId = undefined;
      try {
        return await this.callOnce(name, args, signal);
      } catch (retry) {
        throw new McpError(
          `The daemon rejected the MCP session twice (${describe(retry)}). ` +
            'Restarting the daemon clears it; the extension retries a session once, not in a loop.',
          retry instanceof McpError ? retry.status : undefined,
        );
      }
    }
  }

  private async callOnce(
    name: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<ToolResult> {
    await this.ensureSession(signal);
    const reply = await this.rpc('tools/call', { name, arguments: args }, true, signal);
    const result = reply.result ?? {};
    const structured = result['structuredContent'];
    const value =
      typeof structured === 'object' && structured !== null && !Array.isArray(structured)
        ? (structured as Record<string, unknown>)
        : undefined;
    const text = contentText(result['content']);
    if (result['isError'] === true) {
      throw new McpError(text === '' ? `The daemon reported a tool error from ${name}.` : text);
    }
    return { value: value ?? parseObject(text), text };
  }

  private async ensureSession(signal?: AbortSignal): Promise<void> {
    if (this.sessionId !== undefined) {
      return;
    }
    const reply = await this.rpc(
      'initialize',
      {
        protocolVersion: MCP_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: 'headwaters-vscode', version: '0.2.0' },
      },
      false,
      signal,
    );
    if (reply.sessionId === undefined) {
      throw new McpError(
        'The daemon answered initialize without an mcp-session-id header, so no tool call can follow.',
      );
    }
    this.sessionId = reply.sessionId;
    await this.notifyInitialized(signal);
  }

  private async notifyInitialized(signal?: AbortSignal): Promise<void> {
    await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' }, true, signal);
  }

  private async rpc(
    method: string,
    params: Record<string, unknown>,
    withSession: boolean,
    signal?: AbortSignal,
  ): Promise<{ result: Record<string, unknown>; sessionId?: string }> {
    const id = this.nextId++;
    const response = await this.post({ jsonrpc: '2.0', id, method, params }, withSession, signal);
    const sessionId = response.headers.get(SESSION_HEADER) ?? undefined;
    const message = pickReply(await readBody(response), id);
    if (message?.error !== undefined) {
      const code = message.error.code === undefined ? '' : ` (${String(message.error.code)})`;
      throw new McpError(
        `${method} failed: ${message.error.message ?? 'the daemon sent no message'}${code}`,
      );
    }
    if (message === undefined) {
      throw new McpError(`The daemon sent no JSON-RPC reply to ${method}.`);
    }
    return { result: message.result ?? {}, sessionId };
  }

  private async post(
    payload: Record<string, unknown>,
    withSession: boolean,
    signal?: AbortSignal,
  ): Promise<Response> {
    const headers: Record<string, string> = {
      authorization: `Bearer ${this.token}`,
      'content-type': 'application/json',
      accept: 'application/json, text/event-stream',
    };
    if (withSession && this.sessionId !== undefined) {
      headers[SESSION_HEADER] = this.sessionId;
      headers[PROTOCOL_HEADER] = MCP_PROTOCOL_VERSION;
    }
    const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    const response = await fetch(this.endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: signal === undefined ? timeout : AbortSignal.any([signal, timeout]),
    });
    if (!response.ok) {
      const detail = await safeText(response);
      throw new McpError(
        `The daemon answered ${String(response.status)} at ${this.mcpPath}` +
          (detail === '' ? '.' : `: ${detail}`),
        response.status,
      );
    }
    return response;
  }
}

/**
 * Sessions per daemon endpoint, so a `tools/call` can outlive one request and a restarted daemon's
 * rejected session is actually observable (and recoverable) rather than reinvented every call.
 * Keyed by origin and MCP path; the token is refreshed on reuse, which also drops a stale session.
 */
const clients = new Map<string, McpClient>();

export function clientFor(daemon: {
  readonly apiUrl: string;
  readonly token: string;
  readonly mcpPath: string;
}): McpClient {
  const key = `${daemon.apiUrl}${daemon.mcpPath}`;
  const existing = clients.get(key);
  if (existing !== undefined) {
    existing.setToken(daemon.token);
    return existing;
  }
  const created = new McpClient(daemon.apiUrl, daemon.token, daemon.mcpPath);
  clients.set(key, created);
  return created;
}

/** Drop every cached session. Used by `deactivate` and by the tests. */
export function forgetClients(): void {
  clients.clear();
}

/** A 400/404 on a request that carried a session means the session is gone, not that we asked wrong. */
function isSessionRejection(error: unknown): boolean {
  return (
    error instanceof McpError &&
    (error.status === 400 || error.status === 404) &&
    /session/i.test(error.message)
  );
}

/** Flatten an SSE body to its data lines, so one parser reads both content types. */
async function readBody(response: Response): Promise<string> {
  const type = response.headers.get('content-type') ?? '';
  const text = await safeText(response);
  if (!type.includes('text/event-stream')) {
    return text;
  }
  return text
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice('data:'.length).trim())
    .join('\n');
}

function pickReply(body: string, id: number): JsonRpcMessage | undefined {
  const messages: JsonRpcMessage[] = [];
  for (const line of body.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{')) {
      continue;
    }
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        messages.push(parsed as JsonRpcMessage);
      }
    } catch {
      continue;
    }
  }
  return messages.find((message) => message.id === id) ?? messages.find((m) => m.result !== undefined);
}

function contentText(content: unknown): string {
  if (!Array.isArray(content)) {
    return '';
  }
  const parts: string[] = [];
  for (const part of content) {
    if (typeof part === 'object' && part !== null) {
      const text = (part as { text?: unknown }).text;
      if (typeof text === 'string' && text !== '') {
        parts.push(text);
      }
    }
  }
  return parts.join('\n');
}

function parseObject(text: string): Record<string, unknown> | undefined {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
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
