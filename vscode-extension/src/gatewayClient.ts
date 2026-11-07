/**
 * A thin HTTP client for the Headwaters gateway (HDW-506).
 *
 * It knows the wire shapes the gateway serves and nothing else: no routing, no model choice, no
 * fallback. Every decision about which real model answers a request belongs to the gateway
 * (`headwaters/gateway/`), so this file only moves bytes to and from loopback.
 *
 * Rules this file keeps:
 *  - the daemon token is only ever placed in a request header to 127.0.0.1; it is never logged or
 *    put in an error message;
 *  - prompts, responses and tool arguments are never logged either: errors carry status codes and
 *    the gateway's own message text;
 *  - nothing is retried behind the caller's back.
 */

import { Gateway } from './daemon';
import { readSseData, SSE_DONE } from './sse';

/** One virtual model card, as `/v1/models` describes it. */
export interface ModelCard {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly capabilities: ModelCapabilities;
}

/**
 * The declared floor the gateway computes for a routing alias.
 *
 * A Headwaters virtual model is an alias: the real model behind `headwaters/auto` can differ per
 * request, so its true context window, tool support and vision support are not properties of the
 * alias. The gateway therefore publishes a floor it can defend from catalog data (see
 * `headwaters/gateway/models.py:declared`) rather than a per-model guess.
 */
export interface ModelCapabilities {
  readonly maxInputTokens: number;
  readonly maxOutputTokens: number;
  readonly imageInput: boolean;
  readonly toolCalling: boolean;
  /** `catalog_floor` when real catalog entries backed the floor, else `unknown_defaults`. */
  readonly basis: string;
  /** How many catalog entries the floor was computed over. */
  readonly entries: number;
}

/** What the gateway refused to do, or could not be reached to do. */
export class GatewayError extends Error {
  constructor(
    readonly status: number | undefined,
    message: string,
  ) {
    super(message);
    this.name = 'GatewayError';
  }
}

/** An OpenAI-shaped chat message, as the gateway's `/v1/chat/completions` takes them. */
export interface ChatMessage {
  readonly role: 'system' | 'user' | 'assistant' | 'tool';
  readonly content: string | null;
  readonly tool_calls?: readonly ChatToolCall[];
  readonly tool_call_id?: string;
}

export interface ChatToolCall {
  readonly id: string;
  readonly type: 'function';
  readonly function: { readonly name: string; readonly arguments: string };
}

export interface ChatTool {
  readonly type: 'function';
  readonly function: {
    readonly name: string;
    readonly description: string;
    readonly parameters: unknown;
  };
}

export interface ChatRequest {
  readonly model: string;
  readonly messages: readonly ChatMessage[];
  readonly tools?: readonly ChatTool[];
  readonly toolChoice?: 'auto' | 'required';
}

/** Callbacks for one streamed response. Text arrives already in order. */
export interface StreamHandlers {
  onText(text: string): void;
  onToolCall(call: { id: string; name: string; input: object }): void;
}

/** Short timeouts for the small, non-streaming calls; the chat stream has no timeout. */
const SMALL_CALL_TIMEOUT_MS = 10_000;

export class GatewayClient {
  constructor(private readonly gateway: Gateway) {}

  get baseUrl(): string {
    return this.gateway.url;
  }

  /** `GET /v1/models`. Ids, names and capability floors all come from the gateway. */
  async listModels(signal?: AbortSignal): Promise<ModelCard[]> {
    const response = await fetch(`${this.gateway.url}/v1/models`, {
      headers: this.headers(),
      signal: bounded(signal),
    });
    if (!response.ok) {
      throw await failure(response);
    }
    return cardsOf(await response.json());
  }

