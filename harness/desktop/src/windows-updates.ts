import { NsisUpdater } from "electron-updater";
import type { AppAdapter } from "electron-updater/out/AppAdapter";
import type { UpdateInfo } from "builder-util-runtime";

/** Keep the library's updater and transport; only its queued quit is conditional. */
export class RestartNsisUpdater extends NsisUpdater {
  constructor(canQuit: () => boolean, adapter?: AppAdapter) {
    super(null, adapter);
    const quit = this.app.quit.bind(this.app);
    this.app.quit = () => { if (canQuit()) quit(); };
  }
}

export type UpdatePhase = "idle" | "checking" | "current" | "available" | "downloading" | "verifying" | "ready" | "failed" | "installing";
export interface WindowsUpdateState {
  currentVersion: string;
  phase: UpdatePhase;
  version: string | null;
  progress: number | null;
  source: "mirror" | "github" | null;
  failure: "check" | "download" | "restart" | null;
  checkedAt: number | null;
}

interface Sources {
  mirrorCheck: string;
  githubCheck: string;
  mirrorVersion(version: string): string;
  githubVersion(version: string): string;
}

const SOURCES: Sources = {
  mirrorCheck: "https://nanomuse.cn/dl/win/",
  githubCheck: "https://github.com/nano-muse/nanoMuse/releases/latest/download/",
  mirrorVersion: (version) => `https://nanomuse.cn/dl/v${version}/`,
  githubVersion: (version) => `https://github.com/nano-muse/nanoMuse/releases/download/v${version}/`,
};

export interface WindowsUpdatesOptions {
  currentVersion: string;
  supported: boolean;
  createUpdater(canQuit: () => boolean): NsisUpdater;
  /** The protected exit invokes install only after its final quiet check. */
  restart(install: () => void): Promise<boolean>;
  cancelHandoff(): Promise<void>;
  sources?: Sources;
  now?(): number;
  setInterval?(fn: () => void, ms: number): NodeJS.Timeout;
  clearInterval?(timer: NodeJS.Timeout): void;
}

/** The Windows shell's one manual update flow; electron-updater owns all package I/O. */
export class WindowsUpdates {
  private state: WindowsUpdateState;
  private readonly listeners = new Set<(state: WindowsUpdateState) => void>();
  private candidate: UpdateInfo | null = null;
  private operation: Promise<void> | null = null;
  private timer: NodeJS.Timeout | null = null;
  private readonly sources: Sources;

  constructor(private readonly options: WindowsUpdatesOptions) {
    this.sources = options.sources ?? SOURCES;
    this.state = { currentVersion: options.currentVersion, phase: "idle", version: null, progress: null, source: null, failure: null, checkedAt: null };
  }

  snapshot(): WindowsUpdateState {
    return { ...this.state };
  }

