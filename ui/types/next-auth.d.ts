import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    expired?: boolean;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    backendToken?: string;
    backendTokenExpires?: number;
  }
}