  /** `POST /v1/chat/completions` with `stream: true`, delivered to `handlers` as it arrives. */
  async chat(request: ChatRequest, handlers: StreamHandlers, signal: AbortSignal): Promise<void> {
    const body: Record<string, unknown> = {
      model: request.model,
      messages: request.messages,
      stream: true,
    };
    if (request.tools !== undefined && request.tools.length > 0) {
      body['tools'] = request.tools;
      body['tool_choice'] = request.toolChoice ?? 'auto';
    }

    const response = await fetch(`${this.gateway.url}/v1/chat/completions`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      throw await failure(response);
    }
    if (response.body === null) {
      throw new GatewayError(response.status, 'The gateway sent no response body.');
    }

    const calls = new ToolCalls((call) => handlers.onToolCall(call));
    await readSseData(response.body, (data) => {
      if (data === SSE_DONE) {
        return;
      }
      const chunk = parseJson(data, 'a stream chunk');
      if (!isRecord(chunk)) {
        return;
      }
      const error = chunk['error'];
      if (error !== undefined) {
        throw new GatewayError(undefined, gatewayMessage(error));
      }
      const choice = firstChoice(chunk);
      if (choice === undefined) {
        return;
      }
      const delta = choice['delta'];
      if (isRecord(delta)) {
        const text = delta['content'];
        if (typeof text === 'string' && text !== '') {
          handlers.onText(text);
        }
        calls.push(delta['tool_calls']);
      }
      const finish = choice['finish_reason'];
      if (typeof finish === 'string' && finish !== '') {
        calls.flush();
      }
    });
    calls.flush();
  }