  subscribe(listener: (state: WindowsUpdateState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start(): void {
    if (!this.options.supported || this.timer) return;
    void this.check();
    this.timer = (this.options.setInterval ?? setInterval)(() => { void this.check(); }, 6 * 60 * 60_000);
    this.timer.unref?.();
  }

  dispose(): void {
    if (this.timer) (this.options.clearInterval ?? clearInterval)(this.timer);
    this.timer = null;
    this.listeners.clear();
  }

  check(): Promise<void> {
    return this.run(async () => {
      const previous = this.snapshot();
      this.publish({ phase: "checking", failure: null });
      for (const source of ["mirror", "github"] as const) {
        try {
          const updater = this.updater();
          updater.setFeedURL({ provider: "generic", url: source === "mirror" ? this.sources.mirrorCheck : this.sources.githubCheck });
          const result = await updater.checkForUpdates();
          if (!result) throw new Error("Updater unavailable");
          const info = result.updateInfo;
          const newer = result.isUpdateAvailable && /^\d+\.\d+\.\d+$/.test(info.version) && updater.currentVersion.compare(info.version) < 0;
          if (newer) this.installer(info);
          const keepReady = newer && previous.phase === "ready" && this.sameCandidate(info);
          this.candidate = newer ? info : null;
          this.publish({ phase: newer ? keepReady ? "ready" : "available" : "current", version: newer ? info.version : null,
            progress: keepReady ? 100 : null, source, failure: null, checkedAt: (this.options.now ?? Date.now)() });
          return;
        } catch {
          // A failed mirror check tries the release's GitHub metadata next.
        }
      }
      this.publish({ phase: previous.phase === "ready" ? "ready" : "failed", failure: "check" });
    });
  }

  download(): Promise<void> {
    if (!this.candidate || this.state.phase === "ready") return Promise.resolve();
    return this.run(async () => {
      try {
        await this.verifiedDownload();
        this.publish({ phase: "ready", progress: 100, failure: null });
      } catch {
        this.publish({ phase: "failed", failure: "download", progress: null });
      }
    });
  }

  restart(): Promise<void> {
    if (this.state.phase !== "ready" || !this.candidate) return Promise.resolve();
    return this.run(async () => {
      try {
        // A fresh library instance hashes the cache again before it may be installed.
        const updater = await this.verifiedDownload();
        this.publish({ phase: "installing", failure: null, progress: 100 });
        let installError: Error | null = null;
        let handedOver = false;
        updater.on("error", (error) => {
          installError = error;
          if (handedOver) void this.options.cancelHandoff().then(() => {
            this.publish({ phase: "failed", failure: "restart" });
          });
        });
        const accepted = await this.options.restart(() => {
          updater.quitAndInstall(true, true);
          if (installError) throw installError;
        });
        if (installError) throw installError;
        handedOver = accepted;
        if (!accepted) this.publish({ phase: "ready", failure: "restart" });
      } catch {
        this.publish({ phase: "failed", failure: "restart", progress: null });
        await this.options.cancelHandoff();
      }
    });
  }

  private async verifiedDownload(): Promise<NsisUpdater> {
    const candidate = this.candidate!;
    this.publish({ phase: "verifying", failure: null, progress: null });
    for (const source of ["mirror", "github"] as const) {
      try {
        const updater = this.updater();
        updater.setFeedURL({ provider: "generic", url: source === "mirror" ? this.sources.mirrorVersion(candidate.version) : this.sources.githubVersion(candidate.version) });
        const check = await updater.checkForUpdates();
        if (!check?.isUpdateAvailable || !this.sameCandidate(check.updateInfo)) throw new Error("Candidate metadata changed");
        updater.on("download-progress", (progress) => {
          this.publish({ phase: progress.percent >= 100 ? "verifying" : "downloading", progress: Math.min(100, Math.max(0, progress.percent)), source });
        });
        this.publish({ phase: "downloading", progress: 0, source });
        const paths = await updater.downloadUpdate();
        if (!paths.length) throw new Error("No verified package");
        return updater;
      } catch {
        // Fallback stays on the pinned version and digest, never the next release.
      }
    }
    throw new Error("The candidate package could not be verified");
  }

  private updater(): NsisUpdater {
    let failed = false;
    const updater = this.options.createUpdater(() => !failed && this.state.phase === "installing");
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater.disableWebInstaller = true;
    updater.requestHeaders = { "User-Agent": `nanoMuse-Desktop/${this.options.currentVersion}` };
    // The library queues quitAndInstall's quit before asynchronous spawn errors arrive.
    // This updater's app adapter refuses that queued quit once this attempt has failed.
    updater.on("error", () => { failed = true; });
    return updater;
  }

  private installer(info: UpdateInfo) {
    const name = `nanoMuse-Desktop-${info.version}-win-x64.exe`;
    const files = info.files.filter((f) => /\.exe$/i.test(f.url));
    const file = files[0];
    if (files.length !== 1 || file?.url !== name || !file.sha512) throw new Error("No matching Windows installer integrity metadata");
    return file;
  }

  private sameCandidate(info: UpdateInfo): boolean {
    if (!this.candidate || info.version !== this.candidate.version) return false;
    const expected = this.installer(this.candidate);
    const actual = this.installer(info);
    return expected.sha512 === actual.sha512 && expected.size === actual.size;
  }

  private run(action: () => Promise<void>): Promise<void> {
    if (!this.options.supported || this.state.phase === "installing") return Promise.resolve();
    if (this.operation) return this.operation;
    this.operation = action().finally(() => { this.operation = null; });
    return this.operation;
  }

  private publish(patch: Partial<WindowsUpdateState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.snapshot());
  }
}
