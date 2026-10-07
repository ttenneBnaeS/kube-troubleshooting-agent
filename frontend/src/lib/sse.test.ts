// Run with `npm test` (Node's built-in runner; Node strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";

import { createSSEParser } from "./sse.ts";

test("parses CRLF-framed events", () => {
  const push = createSSEParser();
  assert.deepEqual(push("event: token\r\ndata: hello\r\n\r\nevent: done\r\ndata: \r\n\r\n"), [
    { event: "token", data: "hello" },
    { event: "done", data: "" },
  ]);
});

test("rejoins multi-line data", () => {
  const push = createSSEParser();
  assert.deepEqual(push("event: token\ndata: line one\ndata: line two\n\n"), [
    { event: "token", data: "line one\nline two" },
  ]);
});

test("keeps an incomplete frame until the rest arrives", () => {
  const push = createSSEParser();
  assert.deepEqual(push("event: tok"), []);
  assert.deepEqual(push("en\ndata: a"), []);
  assert.deepEqual(push("bc\n\n"), [{ event: "token", data: "abc" }]);
});

test("a CRLF split across chunks leaves no stray carriage return", () => {
  const push = createSSEParser();
  assert.deepEqual(push("event: token\r\ndata: hi\r"), []);
  assert.deepEqual(push("\n\r\n"), [{ event: "token", data: "hi" }]);
});

test("preserves leading spaces beyond the one after the colon", () => {
  const push = createSSEParser();
  assert.deepEqual(push("event: token\ndata:   indented\n\n"), [{ event: "token", data: "  indented" }]);
});

test("step payloads arrive as parseable JSON", () => {
  const push = createSSEParser();
  const [evt] = push('event: step\r\ndata: {"type": "followup"}\r\n\r\n');
  assert.deepEqual(JSON.parse(evt.data), { type: "followup" });
});
