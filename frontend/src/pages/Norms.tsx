import { useEffect, useState } from "react";
import { api, type Norm, type NormUpdate, type SyncReport } from "../api";
import { Button, ErrorNote, PageHeader, Spinner } from "../ui";

const STATUS: Record<Norm["status"], { label: string; cls: string }> = {
  active: { label: "Действует", cls: "bg-[#1E6B47]/10 text-[#1E6B47]" },
  repealed: { label: "Утратил силу", cls: "bg-[#A8261B]/10 text-[#A8261B]" },
  partially: { label: "Ограниченно", cls: "bg-[#8A5B00]/10 text-[#8A5B00]" },
};

export default function Norms() {
  const [norms, setNorms] = useState<Norm[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updates, setUpdates] = useState<NormUpdate[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, Norm["status"]>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [report, setReport] = useState<SyncReport | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = () => api<Norm[]>("/admin/norms").then(setNorms, (e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  async function syncStatuses() {
    setSyncing(true);
    setCheckError(null);
    setReport(null);
    try {
      setReport(await api<SyncReport>("/admin/norms/sync", { method: "POST" }));
      await load();
    } catch (e) {
      setCheckError((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function save(n: Norm) {
    setSaving(n.id);
    setError(null);
    try {
      const updated = await api<Norm>(`/admin/norms/${n.id}`, {
        method: "PATCH",
        json: { status: draft[n.id] ?? n.status },
      });
      setNorms((list) => list.map((x) => (x.id === n.id ? updated : x)));
      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([k]) => k !== n.id)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(null);
    }
  }

  async function check() {
    setChecking(true);
    setCheckError(null);
    setUpdates(null);
    try {
      setUpdates(await api<NormUpdate[]>("/admin/norms/updates?days=90"));
    } catch (e) {
      setCheckError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  const byNorm = (id: string) => norms.find((n) => n.id === id)?.title ?? id;

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Справочник актов"
          lead="По этой базе проверка находит ссылки на акты. Сверка статусов берёт их из ИПС pravo.gov.ru и каталога ГОСТ и сразу вносит в справочник с пометкой «не проверено»; сохранение записи здесь подтверждает её. Новые публикации по актам — по порталу опубликования."
        />
        <div className="flex flex-wrap gap-2">
          {/* Рабочий справочник — в БД; выгрузка нужна для git, скриптов сверки и резервной копии. */}
          <a
            href="/api/admin/norms/export"
            download="norms.json"
            className="inline-flex items-center rounded-md border border-line bg-white px-4 py-2 text-sm font-medium text-ink hover:border-ink/40"
          >
            Выгрузить norms.json
          </a>
          <Button onClick={syncStatuses} disabled={syncing}>
            {syncing ? <Spinner /> : null}
            {syncing ? "Сверка, 1–3 минуты…" : "Сверить статусы"}
          </Button>
          <Button variant="secondary" onClick={check} disabled={checking}>
            {checking ? <Spinner /> : null}
            Проверить публикации
          </Button>
        </div>
      </div>
      {error && (
        <div className="mb-4">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {checkError && (
        <div className="mb-4">
          <ErrorNote>{checkError}</ErrorNote>
        </div>
      )}

      {report !== null && (
        <section className="mb-6 rounded-sm border border-line bg-white p-4">
          <h2 className="mb-1 text-[15px] font-semibold">
            Сверено актов: {report.checked}, изменено: {report.changed.length}
          </h2>
          {report.changed.length === 0 ? (
            <p className="text-[14px] text-muted">Статусы совпадают с источниками.</p>
          ) : (
            <ul className="mb-2 space-y-2">
              {report.changed.map((r) => (
                <li key={r.norm_id} className="border-l-2 border-seal/40 pl-3 text-[14px]">
                  <div className="text-[12px] font-medium text-seal">{byNorm(r.norm_id)}</div>
                  <div>{r.note}</div>
                </li>
              ))}
            </ul>
          )}
          {report.unchecked.length > 0 && (
            <p className="text-[13px] text-muted">
              Не сверено (нет в источниках или сайт не ответил): {report.unchecked.length} —{" "}
              {report.unchecked.map(byNorm).join("; ")}
            </p>
          )}
        </section>
      )}

      {updates !== null && (
        <section className="mb-6 rounded-sm border border-line bg-white p-4">
          <h2 className="mb-1 text-[15px] font-semibold">
            Публикации за 90 дней по отслеживаемым актам: {updates.length}
          </h2>
          {updates.length === 0 ? (
            <p className="text-[14px] text-muted">
              Новых изменений по актам справочника на портале не найдено.
            </p>
          ) : (
            <>
              <p className="mb-3 text-[13px] text-muted">
                Это кандидаты на проверку: сверьте статус и нормы акта вручную, затем обновите
                справочник (docs/NORMS.md). Номера законов повторяются по годам — проверьте, тот ли
                это акт.
              </p>
              <ul className="space-y-3">
                {updates.map((u, i) => (
                  <li key={i} className="border-l-2 border-seal/40 pl-3 text-[14px]">
                    <div className="text-[12px] font-medium text-seal">{byNorm(u.norm_id)}</div>
                    <div>
                      {u.date} — {u.name}
                    </div>
                    <a
                      href={u.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[13px] text-seal underline underline-offset-2"
                    >
                      Открыть на pravo.gov.ru
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <div className="overflow-x-auto rounded-sm border border-line bg-white">
        <table className="w-full border-collapse text-[14px]">
          <thead className="bg-desk/60 text-left">
            <tr>
              {["Акт", "Статус", "С даты", "Замена", "Нормы", ""].map((h, i) => (
                <th key={i} className="whitespace-nowrap border-b border-line px-4 py-2.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {norms.map((n) => {
              const value = draft[n.id] ?? n.status;
              const dirty = value !== n.status;
              return (
                <tr key={n.id} className="align-top">
                  <td className="border-b border-line px-4 py-2.5">
                    {n.title}
                    {!n.verified && (
                      <span className="ml-2 whitespace-nowrap rounded-full bg-[#8A5B00]/10 px-2 py-0.5 text-[11px] text-[#8A5B00]">
                        не проверено
                      </span>
                    )}
                    {n.updated_by && (
                      <div className="mt-0.5 text-[12px] text-muted">
                        Изменено: {n.updated_by}
                        {n.updated_at && `, ${new Date(n.updated_at).toLocaleString("ru-RU")}`}
                      </div>
                    )}
                    {n.ips_nd && (
                      <a
                        href={`http://pravo.gov.ru/proxy/ips/?docbody=&nd=${n.ips_nd}`}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-2 whitespace-nowrap text-[12px] text-seal underline underline-offset-2"
                      >
                        текст в ИПС
                      </a>
                    )}
                    {n.sync_note && (
                      <div className="mt-1 text-[12px] text-[#8A5B00]">Автосверка: {n.sync_note}</div>
                    )}
                  </td>
                  <td className="border-b border-line px-4 py-2.5">
                    <select
                      value={value}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, [n.id]: e.target.value as Norm["status"] }))
                      }
                      className={`rounded-md border border-line px-2 py-1 text-[13px] ${STATUS[value].cls}`}
                    >
                      {(Object.keys(STATUS) as Norm["status"][]).map((s) => (
                        <option key={s} value={s} className="bg-white text-ink">
                          {STATUS[s].label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="whitespace-nowrap border-b border-line px-4 py-2.5 text-muted">{n.since ?? "—"}</td>
                  <td className="border-b border-line px-4 py-2.5 text-muted">{n.replaced_by ?? "—"}</td>
                  <td className="border-b border-line px-4 py-2.5 text-center text-muted">{n.facts_count || "—"}</td>
                  <td className="border-b border-line px-4 py-2.5">
                    {(dirty || !n.verified) && (
                      <Button variant="secondary" onClick={() => save(n)} disabled={saving === n.id}>
                        {saving === n.id ? <Spinner /> : dirty ? "Сохранить" : "Подтвердить"}
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
