/**
 * A real loopback HTTP gateway for the extension tests (HDW-506).
 *
 * It is not a stub of the provider: it is a `node:http` server that speaks the same wire protocol as
 * `headwaters/gateway/openai_routes.py` over a real socket, so the extension's own `GatewayClient`
 * reaches it exactly as it would reach the daemon. Routes: `GET /v1/models`, streaming
 * `POST /v1/chat/completions` (SSE), and `POST /v1/messages/count_tokens`. Everything except a
 * missing or wrong bearer token gets a 401 with `WWW-Authenticate: Bearer`, as the real gateway does.
 */

import * as http from 'node:http';
import { AddressInfo } from 'node:net';

export interface MockCard {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly capabilities: {
    max_input_tokens: number;
    max_output_tokens: number;
    image_input: boolean;
    tool_calling: boolean;
    basis: string;
    entries: number;
  };
}

export interface RecordedRequest {
  readonly method: string;
  readonly path: string;
  readonly authorized: boolean;
  readonly body?: unknown;
}

export const MOCK_TOKEN = 't0ken-for-tests';

/** Model ids the mock's `headwaters_pin_model` accepts; anything else is refused (`unknown_model`). */
export const MOCK_MODELS = ['headwaters/auto', 'headwaters/free', 'headwaters/local', 'llama3:8b'];

/**
 * The mutable daemon state the two HDW-507 status surfaces read (HDW-507).
 *
 * `GET /v1/status` renders the `tray` block from here and the MCP `headwaters_status` renders
 * savings and the pinned model from here, exactly as the real daemon splits the two. A test flips a
 * field and reads the bar.
 */
export interface MockState {
  scope: string;
  privateLock: boolean;
  airplane: boolean;
  tier: string | null;
  warmModel: string | null;
  walls: string[];
  lastStatus: string | null;
  belowFloor: boolean;
  pinnedModel: string | null;
  savings: Record<string, unknown> | null;
  lastHandoffGoal: string | null;
  lastScopeWrite: { key: string; value: unknown } | null;
}

export function defaultState(): MockState {
  return {
    scope: 'auto',
    privateLock: false,
    airplane: false,
    tier: 'paid',
    warmModel: 'llama3:8b',
    walls: [],
    lastStatus: 'ok',
    belowFloor: false,
    pinnedModel: null,
    savings: {
      available: true,
      feature: 'ledger',
      kind: 'estimate',
      reference: { model: 'gpt-4o-mini', price_date: '2026-01-01' },
      totals: {
        requests: 10,
        priced_requests: 10,
        unpriced_requests: 0,
        estimated_savings_usd: 12.34,
      },
      label: 'Savings: 10 of 10 requests priced against gpt-4o-mini at 2026-01-01.',
    },
    lastHandoffGoal: null,
    lastScopeWrite: null,
  };
}

/** How the mock treats an MCP session on a `tools/call` (HDW-507). */
export type SessionMode = 'ok' | 'expire-once' | 'always-reject';

export const MOCK_CARDS: MockCard[] = [
  card('headwaters/auto', 'Headwaters Auto', true),
  card('headwaters/free', 'Headwaters Free', true),
  card('headwaters/local', 'Headwaters Local', false),
];

function card(id: string, name: string, toolCalling: boolean): MockCard {
  return {
    id,
    name,
    description: `${name} routing alias.`,
    capabilities: {
      max_input_tokens: 6554,
      max_output_tokens: 984,
      image_input: true,
      tool_calling: toolCalling,
      basis: 'catalog_floor',
      entries: 3,
    },
  };
}

/** Text the mock streams before any tool call. */
export const MOCK_TEXT_CHUNKS = ['Hello ', 'from ', 'Headwaters.'];

/** The tool call the mock streams when the request carries tools. */
export const MOCK_TOOL_CALL = { id: 'call_7', name: 'read_file', arguments: '{"path":"a.txt"}' };

