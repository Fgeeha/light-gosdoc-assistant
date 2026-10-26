import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { api, type User } from "./api";

type Status = { model: string; online: boolean; loaded?: boolean };

const NAV = [
  { to: "/check", label: "Проверка по нормативной базе" },
  { to: "/letter", label: "Ответ на обращение" },
  { to: "/table", label: "Перенос таблиц" },
];

function Mark() {
  return (
    <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
      <rect width="32" height="32" rx="6" fill="#fff" fillOpacity=".1" />
      <path d="M9 7h10l5 5v13H9z" fill="#fff" />
      <path d="M19 7v5h5" fill="#c9d3e6" />
      <path d="M12 17.5l3 3 6-7" stroke="#2A4FA8" strokeWidth="2.5" fill="none" />
    </svg>
  );
}

function ModelStatus() {
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => {
    const load = () => api<Status>("/tools/status").then(setS, () => setS(null));
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  const ok = s?.online && s.loaded !== false;
  return (
    <div className="rounded-md bg-white/6 px-3 py-2.5 text-[13px] leading-snug">
      <div className="flex items-center gap-2">
        <span className={`size-2 rounded-full ${ok ? "bg-emerald-400" : s ? "bg-amber-400" : "bg-white/30"}`} />
        <span className="font-medium text-white">{s?.model ?? "Модель"}</span>
      </div>
      <p className="mt-0.5 text-white/60">
        {!s ? "Проверка связи…" : !s.online ? "Сервер модели недоступен" : s.loaded === false ? "Модель не найдена на сервере" : "Работает на сервере организации"}
      </p>
    </div>
  );
}

export default function Layout({ user, onLogout }: { user: User; onLogout: () => void }) {
  const items = user.is_admin
    ? [...NAV, { to: "/norms", label: "Справочник" }, { to: "/admin", label: "Пользователи" }]
    : NAV;
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[264px_1fr]">
      <aside className="bg-ink text-white lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col">
        <div className="flex items-center gap-3 px-5 py-4 lg:py-6">
          <Mark />
          <div className="leading-tight">
            <div className="text-[17px] font-semibold">ГосДок</div>
            <div className="text-[13px] text-white/60">Ассистент делопроизводства</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto [scrollbar-width:none] px-3 pb-3 lg:flex-col lg:overflow-visible lg:pb-0">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              className={({ isActive }) =>
                `shrink-0 rounded-md px-3 py-2 text-[14px] transition-colors ${
                  isActive ? "bg-white text-ink font-medium" : "text-white/80 hover:bg-white/8 hover:text-white"
                }`
              }
            >
              {it.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden space-y-3 px-3 pb-5 lg:block">
          <ModelStatus />
          <div className="flex items-center justify-between gap-2 px-2 text-[13px]">
            <span className="truncate text-white/80" title={user.username}>
              {user.full_name || user.username}
            </span>
            <button onClick={onLogout} className="shrink-0 text-white/60 hover:text-white">
              Выйти
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 sm:px-8 lg:px-10 lg:py-9">
        <div className="mx-auto max-w-[1360px]">
          <Outlet />
        </div>
        <div className="mt-10 flex justify-end lg:hidden">
          <button onClick={onLogout} className="text-sm text-muted">
            Выйти ({user.username})
          </button>
        </div>
      </main>
    </div>
  );
}
