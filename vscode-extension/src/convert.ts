/**
 * VS Code chat request -> gateway chat request (HDW-506).
 *
 * `provideLanguageModelChatResponse` hands us `LanguageModelChatRequestMessage`s: a role plus a
 * heterogeneous list of content parts that the provider has to interpret itself. This file is
 * that interpretation, and only that: it builds the OpenAI-shaped messages the gateway's
 * `/v1/chat/completions` already speaks. Anything it does not recognise is dropped rather than
 * guessed at, and images are never expected because this provider declares `imageInput: false`
 * (`@types/vscode` 1.104 has no image content part to carry them in).
 */

import * as vscode from 'vscode';

import type { ChatMessage, ChatTool, ChatToolCall } from './gatewayClient';

export interface ConvertedRequest {
  readonly messages: ChatMessage[];
  readonly tools: ChatTool[];
  readonly toolChoice: 'auto' | 'required';
}

/** Build the gateway request body for one VS Code request. */
export function convertRequest(
  messages: readonly vscode.LanguageModelChatRequestMessage[],
  tools: readonly vscode.LanguageModelChatTool[] | undefined,
  toolMode: vscode.LanguageModelChatToolMode | undefined,
): ConvertedRequest {
  const converted: ChatMessage[] = [];
  for (const message of messages) {
    if (message.role === vscode.LanguageModelChatMessageRole.Assistant) {
      appendAssistant(message, converted);
    } else {
      appendUser(message, converted);
    }
  }
  const convertedTools = (tools ?? []).map(convertTool);
  return {
    messages: converted,
    tools: convertedTools,
    toolChoice: toolMode === vscode.LanguageModelChatToolMode.Required ? 'required' : 'auto',
  };
}

function appendUser(
  message: vscode.LanguageModelChatRequestMessage,
  into: ChatMessage[],
): void {
  const textParts: string[] = [];
  const toolResults: ChatMessage[] = [];
  for (const part of message.content) {
    if (part instanceof vscode.LanguageModelToolResultPart) {
      toolResults.push({
        role: 'tool',
        tool_call_id: part.callId,
        content: textOf(part.content),
      });
    } else if (isTextPart(part)) {
      textParts.push(part.value);
    }
  }
  // Tool results answer an assistant turn, so they go before this user turn's own text.
  into.push(...toolResults);
  const text = textParts.join('');
  if (text !== '' || toolResults.length === 0) {
    into.push({ role: 'user', content: text });
  }
}

function appendAssistant(
  message: vscode.LanguageModelChatRequestMessage,
  into: ChatMessage[],
): void {
  const textParts: string[] = [];
  const calls: ChatToolCall[] = [];
  for (const part of message.content) {
    if (part instanceof vscode.LanguageModelToolCallPart) {
      calls.push({
        id: part.callId,
        type: 'function',
        function: { name: part.name, arguments: JSON.stringify(part.input ?? {}) },
      });
    } else if (isTextPart(part)) {
      textParts.push(part.value);
    }
  }
  const text = textParts.join('');
  into.push({
    role: 'assistant',
    content: text === '' && calls.length > 0 ? null : text,
    ...(calls.length > 0 ? { tool_calls: calls } : {}),
  });
}

function convertTool(tool: vscode.LanguageModelChatTool): ChatTool {
  return {
    type: 'function',
    function: {
      name: tool.name,
      description: tool.description,
      parameters: tool.inputSchema ?? { type: 'object', properties: {} },
    },
  };
}

/** Flatten a tool result's parts: text parts contribute their text, anything else is ignored. */
function textOf(parts: readonly unknown[]): string {
  const pieces: string[] = [];
  for (const part of parts) {
    if (typeof part === 'string') {
      pieces.push(part);
    } else if (isTextPart(part)) {
      pieces.push(part.value);
    }
  }
  return pieces.join('\n');
}

function isTextPart(part: unknown): part is vscode.LanguageModelTextPart {
  return (
    part instanceof vscode.LanguageModelTextPart ||
    (typeof part === 'object' &&
      part !== null &&
      (part as { constructor?: { name?: string } }).constructor?.name === 'LanguageModelTextPart' &&
      typeof (part as { value?: unknown }).value === 'string')
  );
}
