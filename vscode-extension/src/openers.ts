/**
 * The one place that opens a URL in the user's browser (HDW-507).
 *
 * It is a module-level hook only so the extension-host test can observe that `openDashboard`
 * really asked VS Code to open the daemon's dashboard URL; `vscode.env.openExternal` opens a real
 * browser and cannot be asserted on. Production never replaces it.
 */

import * as vscode from 'vscode';

export type ExternalOpener = (url: string) => Thenable<boolean>;

const defaultOpener: ExternalOpener = (url) => vscode.env.openExternal(vscode.Uri.parse(url));

let opener: ExternalOpener = defaultOpener;

export function openExternal(url: string): Thenable<boolean> {
  return opener(url);
}

/** Test seam. Returns the previous opener so a suite can restore it. */
export function setExternalOpener(next: ExternalOpener): ExternalOpener {
  const previous = opener;
  opener = next;
  return previous;
}

export function resetExternalOpener(): void {
  opener = defaultOpener;
}