export class MockGateway {
  private server: http.Server | undefined;
  private port = 0;
  readonly requests: RecordedRequest[] = [];
  readonly state: MockState = defaultState();
  /** Answer every authenticated request with 401, as a daemon whose token was rotated does. */
  authBroken = false;
  /** How a `tools/call` treats its session; see {@link SessionMode}. */
  sessionMode: SessionMode = 'ok';
  private sessions = new Set<string>();
  private sessionCounter = 0;

  get url(): string {
    return `http://127.0.0.1:${String(this.port)}`;
  }

  get portNumber(): number {
    return this.port;
  }

  /** Forget every session, as a restarted daemon does. */
  expireSessions(): void {
    this.sessions.clear();
  }

  /** The bodies of the requests that carried one path and method, oldest first. */
  bodiesFor(method: string, path: string): unknown[] {
    return this.requests
      .filter((entry) => entry.method === method && entry.path === path)
      .map((entry) => entry.body);
  }

  async start(): Promise<void> {
    const server = http.createServer((request, response) => {
      this.handle(request, response).catch(() => {
        response.writeHead(500, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ error: { message: 'mock failure' } }));
      });
    });
    this.server = server;
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    this.port = (server.address() as AddressInfo).port;
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = undefined;
    if (server === undefined) {
      return;
    }
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  private async handle(request: http.IncomingMessage, response: http.ServerResponse): Promise<void> {
    const path = request.url ?? '/';
    const authorized = request.headers.authorization === `Bearer ${MOCK_TOKEN}`;
    const body = await readBody(request);
    this.requests.push({ method: request.method ?? '', path, authorized, body });

    if (!authorized) {
      response.writeHead(401, {
        'Content-Type': 'application/json',
        'WWW-Authenticate': 'Bearer',
      });
      response.end(JSON.stringify({ error: { message: 'unauthorized', type: 'auth', code: '401' } }));
      return;
    }

    if (this.authBroken) {
      response.writeHead(401, {
        'Content-Type': 'application/json',
        'WWW-Authenticate': 'Bearer',
      });
      response.end(JSON.stringify({ error: { message: 'unauthorized', type: 'auth', code: '401' } }));
      return;
    }

    if (request.method === 'GET' && path === '/v1/models') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ object: 'list', data: MOCK_CARDS }));
      return;
    }

    if (request.method === 'POST' && path === '/v1/chat/completions') {
      this.stream(response, body);
      return;
    }

    if (request.method === 'POST' && path === '/v1/messages/count_tokens') {
      const text = countedText(body);
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ input_tokens: Math.ceil(text.length / 4) }));
      return;
    }

    if (request.method === 'GET' && path === '/v1/status') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(this.statusPayload()));
      return;
    }

    if (request.method === 'POST' && path === '/v1/scope') {
      this.writeScope(response, body);
      return;
    }

    if (request.method === 'POST' && path === '/mcp') {
      this.mcp(response, request, body);
      return;
    }

    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: { message: 'not found' } }));
  }

  /** The `GET /v1/status` body: the real daemon's top level plus the `tray` block. */
  statusPayload(): Record<string, unknown> {
    const s = this.state;
    return {
      pid: process.pid,
      version: '9.9.9-mock',
      host: '127.0.0.1',
      mcp_path: '/mcp',
      ports: { api: this.port, gateway: this.port, dashboard: this.port },
      scope: s.scope,
      private_lock: s.privateLock,
      airplane: s.airplane,
      catalog: { source: 'mock', version: '2026.01.01' },
      keep_warm: null,
      last_refresh: '2026-01-01T00:00:00Z',
      tray: {
        scope: s.scope,
        effective_scope: s.privateLock || s.airplane ? 'local' : s.scope,
        tier: s.tier,
        warm_model: s.warmModel,
        walls: [...s.walls],
        last_status: s.lastStatus,
        below_floor: s.belowFloor,
        keys_set: 1,
        anonymous_provider: false,
      },
    };
  }

  /** `POST /v1/scope`: the same three keys the real route accepts, applied in place. */
  private writeScope(response: http.ServerResponse, body: unknown): void {
    const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
    const key = record['key'];
    const value = record['value'];
    const known = key === 'default_scope' || key === 'private_lock' || key === 'strategy';
    if (!known) {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ ok: false, reason: `unknown key ${String(key)}` }));
      return;
    }
    this.state.lastScopeWrite = { key: String(key), value };
    let previous: unknown;
    if (key === 'default_scope') {
      previous = this.state.scope;
      this.state.scope = String(value);
    } else if (key === 'private_lock') {
      previous = this.state.privateLock;
      this.state.privateLock = value === true;
    } else {
      previous = 'cost_saver';
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(
      JSON.stringify({
        ok: true,
        key,
        value: key === 'private_lock' ? value === true : String(value),
        previous,
        default_scope: this.state.scope,
        private_lock: this.state.privateLock,
        airplane: this.state.airplane,
        applied: true,
      }),
    );
  }

  /**
   * The MCP streamable-HTTP endpoint: initialize, notifications/initialized, tools/call — including
   * the transport's own failures (406 without `text/event-stream`, 400 without a live session id).
   */
  private mcp(response: http.ServerResponse, request: http.IncomingMessage, body: unknown): void {
    const accept = request.headers.accept ?? '';
    if (!accept.includes('text/event-stream')) {
      response.writeHead(406, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Not Acceptable: client must accept both' } }));
      return;
    }
    const message = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {};
    const method = message['method'];
    const id = message['id'];

    if (method === 'initialize') {
      this.sessionCounter += 1;
      const session = this.sessionCounter.toString(16).padStart(32, '0');
      if (this.sessionMode !== 'always-reject') {
        this.sessions.add(session);
      }
      response.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'mcp-session-id': session,
      });
      frame(response, {
        jsonrpc: '2.0',
        id,
        result: {
          protocolVersion: '2025-11-25',
          capabilities: { tools: {} },
          serverInfo: { name: 'headwaters', version: '9.9.9-mock' },
        },
      });
      response.end();
      return;
    }

    if (method === 'notifications/initialized') {
      response.writeHead(202);
      response.end();
      return;
    }

    const session = request.headers['mcp-session-id'];
    const valid = typeof session === 'string' && this.sessions.has(session);
    if (!valid) {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Missing session ID' } }));
      return;
    }

    if (method !== 'tools/call') {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: `unknown method ${String(method)}` } }));
      return;
    }

    const params = typeof message['params'] === 'object' && message['params'] !== null
      ? (message['params'] as Record<string, unknown>)
      : {};
    const name = params['name'];
    const args = typeof params['arguments'] === 'object' && params['arguments'] !== null
      ? (params['arguments'] as Record<string, unknown>)
      : {};

    if (this.sessionMode === 'expire-once') {
      this.sessionMode = 'ok';
      this.sessions.clear();
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Missing session ID' } }));
      return;
    }
    if (this.sessionMode === 'always-reject') {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'Missing session ID' } }));
      return;
    }

    const result = this.toolResult(name, args);
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    frame(response, { jsonrpc: '2.0', id, result });
    response.end();
  }

  /** One tool's `structuredContent` plus the text copy the real server also sends. */
  private toolResult(name: unknown, args: Record<string, unknown>): Record<string, unknown> {
    const s = this.state;
    if (name === 'headwaters_status') {
      const value = {
        ok: true,
        scope: { default_scope: s.scope, private_lock: s.privateLock, airplane: s.airplane },
        walls: s.walls.map((kind) => ({ kind, reset_at: null, detail: null })),
        gpu: null,
        warm_models: s.warmModel === null ? [] : [{ model: s.warmModel, vram_gb: 4 }],
        pinned_model: s.pinnedModel,
        savings: s.savings,
        catalog: 'mock',
        notes: [],
        error: null,
      };
      return {
        content: [{ type: 'text', text: JSON.stringify(value) }],
        structuredContent: value,
      };
    }

    if (name === 'headwaters_pin_model') {
      const model = args['model'];
      if (model !== null && model !== undefined && model !== '') {
        if (!MOCK_MODELS.includes(String(model))) {
          const value = {
            ok: false,
            pinned_model: s.pinnedModel,
            error: { code: 'unknown_model', message: `${String(model)} is not a model id here.` },
            notes: [],
          };
          return {
            content: [{ type: 'text', text: JSON.stringify(value) }],
            structuredContent: value,
          };
        }
      }
      s.pinnedModel = model === null || model === undefined || model === '' ? null : String(model);
      const value = {
        ok: true,
        pinned_model: s.pinnedModel,
        error: null,
        notes: ['Front-door MCP tool calls only.'],
      };
      return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
    }

    if (name === 'headwaters_handoff') {
      const goal = typeof args['goal'] === 'string' ? args['goal'] : '';
      s.lastHandoffGoal = goal;
      const value = {
        ok: true,
        packet_id: 'pkt_mock_1',
        packet: { goal, files: [], last_error: null, recent_turns: [], scope: s.scope, model: s.tier },
        files: [],
        redacted: [],
        notes: [],
        error: null,
      };
      return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
    }

    return {
      content: [{ type: 'text', text: `unknown tool ${String(name)}` }],
      isError: true,
    };
  }

  private stream(response: http.ServerResponse, body: unknown): void {
    const wantsTools = hasTools(body);
    const isSecondTurn = mentionsToolResult(body);
    response.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    for (const text of MOCK_TEXT_CHUNKS) {
      frame(response, { choices: [{ index: 0, delta: { content: text } }] });
    }
    if (wantsTools && !isSecondTurn) {
      frame(response, {
        choices: [
          {
            index: 0,
            delta: {
              tool_calls: [
                {
                  index: 0,
                  id: MOCK_TOOL_CALL.id,
                  type: 'function',
                  function: { name: MOCK_TOOL_CALL.name, arguments: '' },
                },
              ],
            },
          },
        ],
      });
      // Arguments arrive in two deltas, as the gateway can split them.
      const split = 8;
      for (const fragment of [
        MOCK_TOOL_CALL.arguments.slice(0, split),
        MOCK_TOOL_CALL.arguments.slice(split),
      ]) {
        frame(response, {
          choices: [
            { index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: fragment } }] } },
          ],
        });
      }
      frame(response, { choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] });
    } else {
      frame(response, { choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] });
    }
    response.write('data: [DONE]\n\n');
    response.end();
  }
}

