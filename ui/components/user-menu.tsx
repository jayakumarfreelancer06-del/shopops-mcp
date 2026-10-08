import { signOut } from "@/auth";

export function UserMenu({ user }: { user: { name?: string | null; email?: string | null; image?: string | null } }) {
  return (
    <div className="flex items-center gap-3">
      {user.image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={user.image} alt="" className="h-7 w-7 rounded-full" referrerPolicy="no-referrer" />
      ) : null}
      <span className="hidden text-sm text-muted sm:inline">{user.email}</span>
      <form
        action={async () => {
          "use server";
          await signOut({ redirectTo: "/login" });
        }}
      >
        <button className="rounded-md border border-line px-2.5 py-1 text-xs text-muted hover:bg-subtle">Sign out</button>
      </form>
    </div>
  );
}
