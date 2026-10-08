import { UserMenu } from "@/components/user-menu";
import { requireUser } from "@/lib/session";
import { Console } from "./console";

export default async function Home() {
  const user = await requireUser();
  const mcpUrl = process.env.MCP_PUBLIC_URL ?? "http://localhost:8001/mcp";
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between border-b border-line bg-panel px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent font-bold text-white">S</div>
          <div>
            <div className="font-semibold leading-tight">ShopOps MCP</div>
            <div className="text-xs text-muted">Kettle &amp; Crate store backend · {mcpUrl}</div>
          </div>
        </div>
        <UserMenu user={user} />
      </header>
      <Console mcpUrl={mcpUrl} />
    </div>
  );
}
