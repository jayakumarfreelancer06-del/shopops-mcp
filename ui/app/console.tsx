"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, postJson } from "@/lib/api";

type Tool = { name: string; description: string; read_only: boolean };
type Overview = { calls_24h: number; failed_24h: number; p95_ms: number; tools: Tool[]; resources: string[]; prompts: string[] };
type Key = { id: number; name: string; prefix: string; scope: "read" | "read_write"; created_at: string; last_used_at: string | null; revoked_at: string | null };
type Call = { id: number; at: string; principal: string; transport: string; tool: string; arguments: Record<string, unknown>; status: string; duration_ms: number; error: string | null };

const TABS = ["Connect", "API keys", "Audit log"] as const;
type Tab = (typeof TABS)[number];

export function Console({ mcpUrl }: { mcpUrl: string }) {
  const [tab, setTab] = useState<Tab>("Connect");
  const [overview, setOverview] = useState<Overview | null>(null);

  useEffect(() => {
    api<Overview>("/api/overview").then(setOverview).catch(() => {});
  }, [tab]);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-6">
      <div className="mb-6 grid gap-3 sm:grid-cols-4">
        <Stat label="Tool calls (24h)" value={overview?.calls_24h ?? "–"} />
        <Stat label="Failed / denied (24h)" value={overview?.failed_24h ?? "–"} />
        <Stat label="p95 latency (24h)" value={overview ? `${Math.round(overview.p95_ms)} ms` : "–"} />
        <Stat label="Tools exposed" value={overview?.tools.length ?? "–"} />
      </div>

      <div className="mb-5 inline-flex rounded-lg border border-line bg-subtle p-0.5">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${tab === t ? "bg-panel shadow-sm" : "text-muted hover:text-fg"}`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Connect" && <Connect mcpUrl={mcpUrl} overview={overview} />}
      {tab === "API keys" && <Keys mcpUrl={mcpUrl} />}
      {tab === "Audit log" && <Audit />}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function Code({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border border-line bg-subtle p-3 font-mono text-xs leading-relaxed">{children}</pre>
      <button
        onClick={() => {
          navigator.clipboard.writeText(children);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        }}
        className="absolute right-2 top-2 rounded border border-line bg-panel px-2 py-0.5 text-[11px] text-muted hover:text-fg"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function snippets(mcpUrl: string, key: string) {
  return {
    claudeCode: `claude mcp add --transport http shopops ${mcpUrl} \\\n  --header "Authorization: Bearer ${key}"`,
    desktopRemote: JSON.stringify(
      { mcpServers: { shopops: { command: "npx", args: ["-y", "mcp-remote", mcpUrl, "--header", `Authorization: Bearer ${key}`] } } },
      null,
      2,
    ),
    desktopStdio: JSON.stringify(
      { mcpServers: { shopops: { command: "docker", args: ["exec", "-i", "shopops-api-1", "python", "-m", "shopops.stdio"] } } },
      null,
      2,
    ),
  };
}

function Connect({ mcpUrl, overview }: { mcpUrl: string; overview: Overview | null }) {
  const s = snippets(mcpUrl, "<YOUR_API_KEY>");
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div className="flex flex-col gap-5">
        <Section title="Claude Code (remote, Streamable HTTP)">
          <Code>{s.claudeCode}</Code>
        </Section>
        <Section title="Claude Desktop (remote, via mcp-remote)" hint="claude_desktop_config.json">
          <Code>{s.desktopRemote}</Code>
        </Section>
        <Section title="Claude Desktop (local, stdio)" hint="Runs inside the api container; the caller is the local operator.">
          <Code>{s.desktopStdio}</Code>
        </Section>
        <Section title="MCP Inspector">
          <Code>{`npx -y @modelcontextprotocol/inspector\n# Transport: Streamable HTTP · URL: ${mcpUrl}\n# Header: Authorization = Bearer <YOUR_API_KEY>`}</Code>
        </Section>
      </div>
      <div className="flex flex-col gap-5">
        <Section title="Tools">
          <ul className="flex flex-col gap-2">
            {overview?.tools.map((t) => (
              <li key={t.name} className="rounded-lg border border-line bg-panel p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <code className="text-xs font-semibold">{t.name}</code>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${t.read_only ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>
                    {t.read_only ? "read" : "write"}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">{t.description}</p>
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Resources & prompts">
          <div className="flex flex-wrap gap-1.5">
            {[...(overview?.resources ?? []), ...(overview?.prompts ?? [])].map((r) => (
              <code key={r} className="rounded bg-subtle px-1.5 py-0.5 text-xs">
                {r}
              </code>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-sm font-semibold">{title}</h2>
      {hint && <p className="mb-2 text-xs text-muted">{hint}</p>}
      <div className={hint ? "" : "mt-2"}>{children}</div>
    </section>
  );
}

function Keys({ mcpUrl }: { mcpUrl: string }) {
  const [keys, setKeys] = useState<Key[]>([]);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<Key["scope"]>("read");
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api<Key[]>("/api/keys").then(setKeys).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const res = await postJson<{ key: string }>("/api/keys", { name, scope });
      setCreated(res.key);
      setName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function revoke(id: number) {
    await api(`/api/keys/${id}`, { method: "DELETE" });
    load();
  }

  return (
    <div className="flex flex-col gap-5">
      <form onSubmit={create} className="flex flex-wrap items-end gap-3 rounded-xl border border-line bg-panel p-4">
        <label className="flex flex-col gap-1 text-xs text-muted">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. claude-desktop-laptop"
            required
            maxLength={60}
            className="w-64 rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Scope
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as Key["scope"])}
            className="rounded-md border border-line bg-bg px-2.5 py-1.5 text-sm text-fg"
          >
            <option value="read">read (read tools only)</option>
            <option value="read_write">read_write (+ guarded writes)</option>
          </select>
        </label>
        <button className="rounded-md bg-accent px-3.5 py-1.5 text-sm font-medium text-white">Create key</button>
        {error && <span className="text-sm text-danger">{error}</span>}
      </form>

      {created && (
        <div className="rounded-xl border border-accent/40 bg-accent-soft p-4">
          <p className="mb-2 text-sm font-medium">Copy this key now. It is shown only once; only its hash is stored.</p>
          <Code>{created}</Code>
          <p className="mb-2 mt-3 text-xs text-muted">Claude Code:</p>
          <Code>{snippets(mcpUrl, created).claudeCode}</Code>
          <button onClick={() => setCreated(null)} className="mt-3 text-xs text-muted hover:text-fg">
            Done
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-line bg-panel">
        <table className="w-full text-sm">
          <thead className="bg-subtle text-left text-xs text-muted">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Key</th>
              <th className="px-3 py-2">Scope</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2">Last used</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-muted">
                  No keys yet.
                </td>
              </tr>
            )}
            {keys.map((k) => (
              <tr key={k.id} className={`border-t border-line ${k.revoked_at ? "opacity-50" : ""}`}>
                <td className="px-3 py-2">{k.name}</td>
                <td className="px-3 py-2 font-mono text-xs">{k.prefix}…</td>
                <td className="px-3 py-2">
                  <span className={`rounded px-1.5 py-0.5 text-xs ${k.scope === "read" ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>{k.scope}</span>
                </td>
                <td className="px-3 py-2 text-xs text-muted">{fmt(k.created_at)}</td>
                <td className="px-3 py-2 text-xs text-muted">{k.last_used_at ? fmt(k.last_used_at) : "never"}</td>
                <td className="px-3 py-2 text-right">
                  {k.revoked_at ? (
                    <span className="text-xs text-muted">revoked</span>
                  ) : (
                    <button onClick={() => revoke(k.id)} className="rounded border border-line px-2 py-0.5 text-xs text-danger hover:bg-danger-soft">
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  ok: "bg-ok-soft text-ok",
  rejected: "bg-warn-soft text-warn",
  denied: "bg-danger-soft text-danger",
  error: "bg-danger-soft text-danger",
};

function Audit() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api<Call[]>("/api/audit?limit=200").then((c) => alive && setCalls(c)).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-panel">
      <div className="flex items-center justify-between border-b border-line px-3 py-2 text-xs text-muted">
        <span>Every tool call: who, what, how long, outcome. Refreshes every 3 s.</span>
        <span>{calls.length} most recent</span>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-subtle text-left text-xs text-muted">
          <tr>
            <th className="px-3 py-2">Time</th>
            <th className="px-3 py-2">Tool</th>
            <th className="px-3 py-2">Caller</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2 text-right">Duration</th>
          </tr>
        </thead>
        <tbody>
          {calls.length === 0 && (
            <tr>
              <td colSpan={5} className="px-3 py-6 text-center text-muted">
                No tool calls yet. Connect a client from the Connect tab.
              </td>
            </tr>
          )}
          {calls.map((c) => (
            <Fragment key={c.id}>
              <tr onClick={() => setOpen(open === c.id ? null : c.id)} className="cursor-pointer border-t border-line hover:bg-subtle">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{fmt(c.at)}</td>
                <td className="px-3 py-2 font-mono text-xs">{c.tool}</td>
                <td className="px-3 py-2 text-xs">
                  {c.principal} <span className="text-muted">· {c.transport}</span>
                </td>
                <td className="px-3 py-2">
                  <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[c.status] ?? ""}`}>{c.status}</span>
                </td>
                <td className="px-3 py-2 text-right text-xs text-muted">{c.duration_ms} ms</td>
              </tr>
              {open === c.id && (
                <tr className="bg-subtle">
                  <td colSpan={5} className="px-3 py-2">
                    <pre className="whitespace-pre-wrap font-mono text-xs">{JSON.stringify(c.arguments, null, 2)}</pre>
                    {c.error && <p className="mt-1 text-xs text-danger">{c.error}</p>}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