  /**
   * `POST /v1/messages/count_tokens`. The gateway answers with an estimate from the model it
   * routes to; this extension does not tokenise text itself.
   */
  async countTokens(text: string, signal?: AbortSignal): Promise<number> {
    const response = await fetch(`${this.gateway.url}/v1/messages/count_tokens`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify({ messages: [{ role: 'user', content: [{ type: 'text', text }] }] }),
      signal: bounded(signal),
    });
    if (!response.ok) {
      throw await failure(response);
    }
    const body: unknown = await response.json();
    const count = isRecord(body) ? body['input_tokens'] : undefined;
    if (typeof count !== 'number' || !Number.isFinite(count) || count < 0) {
      throw new GatewayError(response.status, 'The gateway sent no usable input_tokens.');
    }
    return Math.trunc(count);
  }

  private headers(json = false): Record<string, string> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.gateway.token}` };
    if (json) {
      headers['Content-Type'] = 'application/json';
    }
    return headers;
  }
}

/** Accumulates `tool_calls` deltas and emits each call once its arguments are complete. */
class ToolCalls {
  private readonly pending = new Map<
    number,
    { id: string; name: string; arguments: string; emitted: boolean }
  >();

  constructor(private readonly emit: (call: { id: string; name: string; input: object }) => void) {}

  push(deltas: unknown): void {
    if (!Array.isArray(deltas)) {
      return;
    }
    for (const delta of deltas) {
      if (!isRecord(delta)) {
        continue;
      }
      const index = delta['index'];
      const key = typeof index === 'number' && Number.isInteger(index) ? index : 0;
      const entry = this.pending.get(key) ?? { id: '', name: '', arguments: '', emitted: false };
      const id = delta['id'];
      if (typeof id === 'string' && id !== '') {
        entry.id = id;
      }
      const fn = delta['function'];
      if (isRecord(fn)) {
        const name = fn['name'];
        if (typeof name === 'string' && name !== '') {
          entry.name = name;
        }
        const args = fn['arguments'];
        if (typeof args === 'string') {
          entry.arguments += args;
        }
      }
      this.pending.set(key, entry);
    }
  }

  flush(): void {
    for (const entry of this.pending.values()) {
      if (entry.emitted) {
        continue;
      }
      entry.emitted = true;
      if (entry.name === '') {
        throw new GatewayError(undefined, 'The gateway sent a tool call with no function name.');
      }
      this.emit({
        id: entry.id === '' ? fallbackCallId() : entry.id,
        name: entry.name,
        input: parseArguments(entry.arguments),
      });
    }
  }
}

/** Tool arguments are JSON by contract; an empty string means "no arguments". */
function parseArguments(text: string): object {
  if (text.trim() === '') {
    return {};
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new GatewayError(undefined, 'The gateway sent tool arguments that are not JSON.');
  }
  if (!isRecord(parsed)) {
    throw new GatewayError(undefined, 'The gateway sent tool arguments that are not an object.');
  }
  return parsed;
}

let callCounter = 0;

function fallbackCallId(): string {
  callCounter += 1;
  return `headwaters-call-${callCounter}`;
}

function firstChoice(chunk: Record<string, unknown>): Record<string, unknown> | undefined {
  const choices = chunk['choices'];
  if (!Array.isArray(choices) || choices.length === 0) {
    return undefined;
  }
  const choice = choices[0];
  return isRecord(choice) ? choice : undefined;
}

/** Read an OpenAI-shaped `{data: [...]}` list into cards, rejecting anything malformed. */
function cardsOf(body: unknown): ModelCard[] {
  const data = isRecord(body) ? body['data'] : undefined;
  if (!Array.isArray(data)) {
    throw new GatewayError(undefined, 'The gateway sent no model list.');
  }
  return data.map((item) => {
    const id = isRecord(item) ? item['id'] : undefined;
    if (typeof id !== 'string' || id === '') {
      throw new GatewayError(undefined, 'The gateway sent a model with no id.');
    }
    const capabilities = isRecord(item) ? item['capabilities'] : undefined;
    const name = isRecord(item) ? item['name'] : undefined;
    const description = isRecord(item) ? item['description'] : undefined;
    return {
      id,
      name: typeof name === 'string' && name !== '' ? name : id,
      description: typeof description === 'string' ? description : '',
      capabilities: capabilitiesOf(capabilities, id),
    };
  });
}

function capabilitiesOf(raw: unknown, id: string): ModelCapabilities {
  if (!isRecord(raw)) {
    throw new GatewayError(undefined, `The gateway sent no capabilities for ${id}.`);
  }
  const maxInputTokens = raw['max_input_tokens'];
  const maxOutputTokens = raw['max_output_tokens'];
  if (!isPositiveInteger(maxInputTokens) || !isPositiveInteger(maxOutputTokens)) {
    throw new GatewayError(undefined, `The gateway sent unusable capability limits for ${id}.`);
  }
  return {
    maxInputTokens,
    maxOutputTokens,
    imageInput: raw['image_input'] === true,
    toolCalling: raw['tool_calling'] === true,
    basis: typeof raw['basis'] === 'string' ? raw['basis'] : 'unknown',
    entries: typeof raw['entries'] === 'number' ? raw['entries'] : 0,
  };
}

async function failure(response: Response): Promise<GatewayError> {
  let message = `The gateway answered ${response.status}.`;
  try {
    const body: unknown = await response.json();
    const error = isRecord(body) ? body['error'] : undefined;
    if (error !== undefined) {
      message = gatewayMessage(error);
    }
  } catch {
    // Not JSON, or already consumed: the status alone is the honest message.
  }
  return new GatewayError(response.status, message);
}

function gatewayMessage(error: unknown): string {
  if (isRecord(error)) {
    const message = error['message'];
    if (typeof message === 'string' && message !== '') {
      return message;
    }
  }
  return typeof error === 'string' && error !== '' ? error : 'The gateway reported an error.';
}

function parseJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    throw new GatewayError(undefined, `The gateway sent ${what} that is not JSON.`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

/** Small calls get a deadline; a caller cancellation wins over it. */
function bounded(signal: AbortSignal | undefined): AbortSignal {
  const timeout = AbortSignal.timeout(SMALL_CALL_TIMEOUT_MS);
  return signal === undefined ? timeout : AbortSignal.any([signal, timeout]);
}
