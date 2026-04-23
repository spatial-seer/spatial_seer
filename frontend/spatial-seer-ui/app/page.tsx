"use client";

import { useEffect, useMemo, useState } from "react";
import { createClient, type RealtimeChannel } from "@supabase/supabase-js";

type LivePrediction = {
  id: number;
  created_at: string;
  trial_id: number | null;
  predicted_room: string;
};

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

// Module-scoped client so we don't recreate it on every render. Returns null
// when env vars are missing so the UI can render a helpful setup banner
// instead of crashing at import time.
const supabase =
  SUPABASE_URL && SUPABASE_ANON_KEY
    ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
    : null;

const HISTORY_LIMIT = 10;

// Per-room theming for the big status header. Keys are normalized lowercase
// and stripped of non-alphanumerics so "Living Room", "living_room", and
// "living-room" all match the same entry.
const ROOM_THEME: Record<
  string,
  { bg: string; ring: string; text: string; label: string }
> = {
  kitchen: {
    bg: "bg-red-600",
    ring: "ring-red-400/40",
    text: "text-white",
    label: "Kitchen",
  },
  lab: {
    bg: "bg-blue-600",
    ring: "ring-blue-400/40",
    text: "text-white",
    label: "Lab",
  },
  hallway: {
    bg: "bg-zinc-600",
    ring: "ring-zinc-400/40",
    text: "text-white",
    label: "Hallway",
  },
  bedroom: {
    bg: "bg-purple-600",
    ring: "ring-purple-400/40",
    text: "text-white",
    label: "Bedroom",
  },
  livingroom: {
    bg: "bg-emerald-600",
    ring: "ring-emerald-400/40",
    text: "text-white",
    label: "Living Room",
  },
  bathroom: {
    bg: "bg-cyan-600",
    ring: "ring-cyan-400/40",
    text: "text-white",
    label: "Bathroom",
  },
  office: {
    bg: "bg-amber-600",
    ring: "ring-amber-400/40",
    text: "text-white",
    label: "Office",
  },
  outside: {
    bg: "bg-teal-700",
    ring: "ring-teal-400/40",
    text: "text-white",
    label: "Outside",
  },
};

const DEFAULT_THEME = {
  bg: "bg-zinc-800",
  ring: "ring-zinc-500/40",
  text: "text-white",
  label: "Unknown",
};

function themeForRoom(room: string | undefined | null) {
  if (!room) return DEFAULT_THEME;
  const key = room.toLowerCase().replace(/[^a-z0-9]/g, "");
  return ROOM_THEME[key] ?? { ...DEFAULT_THEME, label: prettifyRoom(room) };
}

