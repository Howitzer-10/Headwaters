/**
 * The accept-when evidence for HDW-506.
 *
 * The models must appear in VS Code's own picker and must work in agent mode against a mocked
 * gateway. The gateway here is a real loopback HTTP server (`test/mockGateway.ts`) reached through
 * the extension's normal client code, and the daemon is a temp `HEADWATERS_HOME` holding a
 * `daemon.json` and a `daemon.token`. Nothing in this file stubs a provider method.
 *
 * What is and is not covered:
 *  - the picker is VS Code's (`vscode.lm.selectChatModels`), and token counting goes out through
 *    `LanguageModelChat.countTokens`, i.e. VS Code calls the registered provider;
 *  - streaming and tool calls call the provider's documented entry points
 *    (`provideLanguageModelChatResponse` / `provideLanguageModelChatInformation`) with the same
 *    message shapes VS Code hands it. The `LanguageModelChat.sendRequest` path additionally requires
 *    the user consent dialog, which cannot be answered in an unattended test run, so it is not
 *    exercised here; see docs/reports/HDW-506.md.
 */

import * as assert from 'node:assert/strict';

import * as vscode from 'vscode';

import { HeadwatersProvider } from '../src/provider';
import { delay, TempHome } from './fixture';
import {
  MOCK_CARDS,
  MOCK_TEXT_CHUNKS,
  MOCK_TOKEN,
  MOCK_TOOL_CALL,
  MockGateway,
  RecordedRequest,
} from './mockGateway';

const EXTENSION_ID = 'headwaters.headwaters-vscode';
const VENDOR = 'headwaters';

function requestMessage(
  role: vscode.LanguageModelChatMessageRole,
  content: readonly unknown[],
): vscode.LanguageModelChatRequestMessage {
  return { role, content, name: undefined };
}

function user(text: string): vscode.LanguageModelChatRequestMessage {
  return requestMessage(vscode.LanguageModelChatMessageRole.User, [
    new vscode.LanguageModelTextPart(text),
  ]);
}

/** Collect everything the provider reports, the way VS Code's chat view does. */
function collector(): {
  progress: vscode.Progress<vscode.LanguageModelResponsePart>;
  parts: vscode.LanguageModelResponsePart[];
  text(): string;
} {
  const parts: vscode.LanguageModelResponsePart[] = [];
  return {
    progress: { report: (part) => parts.push(part) },
    parts,
    text: () => parts.map((part) => (part instanceof vscode.LanguageModelTextPart ? part.value : '')).join(''),
  };
}

function chatBodies(gateway: MockGateway): Array<Record<string, unknown>> {
  return gateway.requests
    .filter((request) => request.path === '/v1/chat/completions')
    .map((request: RecordedRequest) => (request.body ?? {}) as Record<string, unknown>);
}

