/**
 * The Headwaters language model provider (HDW-506).
 *
 * This is the star feature of the ticket: `vscode.lm.registerLanguageModelChatProvider('headwaters',
 * ...)`, so the gateway's virtual models show up in the VS Code model picker and work in agent mode.
 * It is a thin client. Which real model serves a request, whether to fall back, what to charge: all
 * of that is the gateway's job (`headwaters/gateway/`). Nothing here chooses a model, routes, or
 * retries.
 *
 * The honesty problem this file has to live with: a Headwaters virtual model is a routing alias, so
 * its true context window, tool support and vision support are not properties of the alias.
 * `LanguageModelChatInformation` still demands numbers and booleans. They come from the gateway's
 * declared floor (`capabilities` on each `/v1/models` card, computed there from catalog data by
 * `headwaters/gateway/models.py:declared`) and are never invented here. When the gateway has no
 * catalog entry to stand on it says `basis: "unknown_defaults"`, and this provider shows that in the
 * model's tooltip instead of pretending the floor is a measurement.
 *
 * Images: the gateway reports `image_input` per model, but `@types/vscode` 1.104 has no content part
 * that can carry an image into `provideLanguageModelChatResponse`, so this provider advertises
 * `imageInput: false` regardless. See docs/reports/HDW-506.md.
 */

import * as vscode from 'vscode';

import { GatewayUnavailable, readGateway } from './daemon';
import { convertRequest } from './convert';
import { GatewayClient, GatewayError, ModelCard } from './gatewayClient';

/** The one vendor id this extension answers to; it matches `contributes.languageModelChatProviders`. */
export const VENDOR = 'headwaters';

/**
 * VS Code 1.104's provider API carries text, tool calls and tool results only: there is no
 * `LanguageModelDataPart`, so an image from the user cannot reach us even if the gateway's model
 * behind the alias can see one. Flipping this to true is the whole extension-side change needed once
 * a VS Code API version can carry images.
 */
const CAN_CARRY_IMAGES = false;

/** Used when the gateway's token count cannot be had; declared here so it is never a secret. */
const CHARS_PER_TOKEN_ESTIMATE = 4;

export class HeadwatersProvider implements vscode.LanguageModelChatProvider {
  private readonly changeEmitter = new vscode.EventEmitter<void>();
  private cards: ModelCard[] | undefined;
  private gatewayVersion = '';
  private lastUnavailable: string | undefined;

