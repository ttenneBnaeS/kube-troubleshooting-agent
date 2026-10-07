"use client";

import { useState } from "react";

// Mirrors backend/api/trail.py: one step per thing the agent did, streamed
// as `event: step` while it works and returned with a reloaded thread.
export type TrailStep =
  | {
      type: "sweep";
      namespace: string | null;
      pod_count?: number;
      unhealthy_pods?: { name: string; reason: string }[];
      pods_error?: string;
      warning_count?: number;
      warnings?: { object: string; reason: string; message: string }[];
      events_error?: string;
    }
  | {
      type: "tool";
      tool: string;
      args: Record<string, unknown>;
      rationale: string;
      result: string;
      truncated: boolean;
      error: string | null;
      namespace_filled: boolean;
    }
  | {
      type: "diagnosis";
      root_cause: string;
      confidence: "high" | "medium" | "low";
      citations: string[];
    }
  | { type: "docs"; docs: { title: string; url: string }[]; error: string | null }
  | { type: "followup" };

const CONFIDENCE_STYLES: Record<string, string> = {
  high: "bg-emerald-600/15 text-emerald-700 dark:text-emerald-400",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  low: "bg-red-600/15 text-red-700 dark:text-red-400",
};

function toolLabel(name: string) {
  return name.replace(/_tool$/, "");
}

function formatArgs(args: Record<string, unknown>) {
  return Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

function prettyResult(result: string) {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    // Truncated JSON no longer parses; show it as it came.
    return result;
  }
}

function summarize(steps: TrailStep[], pending: boolean) {
  if (steps.some((s) => s.type === "followup")) {
    return "Answered from earlier evidence, no new cluster reads";
  }
  const tools = steps.filter((s) => s.type === "tool").length;
  const diagnosis = steps.find((s) => s.type === "diagnosis");
  const parts = [`${tools} tool call${tools === 1 ? "" : "s"} after the initial sweep`];
  if (diagnosis && diagnosis.type === "diagnosis") {
    parts.push(`${diagnosis.confidence} confidence`);
  } else if (pending) {
    parts.push("investigating…");
  }
  return `Investigation: ${parts.join(" · ")}`;
}

function Step({ step }: { step: TrailStep }) {
  switch (step.type) {
    case "sweep":
      return (
        <li>
          <p className="font-medium">
            Initial sweep{step.namespace ? ` of ${step.namespace}` : ""}
          </p>
          <p className="text-zinc-600 dark:text-zinc-400">
            {step.pods_error
              ? `Pods: ${step.pods_error}`
              : `${step.pod_count} pod(s), ${step.unhealthy_pods?.length ?? 0} unhealthy`}
            {" · "}
            {step.events_error
              ? `Events: ${step.events_error}`
              : `${step.warning_count} warning event(s)`}
          </p>
          {!!step.unhealthy_pods?.length && (
            <ul className="mt-1 list-disc pl-5">
              {step.unhealthy_pods.map((p) => (
                <li key={p.name}>
                  <code>{p.name}</code>: {p.reason}
                </li>
              ))}
            </ul>
          )}
        </li>
      );
    case "tool":
      return (
        <li>
          <p className="font-medium">
            <code>{toolLabel(step.tool)}</code>
            <span className="font-normal text-zinc-600 dark:text-zinc-400">
              ({formatArgs(step.args)})
            </span>
            {step.error && (
              <span className="ml-2 rounded bg-zinc-500/15 px-1.5 py-0.5 text-[0.8em]">
                returned error: {step.error}
              </span>
            )}
          </p>
          {step.rationale && (
            <p className="whitespace-pre-wrap text-zinc-600 dark:text-zinc-400">
              {step.rationale}
            </p>
          )}
          <details className="mt-1">
            <summary className="cursor-pointer text-zinc-500">
              Result{step.truncated ? " (truncated)" : ""}
            </summary>
            <pre className="mt-1 max-h-64 overflow-auto rounded bg-black/5 p-2 text-[0.85em] dark:bg-white/5">
              {prettyResult(step.result)}
            </pre>
          </details>
        </li>
      );
    case "diagnosis":
      return (
        <li>
          <p className="font-medium">
            Diagnosis{" "}
            <span
              className={`rounded px-1.5 py-0.5 text-[0.8em] ${CONFIDENCE_STYLES[step.confidence] ?? ""}`}
            >
              {step.confidence} confidence
            </span>
          </p>
          <p>{step.root_cause}</p>
          {step.citations.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-zinc-600 dark:text-zinc-400">
              {step.citations.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </li>
      );
    case "docs":
      return (
        <li>
          <p className="font-medium">Reference docs</p>
          {step.error ? (
            <p className="text-zinc-600 dark:text-zinc-400">Retrieval failed: {step.error}</p>
          ) : (
            <ul className="list-disc pl-5">
              {step.docs.map((d) => (
                <li key={d.url}>
                  <a className="underline" href={d.url} target="_blank" rel="noreferrer">
                    {d.title}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </li>
      );
    case "followup":
      return (
        <li className="text-zinc-600 dark:text-zinc-400">
          Answered from the evidence earlier turns gathered.
        </li>
      );
  }
}

export function Trail({ steps, pending }: { steps: TrailStep[]; pending: boolean }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  const latest = steps[steps.length - 1];

  return (
    <div className="mb-2 rounded-xl border border-black/10 text-xs dark:border-white/10">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left font-medium"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>{summarize(steps, pending)}</span>
        <span aria-hidden>{open ? "▾" : "▸"}</span>
      </button>
      {!open && pending && latest.type === "tool" && (
        <p className="px-3 pb-2 text-zinc-600 dark:text-zinc-400">
          Last: <code>{toolLabel(latest.tool)}</code>({formatArgs(latest.args)})
        </p>
      )}
      {open && (
        <ol className="space-y-3 border-t border-black/10 px-3 py-2 break-words dark:border-white/10 [&_code]:rounded [&_code]:bg-black/10 [&_code]:px-1 dark:[&_code]:bg-white/10">
          {steps.map((s, i) => (
            <Step key={i} step={s} />
          ))}
        </ol>
      )}
    </div>
  );
}
