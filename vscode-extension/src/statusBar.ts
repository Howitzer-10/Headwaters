/**
 * The status bar item (HDW-507).
 *
 * One item, one interval, one rule: it shows what the last read said, and when a read fails it
 * says so. It never keeps the previous numbers on screen — a status bar that shows a stale savings
 * figure after the daemon dies is worse than an empty one.
 *
 * The four fields and their real sources:
 *   tier / model — `GET /v1/status` `tray.tier` (tier of the last routed model) and `tray.warm_model`
 *   wall state   — `GET /v1/status` `tray.walls` (active wall kinds)
 *   savings      — MCP `headwaters_status` `savings` (the only surface carrying it)
 *   Private lock — `GET /v1/status` `private_lock`, coloured `charts.purple`
 */

import * as vscode from 'vscode';
import { readSnapshot, Snapshot } from './statusClient';

/** The registration `charts.purple` in `src/vs/platform/theme/common/colors/chartsColors.ts`. */
export const PRIVATE_LOCK_COLOR = 'charts.purple';

/** The literal unavailable text. No tier, no savings, no stale reading. */
export const UNAVAILABLE_TEXT = '$(error) Headwaters — daemon not reachable';

/** Before the first read finishes, say so instead of showing a number nobody read. */
export const READING_TEXT = '$(sync~spin) Headwaters — reading status…';

/**
 * One poll a minute, and no retry on failure: the next tick is the retry. This is the conservative
 * default for a couple of loopback GETs, and the item is refreshed on demand by every command too.
 */
export const STATUS_POLL_MS = 60_000;

export const OPEN_DASHBOARD_COMMAND = 'headwaters.openDashboard';

export interface BarPresentation {
  readonly text: string;
  readonly tooltip: vscode.MarkdownString;
  /** A registered theme colour id, or null for the theme's normal status bar foreground. */
  readonly colorId: string | null;
}

/** D19: never write "paid" bare — the tier is "paid tier". */
export function tierLabel(tier: string | null): string {
  switch (tier) {
    case 'paid':
      return 'paid tier';
    case 'free_cloud':
      return 'free cloud';
    case 'local':
      return 'local';
    case 'other':
      return 'other tier';
    default:
      return 'no tier yet';
  }
}

/** What the item should look like for a snapshot. Pure, so tests can pin every field. */
export function presentBar(snapshot: Snapshot | undefined): BarPresentation {
  const tooltip = new vscode.MarkdownString(undefined, true);
  if (snapshot === undefined) {
    tooltip.appendMarkdown('**Headwaters** — reading the daemon status.\n\n');
    tooltip.appendMarkdown('_No numbers are shown until a read succeeds._');
    return { text: READING_TEXT, tooltip, colorId: null };
  }

  if (snapshot.kind === 'unavailable') {
    tooltip.appendMarkdown('**Headwaters** — daemon not reachable.\n\n');
    tooltip.appendMarkdown(`Reason: \`${snapshot.reason}\`\n\n`);
    tooltip.appendText(snapshot.detail);
    tooltip.appendMarkdown(
      '\n\nNo tier, wall state or savings are shown: the previous reading is not reused.\n\n' +
        `The extension tries again on its next tick (every ${Math.round(STATUS_POLL_MS / 1000)} s).`,
    );
    return { text: UNAVAILABLE_TEXT, tooltip, colorId: null };
  }

  const parts = [
    `$(pulse) ${tierLabel(snapshot.tier)}`,
    `warm ${snapshot.warmModel ?? '—'}`,
    snapshot.walls.length === 0
      ? 'no walls'
      : `${String(snapshot.walls.length)} ${snapshot.walls.length === 1 ? 'wall' : 'walls'}`,
    `savings ${snapshot.savings.text}`,
  ];
  if (snapshot.privateLock) {
    parts.push('$(lock) Private');
  }

  tooltip.appendMarkdown('**Headwaters** — scope and savings in force.\n\n');
  const row = (label: string, value: string): void => {
    tooltip.appendMarkdown(`- ${label}: ${value}\n`);
  };
  row('Tier (last routed model)', `\`${snapshot.tier ?? 'none yet'}\``);
  row('Warm model', snapshot.warmModel === null ? 'none' : `\`${snapshot.warmModel}\``);
  row(
    'Active walls',
    snapshot.walls.length === 0 ? 'none' : snapshot.walls.map((w) => `\`${w}\``).join(', '),
  );
  row('Last outcome', snapshot.lastStatus === null ? 'no decisions yet' : `\`${snapshot.lastStatus}\``);
  row('Scope', `${snapshot.scope ?? 'unknown'} (effective: ${snapshot.effectiveScope ?? 'unknown'})`);
  row(
    'Private Mode',
    snapshot.privateLock
      ? 'on — nothing leaves this machine'
      : snapshot.airplane
        ? 'off (Airplane Mode is on, so the effective scope is local)'
        : 'off',
  );
  row('Savings', `${snapshot.savings.text} — ${snapshot.savingsNote}`);
  row(
    'Pinned model',
    snapshot.pinnedModel === null
      ? 'none'
      : `\`${snapshot.pinnedModel}\` (front-door MCP tool calls only)`,
  );
  tooltip.appendMarkdown(
    '\nSavings is an estimate against one reference model, not money in hand; `—` means the ledger ' +
      'has no estimate, which is not the same as `$0.00`.\n\n',
  );
  tooltip.appendMarkdown(
    `Read ${new Date(snapshot.readAt).toLocaleTimeString()}. Click to open the dashboard.`,
  );

  return {
    text: parts.join(' · '),
    tooltip,
    colorId: snapshot.privateLock ? PRIVATE_LOCK_COLOR : null,
  };
}

