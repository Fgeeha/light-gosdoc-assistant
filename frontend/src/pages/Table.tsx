import { useState } from "react";
import { downloadCsv, useExamples, useStream } from "../api";
import { Button, ErrorNote, Field, PageHeader, Samples, Spinner } from "../ui";

type Row = Record<string, string>;

export default function Table() {
  const examples = useExamples();
  const [source, setSource] = useState("");
  const [columnsText, setColumnsText] = useState("");
  const [instructions, setInstructions] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [sourceCount, setSourceCount] = useState(0);
  const { run, running, error } = useStream();

  const targetColumns = columnsText.split("\n").map((c) => c.trim()).filter(Boolean);

  function transform(e: React.FormEvent) {
    e.preventDefault();
    setRows([]);
    setColumns(targetColumns);
    setProgress(null);
    run("/tools/table", { source, columns: targetColumns, instructions }, (ev, d) => {
      if (ev === "source") setSourceCount(d.rows.length);
      if (ev === "rows") setRows((r) => [...r, ...d]);
      if (ev === "progress") setProgress(d);
    });
  }

  return (
    <>
      <PageHeader
        title="Перенос таблиц"
        lead="Переносит записи из выгрузки в нужную форму: разбирает ФИО и адреса, приводит даты и телефоны к единому формату, классифицирует и считает."
      />
      <form onSubmit={transform}>
        <Samples
          items={examples?.tables}
          onPick={(x) => {
            setSource(x.source);
            setColumnsText(x.columns.join("\n"));
            setInstructions(x.instructions);
          }}
        />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <Field label="Исходная таблица" hint="Скопируйте ячейки из Excel или вставьте CSV. Первая строка — заголовки.">
            <textarea className="field min-h-[260px] resize-y whitespace-pre font-mono text-[13px] leading-relaxed" spellCheck={false} required value={source} onChange={(e) => setSource(e.target.value)} />
          </Field>
          <div className="space-y-4">
            <Field label="Колонки целевой формы" hint="По одной на строку">
              <textarea className="field min-h-[130px] resize-y" required value={columnsText} onChange={(e) => setColumnsText(e.target.value)} />
            </Field>
            <Field label="Правила заполнения">
              <textarea className="field min-h-[100px] resize-y" value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Например: дата в формате ДД.ММ.ГГГГ" />
            </Field>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button disabled={running || !source.trim() || !targetColumns.length}>
            {running && <Spinner />}
            {running ? "Переношу…" : "Перенести данные"}
          </Button>
        </div>
      </form>

      <section aria-live="polite" className="mt-8">
        {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}
        {(running || rows.length > 0) && (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-[17px] font-semibold">
                Результат: {rows.length} из {sourceCount || "…"} записей
              </h2>
              {!running && rows.length > 0 && (
                <Button variant="secondary" onClick={() => downloadCsv(columns, rows, "Результат переноса")}>
                  Скачать для Excel
                </Button>
              )}
            </div>
            {running && progress && progress.total > 1 && (
              <div className="mb-3 h-1 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={progress.done} aria-valuemax={progress.total}>
                <div className="h-full bg-seal transition-[width]" style={{ width: `${(progress.done / progress.total) * 100}%` }} />
              </div>
            )}
            <div className="overflow-x-auto rounded-sm border border-line bg-white">
              <table className="w-full border-collapse text-[14px]">
                <thead className="bg-desk/60 text-left">
                  <tr>
                    <th className="w-10 border-b border-line px-3 py-2 font-medium text-muted">№</th>
                    {columns.map((c) => (
                      <th key={c} className="whitespace-nowrap border-b border-line px-3 py-2 font-medium">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="odd:bg-white even:bg-desk/25">
                      <td className="border-b border-line/70 px-3 py-2 tabular-nums text-muted">{i + 1}</td>
                      {columns.map((c) => (
                        <td key={c} className={`border-b border-line/70 px-3 py-2 ${r[c] ? "" : "bg-amber/5"}`}>
                          {r[c] || <span className="text-muted/60">—</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {running && (
                    <tr>
                      <td colSpan={columns.length + 1} className="px-3 py-3 text-muted">
                        <span className="inline-flex items-center gap-2"><Spinner /> Обрабатываю записи…</span>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
