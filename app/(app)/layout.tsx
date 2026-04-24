import { MobileNav } from "@/components/shared/mobile-nav";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <main className="flex-1 px-4 pb-24 pt-6">{children}</main>
      <MobileNav />
    </div>
  );
}
