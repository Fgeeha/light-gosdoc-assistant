import { useState } from "react";
import { downloadDocx, useExamples, useStream } from "../api";
import { Button, CopyButton, ErrorNote, Field, PageHeader, Samples, Spinner, UploadButton } from "../ui";

const EMPTY = { incoming: "", organization: "", signer: "", instructions: "" };

export default function Letter() {
  const examples = useExamples();
  const [form, setForm] = useState(EMPTY);
  const [letter, setLetter] = useState("");
  const { run, running, error, stop } = useStream();
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  function generate(e: React.FormEvent) {
    e.preventDefault();
    setLetter("");
    run("/tools/letter", form, (ev, d) => ev === "delta" && setLetter((l) => l + d));
  }

  return (
    <>
      <PageHeader
        title="Ответ на обращение"
        lead="Готовит проект ответа по существу каждого вопроса с учётом 59-ФЗ и правил делопроизводства. Недостающие сведения помечаются для заполнения."
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <form onSubmit={generate} className="space-y-4">
          <Samples items={examples?.letters} onPick={({ incoming, organization, signer, instructions }) => setForm({ incoming, organization, signer, instructions })} />
          <Field label="Входящее обращение">
            <textarea className="field min-h-[240px] resize-y font-serif text-[15px] leading-relaxed" required value={form.incoming} onChange={set("incoming")} />
          </Field>
          <div className="-mt-1"><UploadButton onText={(incoming) => setForm({ ...form, incoming })} /></div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="От имени органа">
              <input className="field" value={form.organization} onChange={set("organization")} placeholder="Администрация …" />
            </Field>
            <Field label="Подписант">
              <input className="field" value={form.signer} onChange={set("signer")} placeholder="Должность, И. О. Фамилия" />
            </Field>
          </div>
          <Field label="Позиция по существу" hint="Что ответить заявителю: принятые решения, сроки, куда обратиться. Модель не будет придумывать факты сверх этого.">
            <textarea className="field min-h-[110px] resize-y" value={form.instructions} onChange={set("instructions")} />
          </Field>
          <div className="flex justify-end gap-2">
            {running && <Button type="button" variant="secondary" onClick={stop}>Остановить</Button>}
            <Button disabled={!form.incoming.trim() || running}>
              {running && <Spinner />}
              {running ? "Готовлю проект…" : "Подготовить ответ"}
            </Button>
          </div>
        </form>

        <section aria-live="polite" className="min-w-0">
          {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}
          <div className="mb-2 flex min-h-9 items-center justify-between">
            <h2 className="text-[17px] font-semibold">Проект ответа</h2>
            {!running && letter && (
              <div className="flex gap-1">
                <CopyButton text={letter} />
                <Button variant="quiet" onClick={() => downloadDocx(letter.trim(), "Проект ответа")}>Скачать .docx</Button>
              </div>
            )}
          </div>
          {letter || running ? (
            <textarea
              aria-label="Текст проекта ответа"
              readOnly={running}
              className={`sheet block min-h-[640px] w-full resize-y px-12 py-10 focus:outline-none focus:ring-2 focus:ring-seal/25 ${running ? "cursor-progress" : ""}`}
              value={letter.trimStart()}
              onChange={(e) => setLetter(e.target.value)}
            />
          ) : (
            <div className="grid min-h-[300px] place-items-center rounded-sm border border-dashed border-line p-10 text-center text-[15px] text-muted">
              <p className="max-w-sm">Текст письма появится здесь. Его можно отредактировать прямо на листе и скачать в Word.</p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
