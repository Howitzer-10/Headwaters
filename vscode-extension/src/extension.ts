/**
 * Extension entry point (HDW-506).
 *
 * Activation does exactly two things: register the Headwaters language model provider under the
 * vendor declared in `package.json`'s `contributes.languageModelChatProviders`, and register a
 * command that makes VS Code re-read the model list. Nothing is spawned, opened or shown: if the
 * daemon is not running the picker is simply empty, which is the normal state on a machine that has
 * not started Headwaters.
 */

import * as vscode from 'vscode';

import { HeadwatersProvider, VENDOR } from './provider';

export const REFRESH_COMMAND = 'headwaters.refreshModels';

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel('Headwaters');
  const provider = new HeadwatersProvider(process.env, log);

  context.subscriptions.push(
    log,
    provider,
    vscode.lm.registerLanguageModelChatProvider(VENDOR, provider),
    vscode.commands.registerCommand(REFRESH_COMMAND, () => {
      provider.refresh();
      log.appendLine('[hdw-506] model list refreshed');
    }),
  );
}

export function deactivate(): void {
  // Everything is on the extension context's subscriptions and is disposed with it.
}
