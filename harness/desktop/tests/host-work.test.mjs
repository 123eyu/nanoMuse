// The shell's client for the Host's work routes (src/host-work.ts): the launch
// URL's token mints the session cookie once, the cookie answers for the work
// check, the gate and the stop; a Host that answers nothing reads as idle
// rather than blocking the quit. Run after `tsc -p tsconfig.json`.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { HostWork } from "../out/host-work.js";

/** A stand-in for the Host's guarded API: the token mint, then the work routes. */
function fakeHost() {
  const seen = { cookies: [], gates: [], stops: 0 };
  let failMint = false;
  let workStatus = 200;
  let workBody = { busy: [] };
  let mint = 0;
  let valid = "";
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://x");
    const collect = async () => {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      return Buffer.concat(chunks).toString("utf8");
    };
    void (async () => {
      if (url.pathname === "/" && url.searchParams.has("token")) {
        if (failMint) {
          res.writeHead(500).end();
          return;
        }
        mint += 1;
        valid = `cookie-${mint}`;
        res.writeHead(303, { location: "./", "set-cookie": `dsh-auth-127.0.0.1=${valid}; Path=/; HttpOnly` }).end();
        return;
      }
      seen.cookies.push(req.headers.cookie ?? "");
      if (req.headers.cookie !== `dsh-auth-127.0.0.1=${valid}`) {
        res.writeHead(401).end("unauthorized");
        return;
      }
      if (req.method === "GET" && url.pathname === "/nanomuse/cloud/work") {
        res.writeHead(workStatus, { "content-type": "application/json" }).end(JSON.stringify(workBody));
        return;
      }
      if (req.method === "POST" && url.pathname === "/nanomuse/cloud/work/gate") {
        seen.gates.push(JSON.parse(await collect()));
        res.writeHead(200, { "content-type": "application/json" }).end('{"open":true}');
        return;
      }
      if (req.method === "POST" && url.pathname === "/nanomuse/cloud/work/stop") {
        seen.stops += 1;
        res.writeHead(200, { "content-type": "application/json" }).end('{"busy":[]}');
        return;
      }
      res.writeHead(404).end();
    })().catch(() => res.destroy());
  });
  const listen = new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      resolve(`http://127.0.0.1:${port}/?token=abc`);
    });
  });
  return {
    seen,
    /** The next mint hands out a new value, so the cookie the client holds goes stale. */
    rotate: () => {
      valid = `gone-${mint}`;
    },
    failMint: (on) => {
      failMint = on;
    },
    answers: (status, body) => {
      workStatus = status;
      workBody = body;
    },
    close: () =>
      new Promise((done) => {
        server.closeAllConnections?.();
        server.close(done);
      }),
    launchUrl: listen,
  };
}

test("the token mints the cookie once, and the cookie answers for the work check", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const lines = [];
  const work = new HostWork(launchUrl, { log: (line) => lines.push(line) });
  host.answers(200, { busy: [{ kind: "reply", label: "" }, { kind: "task", label: "Phone" }] });
  assert.deepEqual(await work.busy(), [{ kind: "reply", label: "" }, { kind: "task", label: "Phone" }]);
  assert.deepEqual(await work.busy(), [{ kind: "reply", label: "" }, { kind: "task", label: "Phone" }], "the second check reuses the cookie");
  assert.equal(host.seen.cookies.length, 2);
  assert.ok(host.seen.cookies.every((c) => c === "dsh-auth-127.0.0.1=cookie-1"));
  assert.deepEqual(lines, []);
  await host.close();
});

test("a stale cookie is minted again and the retried request goes through", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const work = new HostWork(launchUrl, { log: () => undefined });
  host.answers(200, { busy: [] });
  assert.deepEqual(await work.busy(), []);
  host.rotate();
  assert.deepEqual(await work.busy(), [], "the 401 is answered by a fresh mint, not an empty answer");
  assert.deepEqual(host.seen.cookies.slice(-2), ["dsh-auth-127.0.0.1=cookie-1", "dsh-auth-127.0.0.1=cookie-2"], "the stale one, then the fresh mint");
  await host.close();
});

test("a mint that fails reads as no answer, not a wrong one", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const lines = [];
  const work = new HostWork(launchUrl, { log: (line) => lines.push(line) });
  host.failMint(true);
  assert.deepEqual(await work.busy(), []);
  assert.ok(lines.some((line) => line.includes("did not mint")));
  await host.close();
});

test("the gate and the stop reach the plugin; the stop answers after the work ends", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const work = new HostWork(launchUrl, { log: () => undefined });
  await work.setGate(false);
  await work.stopAll();
  await work.setGate(true);
  assert.deepEqual(host.seen.gates, [{ open: false }, { open: true }]);
  assert.equal(host.seen.stops, 1);
  await host.close();
});

test("a Host that answers nothing reads as idle: no throw, the quit can go on", async () => {
  const lines = [];
  const work = new HostWork("http://127.0.0.1:9/?token=abc", { log: (line) => lines.push(line) });
  assert.deepEqual(await work.busy(), []);
  await work.setGate(false);
  await work.stopAll();
  await work.setGate(true);
  assert.ok(lines.length > 0, "the failure is logged, not hidden");
});

test("an older plugin without the routes answers no work, and says so once", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const lines = [];
  const work = new HostWork(launchUrl, { log: (line) => lines.push(line) });
  host.answers(404, { error: "no route" });
  assert.deepEqual(await work.busy(), []);
  assert.equal(lines.length, 1);
  await host.close();
});

test("a malformed answer reads as no work", async () => {
  const host = fakeHost();
  const launchUrl = await host.launchUrl;
  const work = new HostWork(launchUrl, { log: () => undefined });
  host.answers(200, { busy: [{ kind: "nope", label: "" }, { kind: "reply" }, 7, { kind: "computer", label: "" }] });
  assert.deepEqual(await work.busy(), [{ kind: "computer", label: "" }]);
  await host.close();
});
