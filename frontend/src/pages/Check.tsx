import { diffArrays } from "diff";
import { useMemo, useState } from "react";
import { downloadDocx, useExamples, useStream, type Finding } from "../api";
import { Button, CopyButton, ErrorNote, PageHeader, Samples, Spinner, UploadButton } from "../ui";

const STATUS: Record<Finding["status"], { label: (f: Finding) => string; tone: string }> = {
  repealed: { label: (f) => `Утратил силу${f.since ? ` с ${f.since}` : ""}`, tone: "border-del text-del" },
  partially: { label: (f) => `Применяется ограниченно${f.since ? ` с ${f.since}` : ""}`, tone: "border-amber text-amber" },
  active: { label: () => "Действует", tone: "border-ins text-ins" },
  unknown: { label: () => "Нет в справочной базе, проверьте вручную", tone: "border-line text-muted" },
};

function Note({ f }: { f: Finding }) {
  const s = STATUS[f.status];
  return (
    <li className={`border-l-[3px] bg-white py-3 pl-4 pr-3 ${s.tone.split(" ")[0]}`}>
      <p className={`text-[13px] font-medium ${s.tone.split(" ")[1]}`}>{s.label(f)}</p>
      <p className="mt-1 text-[14px] leading-snug text-ink">{f.title}</p>
      {f.replaced_by && (
        <p className="mt-2 text-[14px] leading-snug">
          <span className="text-muted">{f.status === "repealed" ? "Заменить на: " : "Ссылаться на: "}</span>
          {f.replaced_by}
        </p>
      )}
      {f.comment && <p className="mt-2 text-[13px] leading-snug text-muted">{f.comment}</p>}
      {f.facts.length > 0 && (
        <ul className="mt-2 space-y-1 text-[13px] leading-snug text-muted">
          {f.facts.map((x) => (
            <li key={x}>— {x}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Redline({ before, after }: { before: string; after: string }) {
  // diffWords считает кириллицу не-словом и дробит правки до букв — сравниваем токены сами.
  // Соседние правки, разделённые только пробелом, склеиваются: «старая фраза» → «новая фраза».
  const parts = useMemo(() => {
    const tokens = (s: string) => s.replace(/[ \t]+$/gm, "").split(/(\s+)/).filter(Boolean);
    const out: { kind: "same" | "del" | "ins"; value: string }[] = [];
    let del = "";
    let ins = "";
    const flush = () => {
      if (del) out.push({ kind: "del", value: del });
      if (ins) out.push({ kind: "ins", value: ins });
      del = ins = "";
    };
    const diff = diffArrays(tokens(before), tokens(after));
    diff.forEach((p, i) => {
      const value = p.value.join("");
      if (p.removed) del += value;
      else if (p.added) ins += value;
      else if (/^[ \t]+$/.test(value) && (del || ins) && (diff[i + 1]?.added || diff[i + 1]?.removed)) {
        del += value;
        ins += value;
      } else {
        flush();
        out.push({ kind: "same", value });
      }
    });
    flush();
    return out;
  }, [before, after]);
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "ins" ? (
          <ins key={i} className="edit">{p.value}</ins>
        ) : p.kind === "del" ? (
          <del key={i} className="edit">{p.value}</del>
        ) : (
          <span key={i}>{p.value}</span>
        ),
      )}
    </>
  );
}

export default function Check() {
  const examples = useExamples();
  const [text, setText] = useState("");
  const [checked, setChecked] = useState("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [result, setResult] = useState("");
  const [view, setView] = useState<"redline" | "clean">("redline");
  const { run, running, error } = useStream();

  const issues = findings?.filter((f) => f.status !== "active") ?? [];
  const actual = findings?.filter((f) => f.status === "active") ?? [];
  const corrected = result.trim();

  function check() {
    setChecked(text);
    setFindings(null);
    setResult("");
    run("/tools/check", { text }, (e, d) => {
      if (e === "findings") setFindings(d);
      if (e === "delta") setResult((r) => r + d);
    });
  }

  return (
    <>
      <PageHeader
        title="Проверка по нормативной базе"
        lead="Находит ссылки на отменённые и изменённые акты, объясняет, чем они заменены, и предлагает актуальную редакцию документа."
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <section>
          <Samples items={examples?.check} onPick={(x) => setText(x.text)} />
          <textarea
            aria-label="Текст документа"
            className="sheet block min-h-[520px] w-full resize-y p-8 focus:outline-none focus:ring-2 focus:ring-seal/25"
            placeholder="Вставьте текст положения, регламента, приказа или технического задания"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <UploadButton onText={setText} />
            <Button onClick={check} disabled={!text.trim() || running}>
              {running && <Spinner />}
              {running ? "Проверяю…" : "Проверить"}
            </Button>
          </div>
        </section>

        <section aria-live="polite" className="min-w-0">
          {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}
          {!findings && !running && (
            <div className="grid h-full min-h-[300px] place-items-center rounded-sm border border-dashed border-line p-10 text-center text-[15px] text-muted">
              <p className="max-w-sm">Вставьте документ или выберите образец слева, затем нажмите «Проверить». Замечания и правки появятся здесь.</p>
            </div>
          )}
          {running && !findings && (
            <p className="flex items-center gap-2 text-muted"><Spinner /> Сверяю ссылки со справочной базой…</p>
          )}
          {findings && (
            <>
              <h2 className="mb-3 text-[17px] font-semibold">
                {issues.length
                  ? `Требуют внимания: ${issues.length}`
                  : actual.length ? "Устаревших ссылок не найдено" : "Ссылок на нормативные акты не найдено"}
              </h2>
              <ul className="space-y-2">
                {[...issues, ...actual].map((f) => <Note key={f.norm_id ?? f.title} f={f} />)}
              </ul>

              {(running || corrected) && findings.some((f) => f.status !== "unknown") && (
                <div className="mt-6">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div role="tablist" className="inline-flex rounded-md border border-line bg-white p-0.5 text-[13px]">
                      {(["redline", "clean"] as const).map((v) => (
                        <button
                          key={v}
                          role="tab"
                          aria-selected={view === v}
                          onClick={() => setView(v)}
                          className={`rounded px-3 py-1 ${view === v ? "bg-ink text-white" : "text-ink hover:bg-desk"}`}
                        >
                          {v === "redline" ? "С правками" : "Чистовик"}
                        </button>
                      ))}
                    </div>
                    {!running && corrected && (
                      <div className="flex gap-1">
                        <CopyButton text={corrected} />
                        <Button variant="quiet" onClick={() => downloadDocx(corrected, "Актуализированный документ")}>
                          Скачать .docx
                        </Button>
                      </div>
                    )}
                  </div>
                  <div className="sheet whitespace-pre-wrap p-8">
                    {running || view === "clean" ? (
                      <span className={running ? "caret" : ""}>{result.trimStart()}</span>
                    ) : (
                      <Redline before={checked.trim()} after={corrected} />
                    )}
                  </div>
                  {!running && view === "redline" && (
                    <p className="mt-2 text-[13px] text-muted">
                      <del className="edit">Зачёркнуто</del> — удалено, <ins className="edit">выделено зелёным</ins> — добавлено моделью. Проверьте правки перед использованием.
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
