import "server-only";
import { redirect } from "next/navigation";
import { auth } from "@/auth";

/** Server-side guard for protected pages. */
export async function requireUser() {
  const session = await auth();
  if (!session?.user || session.expired) redirect("/login");
  return session.user;
}
