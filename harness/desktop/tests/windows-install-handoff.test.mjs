import assert from "node:assert/strict";
import { test, before, after } from "node:test";
import { createRequire, Module } from "node:module";
import { EventEmitter } from "node:events";
import { updateFeed } from "./helpers/update-feed.mjs";

// Keep the real quitAndInstall, install and NSIS argument/fallback logic. The
// Electron event emitter, process spawn and app quit are the external boundaries.
const require = createRequire(import.meta.url);
const electronPath = require.resolve("electron");
const original = require.cache[electronPath];
before(() => {
  const stub = new Module(electronPath);
  stub.loaded = true;
  stub.exports = { autoUpdater: new EventEmitter() };
  require.cache[electronPath] = stub;
});
after(() => {
  if (original) require.cache[electronPath] = original;
  else delete require.cache[electronPath];
});

test("real quitAndInstall cannot quit after an asynchronous installer spawn failure", async (t) => {
  const demo = await updateFeed(t, { realInstall: true });
  await demo.flow.check();
  await demo.flow.download();
  demo.answers.installError = true;
  await demo.flow.restart();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(demo.flow.snapshot().phase, "failed");
  assert.equal(demo.gateOpen(), true);
  assert.deepEqual(demo.installs, []);
  assert.deepEqual(demo.quits, [], "the library's queued quit must not stop the Host after a failed spawn");
  demo.answers.installError = false;
  await demo.flow.download();
  await demo.flow.restart();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(demo.quits, [{ gateOpen: false, busy: [] }], "a manual retry can complete a new protected handoff");
});

test("real quitAndInstall hands a verified update to NSIS with reopen and a closed gate", async (t) => {
  const demo = await updateFeed(t, { realInstall: true });
  await demo.flow.check();
  await demo.flow.download();
  await demo.flow.restart();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(demo.flow.snapshot().phase, "installing");
  assert.deepEqual(demo.installs, [{ args: ["--updated", "/S", "--force-run"], gateOpen: false, busy: [] }]);
  assert.deepEqual(demo.quits, [{ gateOpen: false, busy: [] }]);
});
