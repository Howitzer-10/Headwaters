/**
 * SSE parser tests (HDW-506). The parser sees whatever the socket hands it, so frames split across
 * reads and multi-byte characters split across reads are the cases that matter.
 */

import * as assert from 'node:assert/strict';

import { ByteStream, readSseData, SseBuffer, SSE_DONE } from '../src/sse';

suite('sse', () => {
  test('splits frames that arrive in pieces and keeps partial ones', () => {
    const buffer = new SseBuffer();
    assert.deepEqual(buffer.push('data: {"a":'), []);
    assert.deepEqual(buffer.push('1}\n\ndata: [DONE]\n\n'), ['{"a":1}', SSE_DONE]);
  });

  test('joins multi-line data fields and ignores comments', () => {
    const buffer = new SseBuffer();
    assert.deepEqual(buffer.push(': keepalive\ndata: one\ndata: two\n\n'), ['one\ntwo']);
  });

  test('flushes a last frame the gateway never terminated', () => {
    const buffer = new SseBuffer();
    assert.deepEqual(buffer.push('data: tail'), []);
    assert.equal(buffer.flush(), 'tail');
  });

  test('decodes a character split across two reads', async () => {
    const bytes = Buffer.from('data: {"t":"é"}\n\n', 'utf8');
    // 'é' is two bytes; cutting at 13 puts the split inside it.
    const chunks = [bytes.subarray(0, 13), bytes.subarray(13)];
    const seen: string[] = [];
    await readSseData(streamOf(chunks), (data) => seen.push(data));
    assert.deepEqual(seen, ['{"t":"é"}']);
  });
});

/** The minimum of a byte stream that `readSseData` uses, fed from fixed chunks. */
function streamOf(chunks: readonly Uint8Array[]): ByteStream {
  let index = 0;
  return {
    getReader(): {
      read(): Promise<{ done: boolean; value?: Uint8Array }>;
      releaseLock(): void;
    } {
      return {
        read() {
          if (index >= chunks.length) {
            return Promise.resolve({ done: true });
          }
          const value = chunks[index];
          index += 1;
          return Promise.resolve({ done: false, value });
        },
        releaseLock() {
          // nothing to release in a fixed chunk list
        },
      };
    },
  };
}
