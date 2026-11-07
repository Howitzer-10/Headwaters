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

  get url(): string {
    return `http://127.0.0.1:${String(this.port)}`;
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

    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: { message: 'not found' } }));
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