function frame(response: http.ServerResponse, payload: unknown): void {
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function readBody(request: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => chunks.push(chunk));
    request.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      if (text === '') {
        resolve(undefined);
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch {
        resolve(text);
      }
    });
  });
}

function messagesOf(body: unknown): Array<Record<string, unknown>> {
  if (typeof body !== 'object' || body === null) {
    return [];
  }
  const messages = (body as Record<string, unknown>)['messages'];
  return Array.isArray(messages)
    ? messages.filter(
        (m): m is Record<string, unknown> => typeof m === 'object' && m !== null && !Array.isArray(m),
      )
    : [];
}

function hasTools(body: unknown): boolean {
  if (typeof body !== 'object' || body === null) {
    return false;
  }
  const tools = (body as Record<string, unknown>)['tools'];
  return Array.isArray(tools) && tools.length > 0;
}

function mentionsToolResult(body: unknown): boolean {
  return messagesOf(body).some((message) => message['role'] === 'tool');
}

function countedText(body: unknown): string {
  for (const message of messagesOf(body)) {
    const content = message['content'];
    if (!Array.isArray(content)) {
      continue;
    }
    for (const part of content) {
      if (typeof part === 'object' && part !== null) {
        const text = (part as Record<string, unknown>)['text'];
        if (typeof text === 'string') {
          return text;
        }
      }
    }
  }
  return '';
}
