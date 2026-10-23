import { useState } from "react";
import { api, type User } from "../api";
import { Button, ErrorNote, Field } from "../ui";

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api<User>("/auth/login", { method: "POST", json: { username, password } }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden bg-ink p-14 text-white lg:flex lg:flex-col">
        <div className="text-[20px] font-semibold">ГосДок</div>
        <div className="mt-auto max-w-xl">
          <h1 className="font-serif text-[44px] leading-[1.15]">
            Проверка документов, ответы на обращения и перенос данных за минуты
          </h1>
          <p className="mt-6 max-w-md text-[16px] leading-relaxed text-white/70">
            Языковая модель работает на сервере организации. Тексты документов и персональные данные
            не передаются во внешние сервисы.
          </p>
        </div>
        {/* Силуэт листа с правками — визуальный якорь продукта. */}
        <div aria-hidden className="pointer-events-none absolute -right-24 top-16 w-[420px] rotate-6 rounded-sm bg-white/[0.06] p-10">
          {[92, 78, 85, 40, 88, 70, 95, 60].map((w, i) => (
            <div key={i} className="mb-4 flex gap-2">
              <span className="h-2 rounded-full bg-white/15" style={{ width: `${w}%` }} />
              {i === 2 && <span className="h-2 w-16 rounded-full bg-[#7fbf9f]/50" />}
              {i === 5 && <span className="h-2 w-12 rounded-full bg-[#e08a80]/50" />}
            </div>
          ))}
        </div>
      </section>

      <section className="flex items-center justify-center px-4 py-12">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div>
            <h2 className="text-[24px] font-semibold">Вход в систему</h2>
            <p className="mt-1 text-[15px] text-muted">Учётную запись выдаёт администратор.</p>
          </div>
          <Field label="Логин">
            <input className="field" autoComplete="username" required autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label="Пароль">
            <input className="field" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && <ErrorNote>{error}</ErrorNote>}
          <Button className="w-full py-2.5" disabled={busy}>
            {busy ? "Вход…" : "Войти"}
          </Button>
        </form>
      </section>
    </div>
  );
}
