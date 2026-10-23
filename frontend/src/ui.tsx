import { useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { extractFile } from "./api";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" };

const VARIANTS = {
  primary: "bg-seal text-white hover:bg-[#23448f] disabled:bg-seal/50",
  secondary: "bg-white text-ink border border-line hover:border-ink/40 disabled:text-muted",
  quiet: "text-seal hover:bg-seal/8 disabled:text-muted",
};

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-ink">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function PageHeader({ title, lead }: { title: string; lead: string }) {
  return (
    <header className="mb-6 max-w-3xl">
      <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.01em]">{title}</h1>
      <p className="mt-1.5 text-[15px] text-muted">{lead}</p>
    </header>
  );
}

/** Образцы документов, подготовленные для демонстрации. */
export function Samples<T extends { id: string; title: string; description: string }>({
  items,
  onPick,
}: {
  items: T[] | undefined;
  onPick: (item: T) => void;
}) {
  const [active, setActive] = useState<string | null>(null);
  if (!items?.length) return null;
  return (
    <div className="mb-4">
      <p className="mb-2 text-sm text-muted">Подставить образец:</p>
      <div className="flex flex-wrap gap-2">
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            title={it.description}
            onClick={() => {
              setActive(it.id);
              onPick(it);
            }}
            className={`rounded-full border px-3 py-1 text-[13px] transition-colors ${
              active === it.id
                ? "border-seal bg-seal/8 text-seal"
                : "border-line bg-white text-ink hover:border-ink/40"
            }`}
          >
            {it.title}
          </button>
        ))}
      </div>
    </div>
  );
}

export function UploadButton({ onText }: { onText: (text: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center gap-3">
      <Button variant="secondary" type="button" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? "Читаю файл…" : "Загрузить файл"}
      </Button>
      <input
        ref={input}
        type="file"
        accept=".docx,.pdf,.txt"
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          setBusy(true);
          setError(null);
          try {
            onText(await extractFile(file));
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      />
      {error && <span className="text-sm text-del">{error}</span>}
    </span>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-md border border-del/30 bg-del/5 px-4 py-3 text-sm text-del">
      {children}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent motion-reduce:animate-none ${className}`}
    />
  );
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      variant="quiet"
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? "Скопировано" : "Копировать"}
    </Button>
  );
}
