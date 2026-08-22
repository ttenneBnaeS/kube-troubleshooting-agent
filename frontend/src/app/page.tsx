"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";

type Message = { role: "user" | "assistant"; content: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function streamChat(
  message: string,
  history: Message[],
  onToken: (text: string) => void,
) {
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat request failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // SSE line endings may be CRLF; normalize before framing on blank lines.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
      const eventName = lines
        .find((l) => l.startsWith("event:"))
        ?.slice("event:".length)
        .trim();
      // A single SSE event can carry multiple "data:" lines (embedded
      // newlines in the value) that must be rejoined, not just the first.
      const data = lines
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice("data:".length).replace(/^ /, ""))
        .join("\n");
      if (eventName === "token" && data) onToken(data);
      if (eventName === "error") throw new Error(data || "stream error");
    }
  }
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);

  async function send() {
    const text = input.trim();
    if (!text || pending) return;

    const history = messages;
    const userMsg: Message = { role: "user", content: text };
    setMessages([...history, userMsg, { role: "assistant", content: "" }]);
    setInput("");
    setPending(true);

    try {
      await streamChat(text, history, (token) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + token };
          return next;
        });
      });
    } catch (err) {
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: "assistant",
          content: `Error: ${(err as Error).message}`,
        };
        return next;
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-2xl flex-1 flex-col px-4 py-8">
        <h1 className="mb-6 text-xl font-semibold text-black dark:text-zinc-50">
          Kubernetes Troubleshooting Agent
        </h1>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto pb-4">
          {messages.length === 0 && (
            <p className="text-sm text-zinc-500">
              Ask about a pod, deployment, service, or node — the agent can
              call read-only cluster tools and search official K8s docs to
              ground its answer (Week 3: bounded multi-round tool loop,
              not yet the full LangGraph investigation loop).
            </p>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={
                m.role === "user"
                  ? "min-w-0 max-w-[90%] self-end rounded-2xl bg-black px-4 py-2 text-white dark:bg-zinc-50 dark:text-black"
                  : "min-w-0 max-w-[90%] self-start rounded-2xl bg-zinc-200 px-4 py-2 text-black dark:bg-zinc-800 dark:text-zinc-50"
              }
            >
              {m.content ? (
                m.role === "assistant" ? (
                  <div
                    className="space-y-2 break-words text-sm leading-relaxed
                      [&_code]:rounded [&_code]:bg-black/10 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em] dark:[&_code]:bg-white/10
                      [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5
                      [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-black/10 [&_pre]:p-2 [&_pre]:text-[0.85em] dark:[&_pre]:bg-white/10 [&_pre_code]:bg-transparent [&_pre_code]:p-0
                      [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold"
                  >
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                  </div>
                ) : (
                  <p className="whitespace-pre-wrap break-words text-sm">{m.content}</p>
                )
              ) : pending && i === messages.length - 1 ? (
                <p className="text-sm">…</p>
              ) : null}
            </div>
          ))}
        </div>

        <form
          className="mt-4 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <input
            className="flex-1 rounded-full border border-zinc-300 bg-white px-4 py-2 text-sm text-black outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
            placeholder="Describe what's going wrong..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={pending}
          />
          <button
            type="submit"
            className="rounded-full bg-black px-5 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-50 dark:text-black"
            disabled={pending || !input.trim()}
          >
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