/**
 * Owns the item, the interval and the in-flight guard. `refresh()` never rejects: a failed read
 * becomes an unavailable snapshot, which is what the bar renders.
 */
export class StatusBarController implements vscode.Disposable {
  readonly item: vscode.StatusBarItem;
  private current: Snapshot | undefined;
  private timer: NodeJS.Timeout | undefined;
  /** The read in progress, if any. A second caller joins it instead of starting another. */
  private pending: Promise<Snapshot | undefined> | undefined;
  private disposed = false;

  constructor(
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly pollMs: number = STATUS_POLL_MS,
  ) {
    this.item = vscode.window.createStatusBarItem(
      'headwaters.status',
      vscode.StatusBarAlignment.Right,
      100,
    );
    this.item.name = 'Headwaters';
    this.item.command = OPEN_DASHBOARD_COMMAND;
    this.render();
  }

  get snapshot(): Snapshot | undefined {
    return this.current;
  }

  get presentation(): BarPresentation {
    return presentBar(this.current);
  }

  /** Show the item and start the single interval. The first read starts immediately. */
  start(): void {
    this.item.show();
    if (this.timer === undefined) {
      this.timer = setInterval(() => {
        void this.refresh();
      }, this.pollMs);
    }
    void this.refresh();
  }

  /**
   * Read once. Concurrent calls join the in-flight read instead of stacking requests, and a failed
   * read replaces the snapshot rather than keeping the last one.
   */
  async refresh(read?: (env: NodeJS.ProcessEnv) => Promise<Snapshot>): Promise<Snapshot | undefined> {
    if (this.pending !== undefined) {
      return this.pending;
    }
    const pending = this.readOnce(read);
    this.pending = pending;
    try {
      return await pending;
    } finally {
      this.pending = undefined;
    }
  }

  private async readOnce(
    read?: (env: NodeJS.ProcessEnv) => Promise<Snapshot>,
  ): Promise<Snapshot | undefined> {
    try {
      const snapshot = await (read ?? readSnapshot)(this.env);
      if (!this.disposed) {
        this.current = snapshot;
        this.render();
      }
      return snapshot;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      const snapshot: Snapshot = {
        kind: 'unavailable',
        reason: 'read-failed',
        detail,
        readAt: Date.now(),
      };
      if (!this.disposed) {
        this.current = snapshot;
        this.render();
      }
      return snapshot;
    }
  }

  /** Render the current snapshot onto the item. */
  private render(): void {
    const presentation = presentBar(this.current);
    this.item.text = presentation.text;
    this.item.tooltip = presentation.tooltip;
    this.item.color = presentation.colorId === null ? undefined : new vscode.ThemeColor(presentation.colorId);
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== undefined) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
    this.item.dispose();
  }
}
