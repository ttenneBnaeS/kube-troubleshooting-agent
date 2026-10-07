// Incremental parser for the backend's SSE stream (`event: token` /
// `step` / `error` / `done`). Hand-rolled rather than `EventSource`
// because that can't send a POST body.
//
// Two framing rules, each the fix for a bug that silently dropped content
// rather than erroring:
// - sse-starlette emits CRLF line endings. They're normalized across the
//   whole buffer, not per network chunk, so a `\r\n` split between two
//   chunks can't leave a stray `\r` on the end of a token.
// - One event can carry several `data:` lines (a value with embedded
//   newlines); they're rejoined with `\n`, never just the first kept.

export type SSEEvent = { event: string; data: string };

export function createSSEParser() {
  let buffer = "";

  return function push(chunk: string): SSEEvent[] {
    buffer = (buffer + chunk).replace(/\r\n/g, "\n");
    const frames = buffer.split("\n\n");
    // The last frame may be incomplete; keep it for the next chunk. A
    // trailing lone `\r` stays in it too, until its `\n` arrives.
    buffer = frames.pop() ?? "";

    const events: SSEEvent[] = [];
    for (const frame of frames) {
      const lines = frame.split("\n");
      const event = lines
        .find((l) => l.startsWith("event:"))
        ?.slice("event:".length)
        .trim();
      if (!event) continue;
      const data = lines
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice("data:".length).replace(/^ /, ""))
        .join("\n");
      events.push({ event, data });
    }
    return events;
  };
}
