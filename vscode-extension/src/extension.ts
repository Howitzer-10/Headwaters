/**
 * Extension entry point (HDW-506, extended by HDW-507).
 *
 * Activation does three things: register the Headwaters language model provider under the vendor
 * declared in `package.json`'s `contributes.languageModelChatProviders`, create the status bar item
 * and start its single polling interval, and register the commands. Nothing is spawned and no modal
 * is shown: if the daemon is not running the picker is empty and the bar says the daemon is not
 * reachable, which is the normal state on a machine that has not started Headwaters.
 *
 * `activate` returns a small surface so the extension-host tests can observe the item and the last
 * snapshot. It is additive: anything importing the extension still only sees `activate`.
 */

import * as vscode from 'vscode';

import { CommandDeps, registerCommands } from './commands';
import { forgetClients } from './mcpClient';
import { ExternalOpener, setExternalOpener } from './openers';
import { HeadwatersProvider, VENDOR } from './provider';
import { STATUS_POLL_MS, StatusBarController } from './statusBar';
import { Snapshot } from './statusClient';

export const REFRESH_COMMAND = 'headwaters.refreshModels';

/** What `activate` hands to the extension-host tests. */
export interface HeadwatersExtensionApi {
  readonly statusBar: StatusBarController;
  readonly log: vscode.OutputChannel;
  /** Read the daemon once and render it, exactly as the tick does. */
  refreshStatus(): Promise<Snapshot | undefined>;
  /** The last snapshot the bar rendered, or undefined before the first read. */
  lastSnapshot(): Snapshot | undefined;
  /** The literal text currently on the item. */
  statusText(): string;
  /** Replace the URL opener so a test can see what `openDashboard` asks VS Code to open. */
  setExternalOpener(opener: ExternalOpener): void;
  /** Replace the `headwaters doctor` runner so a test can drive both outcomes. */
  setDoctorRunner(runner: CommandDeps['doctor']): void;
}

let api: HeadwatersExtensionApi | undefined;

export function activate(context: vscode.ExtensionContext): HeadwatersExtensionApi {
  const log = vscode.window.createOutputChannel('Headwaters');
  const provider = new HeadwatersProvider(process.env, log);
  const statusBar = new StatusBarController(process.env, STATUS_POLL_MS);

  let doctor: CommandDeps['doctor'];
  registerCommands(context, {
    log,
    refresh: () => statusBar.refresh(),
    get doctor() {
      return doctor;
    },
  });

  context.subscriptions.push(
    log,
    provider,
    statusBar,
    vscode.lm.registerLanguageModelChatProvider(VENDOR, provider),
    vscode.commands.registerCommand(REFRESH_COMMAND, () => {
      provider.refresh();
      log.appendLine('[hdw-506] model list refreshed');
    }),
  );

  statusBar.start();

  api = {
    statusBar,
    log,
    refreshStatus: () => statusBar.refresh(),
    lastSnapshot: () => statusBar.snapshot,
    statusText: () => statusBar.item.text,
    setExternalOpener: (opener) => {
      setExternalOpener(opener);
    },
    setDoctorRunner: (runner) => {
      doctor = runner;
    },
  };
  return api;
}

export function deactivate(): void {
  // Everything is on the extension context's subscriptions and is disposed with it.
  forgetClients();
  api = undefined;
}
