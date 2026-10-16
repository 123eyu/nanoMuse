import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// The window opens on loading.html and then loads the Host; a back press (a mouse's side
// button too) must not return to the splash, which never moves on by itself.
const text = readFileSync(new URL("../out/main.js", import.meta.url), "utf8");
const source = ts.createSourceFile("main.js", text, ts.ScriptTarget.Latest, true);
const loadHost = source.statements
  .find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "loadHost")
  .getText(source);

function window(destroyed = false) {
  const calls = [];
  return {
    calls,
    isDestroyed: () => destroyed,
    loadURL: async (url) => { calls.push(`load ${url}`); },
    webContents: { navigationHistory: { clear: () => calls.push("clear history") } },
  };
}

function run(win) {
  const context = vm.createContext({ win });
  vm.runInContext(loadHost, context);
  return vm.runInContext("loadHost(win, 'http://127.0.0.1:1/')", context);
}

test("loading the Host drops the splash from the window's history", async () => {
  const win = window();
  await run(win);
  assert.deepEqual(win.calls, ["load http://127.0.0.1:1/", "clear history"]);
});

test("a window closed while the Host loads is left alone", async () => {
  const win = window(true);
  await run(win);
  assert.deepEqual(win.calls, ["load http://127.0.0.1:1/"]);
});

test("every Host load in the shell goes through loadHost", () => {
  assert.doesNotMatch(text, /\.loadURL\(hostUrl\)/);
});
