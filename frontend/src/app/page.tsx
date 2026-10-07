"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { createSSEParser } from "@/lib/sse";

import { Trail, type TrailStep } from "./trail";

type Message = {
  role: "user" | "assistant";
  content: string;
  trail?: TrailStep[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const THREAD_KEY = "kube-troubleshooter.threadId";

// The thread id is a convenience for picking a conversation back up after
// a reload; storage can be unavailable (private mode, blocked site data),
// in which case each page load is simply a new conversation.
function loadThreadId(): string | null {
  try {
    return localStorage.getItem(THREAD_KEY);
  } catch {
    return null;
  }
}

function saveThreadId(id: string) {
  try {
    localStorage.setItem(THREAD_KEY, id);
  } catch {
    // Not persisted; the conversation still works for this page load.
  }
}

async function fetchThread(threadId: string): Promise<Message[]> {
  const res = await fetch(`${API_URL}/api/threads/${encodeURIComponent(threadId)}`);
  if (!res.ok) throw new Error(`thread request failed: ${res.status}`);
  const body = (await res.json()) as { messages: Message[] };
  return body.messages;
}

async function streamChat(
  message: string,
  threadId: string,
  onToken: (text: string) => void,
  onStep: (step: TrailStep) => void,
) {
  // History lives server-side in the agent's checkpointer, keyed by thread.
  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat request failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSSEParser();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const { event, data } of parse(decoder.decode(value, { stream: true }))) {
      if (event === "token" && data) onToken(data);
      if (event === "step" && data) onStep(JSON.parse(data) as TrailStep);
      if (event === "error") throw new Error(data || "stream error");
    }
  }
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  // Conversation history lives server-side in the agent's checkpointer;
  // the client only keeps the thread id, so a reload can fetch it back.
  const threadId = useRef<string | null>(null);

  useEffect(() => {
    const stored = loadThreadId();
    if (!stored) {
      threadId.current = crypto.randomUUID();
      saveThreadId(threadId.current);
      return;
    }
    threadId.current = stored;
    fetchThread(stored)
      // Don't clobber a message the user already sent while this loaded.
      .then((loaded) => setMessages((prev) => (prev.length ? prev : loaded)))
      .catch(() => {
        // Backend down or the thread is gone: start fresh rather than
        // showing an error before the user has asked anything.
      });
  }, []);

  function newConversation() {
    if (pending) return;
    threadId.current = crypto.randomUUID();
    saveThreadId(threadId.current);
    setMessages([]);
  }

  function updateLast(fn: (m: Message) => Message) {
    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || pending) return;
    if (!threadId.current) {
      threadId.current = crypto.randomUUID();
      saveThreadId(threadId.current);
    }

    const userMsg: Message = { role: "user", content: text };
    setMessages([...messages, userMsg, { role: "assistant", content: "", trail: [] }]);
    setInput("");
    setPending(true);

    try {
      await streamChat(
        text,
        threadId.current,
        (token) => updateLast((m) => ({ ...m, content: m.content + token })),
        (step) => updateLast((m) => ({ ...m, trail: [...(m.trail ?? []), step] })),
      );
    } catch (err) {
      updateLast((m) => ({ ...m, content: `Error: ${(err as Error).message}` }));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-2xl flex-1 flex-col px-4 py-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
            Kubernetes Troubleshooting Agent
          </h1>
          <button
            type="button"
            className="shrink-0 rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300"
            onClick={newConversation}
            disabled={pending || messages.length === 0}
          >
            New conversation
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto pb-4">
          {messages.length === 0 && (
            <p className="text-sm text-zinc-500">
              Ask about a pod, deployment, service, or node — the agent can
              investigate with read-only cluster tools, diagnose the root
              cause, and suggest a fix citing the official K8s docs.
              Follow-up questions keep the conversation&apos;s context.
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
              {m.role === "assistant" && m.trail && (
                <Trail steps={m.trail} pending={pending && i === messages.length - 1} />
              )}
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
