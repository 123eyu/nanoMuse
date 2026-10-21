/**
 * Give an Electron-hosted Windows ACL runner a console before it restricts its token.
 *
 * This follows the MIT-licensed DSH Desktop console-host adapter:
 * https://github.com/anywhere-labs/dsh-desktop
 */

import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { join } from 'node:path'

const SW_HIDE = 0
const DONOR_READY = 'nanomuse-console-ready'
const DONOR_TIMEOUT_MS = 10_000

export interface WindowsConsoleHostApi {
  hasConsole: () => boolean
  allocWindowlessConsole?: (() => boolean) | undefined
  attachConsole: (processId: number) => number
  getConsoleWindow: () => unknown
  allocConsole: () => number
  getLastError: () => number
  showWindow: (window: unknown, command: number) => number
}

export type WindowsConsoleHostApiLoader = () => WindowsConsoleHostApi

export interface WindowsConsoleDonor {
  pid: number
  release: () => void
}

export type WindowsConsoleDonorLauncher = () => Promise<WindowsConsoleDonor>

export function loadWindowsConsoleHostApi(): WindowsConsoleHostApi {
  const koffi = createRequire(import.meta.url)('koffi') as typeof import('koffi').default
  const kernel32 = koffi.load('kernel32.dll')
  const user32 = koffi.load('user32.dll')
  const getConsoleCodePage = kernel32.func('uint32 __stdcall GetConsoleCP()')
  return {
    hasConsole: () => getConsoleCodePage() !== 0,
    allocWindowlessConsole: loadWindowlessAllocation(koffi, kernel32),
    attachConsole: kernel32.func('int __stdcall AttachConsole(uint32)'),
    getConsoleWindow: kernel32.func('void * __stdcall GetConsoleWindow()'),
    allocConsole: kernel32.func('int __stdcall AllocConsole()'),
    getLastError: kernel32.func('uint32 __stdcall GetLastError()'),
    showWindow: user32.func('int __stdcall ShowWindow(void *, int)'),
  }
}

function loadWindowlessAllocation(
  koffi: typeof import('koffi').default,
  kernel32: ReturnType<typeof import('koffi').default.load>,
): (() => boolean) | undefined {
  const ALLOC_CONSOLE_MODE_NO_WINDOW = 2
  const ALLOC_CONSOLE_RESULT_NO_CONSOLE = 0
  const options = koffi.struct({ mode: 'int', useShowWindow: 'int', showWindow: 'uint16' })
  let allocate: (options: unknown, result: number[]) => number
  try {
    allocate = kernel32.func('AllocConsoleWithOptions', 'int32', [
      koffi.pointer(options),
      koffi.out(koffi.pointer('int')),
    ]) as typeof allocate
  } catch {
    return undefined
  }
  return () => {
    const result = [ALLOC_CONSOLE_RESULT_NO_CONSOLE]
    const status = allocate(
      { mode: ALLOC_CONSOLE_MODE_NO_WINDOW, useShowWindow: 0, showWindow: 0 },
      result,
    )
    return status >= 0 && result[0] !== ALLOC_CONSOLE_RESULT_NO_CONSOLE
  }
}

/**
 * Older Windows has no AllocConsoleWithOptions. A hidden cmd process owns a windowless
 * console long enough for the ACL runner to attach to it.
 */
function launchWindowsConsoleDonor(): Promise<WindowsConsoleDonor> {
  const shell = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'cmd.exe')
  const donor = spawn(shell, ['/d', '/q', '/k', `echo ${DONOR_READY}`], {
    stdio: ['pipe', 'pipe', 'ignore'],
    windowsHide: true,
  })
  const release = (): void => {
    donor.stdout.destroy()
    donor.stdin.end()
    if (donor.exitCode === null && donor.signalCode === null) donor.kill()
  }
  return new Promise((resolve, reject) => {
    let output = ''
    const fail = (cause: Error): void => {
      clearTimeout(timer)
      release()
      reject(cause)
    }
    const timer = setTimeout(
      () => fail(new Error('console donor did not start')),
      DONOR_TIMEOUT_MS,
    )
    donor.once('error', fail)
    donor.once('exit', () => fail(new Error('console donor exited early')))
    donor.stdout.setEncoding('utf8')
    donor.stdout.on('data', (chunk: string) => {
      output += chunk
      if (!output.includes(DONOR_READY) || donor.pid === undefined) return
      clearTimeout(timer)
      donor.removeAllListeners('exit')
      donor.stdout.removeAllListeners('data')
      resolve({
        pid: donor.pid,
        release: () => {
          donor.stdout.destroy()
          donor.stdin.end()
        },
      })
    })
  })
}

async function attachToDonorConsole(
  api: WindowsConsoleHostApi,
  launchDonor: WindowsConsoleDonorLauncher,
): Promise<boolean> {
  let donor: WindowsConsoleDonor
  try {
    donor = await launchDonor()
  } catch {
    return false
  }
  try {
    return api.attachConsole(donor.pid) !== 0
  } finally {
    donor.release()
  }
}

/**
 * Establish a hidden console while the runner still has its ordinary token. The restricted
 * PowerShell child inherits it instead of trying to create conhost after token restriction,
 * which is the path that exits with 0xC0000142.
 */
export async function ensureWindowsConsoleHost(
  platform: NodeJS.Platform = process.platform,
  loadApi: WindowsConsoleHostApiLoader = loadWindowsConsoleHostApi,
  launchDonor: WindowsConsoleDonorLauncher = launchWindowsConsoleDonor,
): Promise<void> {
  if (platform !== 'win32') return
  const api = loadApi()
  if (api.hasConsole()) return
  if (api.allocWindowlessConsole?.()) return
  if (await attachToDonorConsole(api, launchDonor)) return
  if (api.allocConsole() === 0) {
    throw new Error(
      `could not allocate a console for the Windows ACL runner (Win32 ${api.getLastError()})`,
    )
  }
  const window = api.getConsoleWindow()
  if (window !== null) api.showWindow(window, SW_HIDE)
}
