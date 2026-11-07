/**
 * Server-sent events, as the Headwaters gateway writes them (HDW-506).
 *
 * `headwaters/gateway/openai_routes.py` sends `data: <json>\n\n` frames and a final
 * `data: [DONE]`. This parser is deliberately dumb: it splits frames and hands back the `data:`
 * payload, and knows nothing about what the JSON means. It must survive a frame arriving in two
 * reads, which is why the buffer is kept between pushes.
 */

/** Data payloads that are not JSON: the stream's final marker. */
export const SSE_DONE = '[DONE]';

export class SseBuffer {
  private rest = '';

  /** Feed decoded text; returns every complete frame's `data:` payload it can now emit. */
  push(text: string): string[] {
    this.rest += text;
    const payloads: string[] = [];
    let split = this.rest.indexOf('\n\n');
    while (split >= 0) {
      const frame = this.rest.slice(0, split);
      this.rest = this.rest.slice(split + 2);
      const payload = dataOf(frame);
      if (payload !== undefined) {
        payloads.push(payload);
      }
      split = this.rest.indexOf('\n\n');
    }
    return payloads;
  }

  /** Whatever is left when the stream ends: a last frame the gateway did not terminate. */
  flush(): string | undefined {
    const frame = this.rest;
    this.rest = '';
    return frame.trim() === '' ? undefined : dataOf(frame);
  }
}

/** The `data:` fields of one frame, joined; undefined when the frame carries no data. */
function dataOf(frame: string): string | undefined {
  const lines = frame.split('\n').map((line) => (line.endsWith('\r') ? line.slice(0, -1) : line));
  const data = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice('data:'.length).replace(/^ /, ''))
    .join('\n');
  return data === '' ? undefined : data;
}

/** The little bit of a byte stream this parser needs, so `fetch` bodies fit without casts. */
export interface ByteStream {
  getReader(): ByteStreamReader;
}

export interface ByteStreamReader {
  read(): Promise<{ done: boolean; value?: Uint8Array }>;
  releaseLock(): void;
}

/**
 * Read a response body as SSE frames, calling `onData` for each `data:` payload in arrival order.
 * `body` is a web stream in the extension host's Node, so `getReader` is the typed way in.
 */
export async function readSseData(body: ByteStream, onData: (data: string) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const buffer = new SseBuffer();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      for (const data of buffer.push(decoder.decode(value, { stream: true }))) {
        onData(data);
      }
    }
    for (const data of buffer.push(decoder.decode())) {
      onData(data);
    }
  } finally {
    reader.releaseLock();
  }
  const tail = buffer.flush();
  if (tail !== undefined) {
    onData(tail);
  }
}
