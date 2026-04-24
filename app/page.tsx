import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-6 px-4 py-10 text-center">
      <div className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">KitchenOS</h1>
        <p className="text-muted-foreground text-balance">
          Calendar, meals, and inventory — synced for your whole household.
        </p>
      </div>
      <div className="flex w-full flex-col gap-2">
        <Link
          href="/login"
          className="bg-primary text-primary-foreground inline-flex h-11 items-center justify-center rounded-md px-6 text-sm font-medium"
        >
          Sign in
        </Link>
        <Link
          href="/signup"
          className="border-input inline-flex h-11 items-center justify-center rounded-md border px-6 text-sm font-medium"
        >
          Create household
        </Link>
      </div>
    </main>
  );
}