suite('Headwaters language model provider', () => {
  let gateway: MockGateway;
  let home: TempHome;
  let previousHome: string | undefined;

  suiteSetup(async function () {
    this.timeout(60000);
    gateway = new MockGateway();
    await gateway.start();
    home = new TempHome();
    home.writeDaemon(Number(new URL(gateway.url).port), MOCK_TOKEN);

    previousHome = process.env.HEADWATERS_HOME;
    process.env.HEADWATERS_HOME = home.dir;

    const extension = vscode.extensions.getExtension(EXTENSION_ID);
    assert.ok(extension, `${EXTENSION_ID} is loaded in this test host`);
    await extension.activate();
    // The provider may have activated before this suite set HEADWATERS_HOME; refresh makes VS Code
    // ask it again, which is exactly what the contributed command is for.
    await vscode.commands.executeCommand('headwaters.refreshModels');
  });

  suiteTeardown(async () => {
    if (previousHome === undefined) {
      delete process.env.HEADWATERS_HOME;
    } else {
      process.env.HEADWATERS_HOME = previousHome;
    }
    await gateway.stop();
    home.cleanup();
  });

  test('the models appear in the picker', async function () {
    this.timeout(30000);
    let models: vscode.LanguageModelChat[] = [];
    for (let attempt = 0; attempt < 25; attempt += 1) {
      models = await vscode.lm.selectChatModels({ vendor: VENDOR });
      if (models.length > 0) {
        break;
      }
      await delay(200);
    }

    assert.deepEqual(
      models.map((model) => model.id).sort(),
      MOCK_CARDS.map((card) => card.id).sort(),
      'ids come from the gateway, not from the extension',
    );
    for (const model of models) {
      assert.equal(model.vendor, VENDOR);
      assert.equal(model.family, VENDOR);
      assert.equal(model.version, '9.9.9-mock', 'the daemon version is the opaque model version');
      assert.equal(model.maxInputTokens, 6554, 'limits come from the gateway capability floor');
      assert.ok(
        MOCK_CARDS.some((card) => card.name === model.name),
        'names come from the gateway',
      );
    }

    const listed = gateway.requests.filter((request) => request.path === '/v1/models');
    assert.ok(listed.length > 0, 'the extension asked the gateway for its models');
    assert.ok(
      listed.every((request) => request.authorized),
      'every model list request carried the bearer token from daemon.token',
    );
  });

  test('token counting goes from VS Code through the provider to the gateway', async function () {
    this.timeout(30000);
    const [model] = await pickerModel();
    const count = await model.countTokens('hello world');
    assert.equal(count, 3, 'the mock gateway counts ceil(11/4)');
    assert.ok(
      gateway.requests.some((request) => request.path === '/v1/messages/count_tokens' && request.authorized),
      'the count request reached the gateway with the token',
    );
  });

  test('capabilities and limits come from the gateway floor', async function () {
    this.timeout(30000);
    const provider = new HeadwatersProvider({ HEADWATERS_HOME: home.dir });
    const token = new vscode.CancellationTokenSource().token;
    const models = await provider.provideLanguageModelChatInformation({ silent: false }, token);

    assert.equal(models.length, MOCK_CARDS.length);
    const auto = models.find((model) => model.id === 'headwaters/auto');
    assert.ok(auto, 'headwaters/auto is listed');
    assert.equal(auto.maxInputTokens, 6554);
    assert.equal(auto.maxOutputTokens, 984);
    assert.equal(auto.capabilities.toolCalling, true);
    assert.equal(
      auto.capabilities.imageInput,
      false,
      'vision is not advertised even when the gateway reports it: this VS Code has no image part',
    );
    assert.match(auto.tooltip ?? '', /declared floor over 3 catalog model\(s\)/);
    const local = models.find((model) => model.id === 'headwaters/local');
    assert.equal(local?.capabilities.toolCalling, false, 'the gateway decides per model');
  });

  test('a response streams text and a tool call', async function () {
    this.timeout(30000);
    const provider = new HeadwatersProvider({ HEADWATERS_HOME: home.dir });
    const token = new vscode.CancellationTokenSource().token;
    const [model] = await provider.provideLanguageModelChatInformation({ silent: false }, token);
    const sink = collector();

    await provider.provideLanguageModelChatResponse(
      model,
      [user('read a.txt')],
      {
        toolMode: vscode.LanguageModelChatToolMode.Auto,
        tools: [
          {
            name: 'read_file',
            description: 'Read a file from the workspace.',
            inputSchema: { type: 'object', properties: { path: { type: 'string' } } },
          },
        ],
      },
      sink.progress,
      token,
    );

    assert.equal(sink.text(), MOCK_TEXT_CHUNKS.join(''), 'text arrives in order, chunk by chunk');
    const calls = sink.parts.filter(
      (part): part is vscode.LanguageModelToolCallPart =>
        part instanceof vscode.LanguageModelToolCallPart,
    );
    assert.equal(calls.length, 1);
    assert.equal(calls[0].callId, MOCK_TOOL_CALL.id);
    assert.equal(calls[0].name, MOCK_TOOL_CALL.name);
    assert.deepEqual(calls[0].input, { path: 'a.txt' }, 'the split argument deltas are reassembled');

    const body = chatBodies(gateway).at(-1);
    assert.ok(body, 'the gateway received the chat request');
    assert.equal(body['model'], 'headwaters/auto', 'the model id is the gateway alias');
    assert.equal(body['stream'], true);
    assert.equal(body['tool_choice'], 'auto');
    assert.deepEqual(body['messages'], [{ role: 'user', content: 'read a.txt' }]);
    assert.equal((body['tools'] as unknown[]).length, 1, 'tools are forwarded as function schemas');
  });

  test('a tool result goes back as a tool message', async function () {
    this.timeout(30000);
    const provider = new HeadwatersProvider({ HEADWATERS_HOME: home.dir });
    const token = new vscode.CancellationTokenSource().token;
    const [model] = await provider.provideLanguageModelChatInformation({ silent: false }, token);
    const sink = collector();

    await provider.provideLanguageModelChatResponse(
      model,
      [
        user('read a.txt'),
        requestMessage(vscode.LanguageModelChatMessageRole.Assistant, [
          new vscode.LanguageModelToolCallPart(MOCK_TOOL_CALL.id, MOCK_TOOL_CALL.name, {
            path: 'a.txt',
          }),
        ]),
        requestMessage(vscode.LanguageModelChatMessageRole.User, [
          new vscode.LanguageModelToolResultPart(MOCK_TOOL_CALL.id, [
            new vscode.LanguageModelTextPart('contents of a.txt'),
          ]),
        ]),
      ],
      { toolMode: vscode.LanguageModelChatToolMode.Auto },
      sink.progress,
      token,
    );

    const messages = (chatBodies(gateway).at(-1)?.['messages'] ?? []) as Array<
      Record<string, unknown>
    >;
    assert.deepEqual(
      messages.map((message) => message['role']),
      ['user', 'assistant', 'tool'],
    );
    assert.equal(messages[1]['content'], null);
    assert.deepEqual(messages[1]['tool_calls'], [
      {
        id: MOCK_TOOL_CALL.id,
        type: 'function',
        function: { name: MOCK_TOOL_CALL.name, arguments: '{"path":"a.txt"}' },
      },
    ]);
    assert.equal(messages[2]['tool_call_id'], MOCK_TOOL_CALL.id);
    assert.equal(messages[2]['content'], 'contents of a.txt');
    assert.equal(sink.text(), MOCK_TEXT_CHUNKS.join(''), 'the follow-up turn streams text');
  });

  test('silent mode returns an empty list when the daemon is not running', async function () {
    this.timeout(30000);
    const empty = new TempHome();
    try {
      const provider = new HeadwatersProvider({ HEADWATERS_HOME: empty.dir });
      const token = new vscode.CancellationTokenSource().token;
      assert.deepEqual(await provider.provideLanguageModelChatInformation({ silent: true }, token), []);
      assert.deepEqual(await provider.provideLanguageModelChatInformation({ silent: false }, token), []);
    } finally {
      empty.cleanup();
    }
  });

  test('token counting falls back to an admitted estimate when the gateway is gone', async function () {
    this.timeout(30000);
    const empty = new TempHome();
    try {
      const provider = new HeadwatersProvider({ HEADWATERS_HOME: empty.dir });
      const token = new vscode.CancellationTokenSource().token;
      const before = gateway.requests.length;
      const count = await provider.provideTokenCount(
        {
          id: 'headwaters/auto',
          name: 'Headwaters Auto',
          family: VENDOR,
          version: '9.9.9-mock',
          maxInputTokens: 6554,
          maxOutputTokens: 984,
          capabilities: { imageInput: false, toolCalling: true },
        },
        'a short string',
        token,
      );
      assert.equal(count, 4, 'ceil(14/4): the documented character estimate');
      assert.equal(gateway.requests.length, before, 'nothing was sent to the gateway');
    } finally {
      empty.cleanup();
    }
  });
});

async function pickerModel(): Promise<vscode.LanguageModelChat[]> {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    const models = await vscode.lm.selectChatModels({ vendor: VENDOR });
    if (models.length > 0) {
      return models;
    }
    await delay(200);
  }
  return vscode.lm.selectChatModels({ vendor: VENDOR });
}
