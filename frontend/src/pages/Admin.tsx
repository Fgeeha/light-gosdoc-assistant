import { useEffect, useRef, useState } from "react";
import { api, type User } from "../api";
import { Button, ErrorNote, Field, PageHeader } from "../ui";

const NEW = { username: "", full_name: "", password: "", is_admin: false };

export default function Admin({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(NEW);
  const [formError, setFormError] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  const load = () => api<User[]>("/admin/users").then(setUsers, (e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      await api("/admin/users", { method: "POST", json: form });
      dialog.current?.close();
      setForm(NEW);
      load();
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  const patch = (u: User, json: object) => act(() => api(`/admin/users/${u.id}`, { method: "PATCH", json }));

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <PageHeader title="Пользователи" lead="Учётные записи сотрудников, которым открыт доступ к ассистенту." />
        <Button onClick={() => dialog.current?.showModal()}>Добавить пользователя</Button>
      </div>
      {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}

      <div className="overflow-x-auto rounded-sm border border-line bg-white">
        <table className="w-full border-collapse text-[14px]">
          <thead className="bg-desk/60 text-left">
            <tr>
              {["Сотрудник", "Логин", "Роль", "Доступ", "Создан", ""].map((h, i) => (
                <th key={i} className="whitespace-nowrap border-b border-line px-4 py-2.5 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "text-muted"}>
                <td className="border-b border-line/70 px-4 py-3">{u.full_name || "—"}</td>
                <td className="border-b border-line/70 px-4 py-3 font-mono text-[13px]">{u.username}</td>
                <td className="border-b border-line/70 px-4 py-3">{u.is_admin ? "Администратор" : "Сотрудник"}</td>
                <td className="border-b border-line/70 px-4 py-3">
                  <span className={u.is_active ? "text-ins" : "text-del"}>{u.is_active ? "Открыт" : "Заблокирован"}</span>
                </td>
                <td className="whitespace-nowrap border-b border-line/70 px-4 py-3 tabular-nums">
                  {new Date(u.created_at).toLocaleDateString("ru-RU")}
                </td>
                <td className="whitespace-nowrap border-b border-line/70 px-2 py-1.5 text-right">
                  <Button
                    variant="quiet"
                    onClick={() => {
                      const password = prompt(`Новый пароль для ${u.username} (не короче 8 символов)`);
                      if (password) patch(u, { password });
                    }}
                  >
                    Сменить пароль
                  </Button>
                  {u.id !== me.id && (
                    <>
                      <Button variant="quiet" onClick={() => patch(u, { is_active: !u.is_active })}>
                        {u.is_active ? "Заблокировать" : "Разблокировать"}
                      </Button>
                      <Button variant="quiet" onClick={() => patch(u, { is_admin: !u.is_admin })}>
                        {u.is_admin ? "Снять права" : "Сделать админом"}
                      </Button>
                      <Button
                        variant="quiet"
                        className="text-del!"
                        onClick={() => confirm(`Удалить пользователя ${u.username}?`) && act(() => api(`/admin/users/${u.id}`, { method: "DELETE" }))}
                      >
                        Удалить
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <dialog ref={dialog} className="m-auto w-[min(440px,calc(100vw-32px))] rounded-md p-0 shadow-2xl backdrop:bg-ink/40">
        <form onSubmit={create} className="space-y-4 p-6">
          <h2 className="text-[19px] font-semibold">Новый пользователь</h2>
          <Field label="ФИО">
            <input className="field" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} placeholder="Иванов Иван Иванович" />
          </Field>
          <Field label="Логин" hint="Латиница, цифры, точка, дефис">
            <input className="field" required pattern="[A-Za-z0-9._\-]{3,64}" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </Field>
          <Field label="Пароль" hint="Не короче 8 символов">
            <input className="field" type="text" required minLength={8} maxLength={72} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-seal" checked={form.is_admin} onChange={(e) => setForm({ ...form, is_admin: e.target.checked })} />
            Права администратора
          </label>
          {formError && <ErrorNote>{formError}</ErrorNote>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => dialog.current?.close()}>Отмена</Button>
            <Button>Создать</Button>
          </div>
        </form>
      </dialog>
    </>
  );
}