function prettifyRoom(room: string) {
  return room
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function Home() {
  const [predictions, setPredictions] = useState<LivePrediction[]>([]);
  const [status, setStatus] = useState<
    "loading" | "connected" | "disconnected" | "missing-env" | "error"
  >(supabase ? "loading" : "missing-env");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;

    let channel: RealtimeChannel | null = null;
    let cancelled = false;

    async function bootstrap() {
      const { data, error: fetchError } = await supabase!
        .from("live_predictions")
        .select("id, created_at, trial_id, predicted_room")
        .order("created_at", { ascending: false })
        .limit(HISTORY_LIMIT);

      if (cancelled) return;

      if (fetchError) {
        setError(fetchError.message);
        setStatus("error");
        return;
      }

      setPredictions((data as LivePrediction[] | null) ?? []);

      channel = supabase!
        .channel("live-predictions-feed")
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "live_predictions",
          },
          (payload) => {
            const row = payload.new as LivePrediction;
            setPredictions((prev) => {
              if (prev.some((p) => p.id === row.id)) return prev;
              return [row, ...prev].slice(0, HISTORY_LIMIT);
            });
          },
        )
        .subscribe((realtimeStatus) => {
          if (realtimeStatus === "SUBSCRIBED") setStatus("connected");
          else if (
            realtimeStatus === "CHANNEL_ERROR" ||
            realtimeStatus === "TIMED_OUT" ||
            realtimeStatus === "CLOSED"
          ) {
            setStatus("disconnected");
          }
        });
    }

    bootstrap();

    return () => {
      cancelled = true;
      if (channel) supabase!.removeChannel(channel);
    };
  }, []);

  const latest = predictions[0];
  const theme = useMemo(
    () => themeForRoom(latest?.predicted_room),
    [latest?.predicted_room],
  );

  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      {status === "missing-env" && <MissingEnvBanner />}

      <header
        className={`relative flex w-full flex-col items-center justify-center gap-4 px-8 py-16 transition-colors duration-500 ${theme.bg} ${theme.text} ring-8 ring-inset ${theme.ring}`}
      >
        <span className="text-sm font-medium uppercase tracking-[0.35em] opacity-80">
          Currently predicted room
        </span>
        <h1 className="text-center text-6xl font-black uppercase tracking-tight sm:text-8xl">
          {latest ? theme.label : status === "loading" ? "…" : "No data yet"}
        </h1>
        {latest ? (
          <div className="mt-2 flex flex-wrap items-center justify-center gap-x-6 gap-y-1 text-sm opacity-90">
            <span>
              trial_id:{" "}
              <span className="font-mono">{latest.trial_id ?? "—"}</span>
            </span>
            <span>at {formatTime(latest.created_at)}</span>
            <ConnectionDot status={status} />
          </div>
        ) : (
          <ConnectionDot status={status} />
        )}
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <div className="mb-4 flex items-end justify-between">
          <h2 className="text-xl font-semibold">Recent predictions</h2>
          <span className="text-xs uppercase tracking-widest text-zinc-400">
            last {HISTORY_LIMIT} · live
          </span>
        </div>

        {error && (
          <div className="mb-6 rounded-lg border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-200">
            <strong className="mr-2 font-semibold">Error:</strong>
            {error}
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/60 shadow-xl shadow-black/20">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/80 text-left text-xs uppercase tracking-wider text-zinc-400">
              <tr>
                <th className="px-5 py-3">#</th>
                <th className="px-5 py-3">Time</th>
                <th className="px-5 py-3">Trial</th>
                <th className="px-5 py-3">Predicted room</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {predictions.length === 0 && status !== "missing-env" && (
                <tr>
                  <td
                    colSpan={4}
                    className="px-5 py-10 text-center text-zinc-500"
                  >
                    {status === "loading"
                      ? "Loading predictions…"
                      : "Waiting for the first prediction."}
                  </td>
                </tr>
              )}
              {predictions.map((p, idx) => {
                const rowTheme = themeForRoom(p.predicted_room);
                const isLatest = idx === 0;
                return (
                  <tr
                    key={p.id}
                    className={`transition-colors ${
                      isLatest
                        ? "bg-zinc-800/60"
                        : "hover:bg-zinc-800/40"
                    }`}
                  >
                    <td className="px-5 py-3 font-mono text-zinc-500">
                      {p.id}
                    </td>
                    <td className="px-5 py-3 text-zinc-300">
                      {formatTime(p.created_at)}
                    </td>
                    <td className="px-5 py-3 font-mono text-zinc-400">
                      {p.trial_id ?? "—"}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${rowTheme.bg} ${rowTheme.text}`}
                      >
                        {rowTheme.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="mt-6 text-center text-xs text-zinc-500">
          Predictions stream from Supabase Realtime on{" "}
          <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-300">
            public.live_predictions
          </code>
          . Insert a row in{" "}
          <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-zinc-300">
            hardware_data
          </code>{" "}
          to trigger one.
        </p>
      </main>
    </div>
  );
}

function ConnectionDot({
  status,
}: {
  status:
    | "loading"
    | "connected"
    | "disconnected"
    | "missing-env"
    | "error";
}) {
  const map = {
    loading: { dot: "bg-yellow-300", label: "connecting" },
    connected: { dot: "bg-emerald-400", label: "live" },
    disconnected: { dot: "bg-red-400", label: "reconnecting" },
    "missing-env": { dot: "bg-red-500", label: "env missing" },
    error: { dot: "bg-red-500", label: "error" },
  } as const;
  const { dot, label } = map[status];
  return (
    <span className="inline-flex items-center gap-2 text-xs uppercase tracking-widest opacity-90">
      <span
        className={`inline-block h-2.5 w-2.5 animate-pulse rounded-full ${dot}`}
      />
      {label}
    </span>
  );
}

function MissingEnvBanner() {
  return (
    <div className="bg-red-600 px-6 py-3 text-center text-sm font-medium text-white">
      Missing{" "}
      <code className="mx-1 rounded bg-red-900/50 px-1.5 py-0.5 font-mono">
        NEXT_PUBLIC_SUPABASE_URL
      </code>{" "}
      or{" "}
      <code className="mx-1 rounded bg-red-900/50 px-1.5 py-0.5 font-mono">
        NEXT_PUBLIC_SUPABASE_ANON_KEY
      </code>
      . Populate <code className="mx-1 font-mono">.env.local</code> and
      restart <code className="mx-1 font-mono">next dev</code>.
    </div>
  );
}
