import { NavLink, Outlet } from "react-router-dom";
import { Activity, FolderGit2, KeyRound } from "lucide-react";
import { cn } from "@/lib/cn";

const NAV = [
  { to: "/", label: "Projects", icon: FolderGit2, end: true },
  { to: "/secrets", label: "Secrets", icon: KeyRound, end: false },
];

export function Layout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-6">
          <NavLink to="/" className="flex items-center gap-2 font-semibold">
            <Activity className="h-5 w-5" /> agentprdiff Studio
          </NavLink>
          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm",
                    isActive ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/60",
                  )
                }
              >
                <Icon className="h-4 w-4" /> {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