  readonly onDidChangeLanguageModelChatInformation = this.changeEmitter.event;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly log?: vscode.OutputChannel,
  ) {}

  /** Forget the cached model list and tell VS Code to ask again. */
  refresh(): void {
    this.cards = undefined;
    this.changeEmitter.fire();
  }

  dispose(): void {
    this.changeEmitter.dispose();
  }

  async provideLanguageModelChatInformation(
    options: vscode.PrepareLanguageModelChatModelOptions,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelChatInformation[]> {
    // Silent means "do not prompt anybody". A missing daemon is the normal state on a machine that
    // has not started Headwaters, so it is never a modal, never a toast, and never a secret request.
    // Both modes return an empty list; `silent` only changes how much is written to the log.
    if (options.silent) {
      this.note('looking for models silently');
    }
    const cards = await this.loadCards(token);
    if (cards === undefined) {
      return [];
    }
    return cards.map((card) => this.describe(card));
  }

  async provideLanguageModelChatResponse(
    model: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const gateway = await readGateway(this.env);
    const client = new GatewayClient(gateway);
    const converted = convertRequest(
      messages,
      model.capabilities.toolCalling === true ? options.tools : undefined,
      options.toolMode,
    );

    const controller = new AbortController();
    const subscription = token.onCancellationRequested(() => controller.abort());
    try {
      await client.chat(
        {
          model: model.id,
          messages: converted.messages,
          tools: converted.tools,
          toolChoice: converted.toolChoice,
        },
        {
          onText: (text) => progress.report(new vscode.LanguageModelTextPart(text)),
          onToolCall: (call) =>
            progress.report(new vscode.LanguageModelToolCallPart(call.id, call.name, call.input)),
        },
        controller.signal,
      );
    } catch (error) {
      throw describeFailure(error);
    } finally {
      subscription.dispose();
    }
  }

  async provideTokenCount(
    model: vscode.LanguageModelChatInformation,
    text: string | vscode.LanguageModelChatRequestMessage,
    token: vscode.CancellationToken,
  ): Promise<number> {
    const flat = typeof text === 'string' ? text : textOfMessage(text);
    if (flat === '' || token.isCancellationRequested) {
      return 0;
    }
    try {
      const gateway = await readGateway(this.env);
      return await new GatewayClient(gateway).countTokens(flat);
    } catch {
      // Counting is advisory: VS Code uses it for trimming and for a progress bar. A gateway that is
      // down must not break the picker or the chat, so fall back to an admitted character estimate.
      this.note(`token count estimated for ${model.id}`);
      return Math.max(1, Math.ceil(flat.length / CHARS_PER_TOKEN_ESTIMATE));
    }
  }

  /** The cached card list, or undefined when the gateway is not reachable right now. */
  private async loadCards(token: vscode.CancellationToken): Promise<ModelCard[] | undefined> {
    if (this.cards !== undefined) {
      return this.cards;
    }
    if (token.isCancellationRequested) {
      return undefined;
    }
    try {
      const gateway = await readGateway(this.env);
      this.gatewayVersion = gateway.version;
      this.cards = await new GatewayClient(gateway).listModels();
      this.lastUnavailable = undefined;
      this.note(`gateway listed ${this.cards.length} models`);
      return this.cards;
    } catch (error) {
      const reason = error instanceof GatewayUnavailable ? error.reason : 'gateway-error';
      if (this.lastUnavailable !== reason) {
        this.lastUnavailable = reason;
        this.note(`no models: ${reason}`);
      }
      return undefined;
    }
  }

  private describe(card: ModelCard): vscode.LanguageModelChatInformation {
    const basis =
      card.capabilities.basis === 'catalog_floor'
        ? `declared floor over ${String(card.capabilities.entries)} catalog model(s)`
        : 'conservative unknown-model default: the catalog gave no eligible entry';
    return {
      id: card.id,
      name: card.name,
      family: 'headwaters',
      version: this.gatewayVersion === '' ? '1' : this.gatewayVersion,
      detail: 'Headwaters gateway',
      tooltip: [
        card.description,
        '',
        'Routing alias: the gateway picks the real model per request.',
        `Limits: ${String(card.capabilities.maxInputTokens)} in / ${String(card.capabilities.maxOutputTokens)} out (${basis}).`,
        `Tools: ${card.capabilities.toolCalling ? 'yes' : 'not declared'} · Vision: ${
          CAN_CARRY_IMAGES && card.capabilities.imageInput ? 'yes' : 'not available in this VS Code'
        }`,
      ].join('\n'),
      maxInputTokens: card.capabilities.maxInputTokens,
      maxOutputTokens: card.capabilities.maxOutputTokens,
      capabilities: {
        imageInput: CAN_CARRY_IMAGES && card.capabilities.imageInput,
        toolCalling: card.capabilities.toolCalling,
      },
    };
  }

  private note(message: string): void {
    // Only state transitions describe what happened; prompts, responses, tokens and keys never do.
    this.log?.appendLine(`[hdw-506] ${message}`);
  }
}

/** Turn a gateway-side failure into something a user can read, without leaking the token. */
function describeFailure(error: unknown): Error {
  if (error instanceof GatewayUnavailable) {
    return new Error(`Headwaters is not available: ${error.message}`);
  }
  if (error instanceof GatewayError) {
    return new Error(`Headwaters gateway error: ${error.message}`);
  }
  if (error instanceof Error && error.name === 'AbortError') {
    return new Error('Headwaters request cancelled.');
  }
  return new Error('Headwaters request failed.');
}

/** Flatten a request message to its text, for token counting. */
function textOfMessage(message: vscode.LanguageModelChatRequestMessage): string {
  const pieces: string[] = [];
  for (const part of message.content) {
    if (part instanceof vscode.LanguageModelTextPart) {
      pieces.push(part.value);
    }
  }
  return pieces.join('');
}
