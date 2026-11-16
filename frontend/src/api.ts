import { useEffect, useRef, useState } from "react";
import { createSseParser, csvCell } from "./format";

export type User = {
  id: number;
  username: string;
  full_name: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
};

export type Finding = {
  norm_id: string | null;
  status: "active" | "repealed" | "partially" | "unknown";
  title: string;
  quote: string;
  occurrences: number;
  since?: string | null;
  replaced_by?: string | null;
  comment?: string | null;
  facts: string[];
};

export type Norm = {
  id: string;
  title: string;
  status: "active" | "repealed" | "partially";
  since?: string | null;
  replaced_by?: string | null;
  facts_count: number;
  verified: boolean;
  updated_by?: string | null;
  updated_at?: string | null;
};

export type NormUpdate = { norm_id: string; date: string; name: string; url: string };

type Example = { id: string; title: string; description: string };
export type Examples = {
  check: (Example & { text: string; expected: string })[];
  letters: (Example & { incoming: string; organization: string; signer: string; instructions: string })[];
  tables: (Example & { source: string; columns: string[]; instructions: string })[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function errorFrom(r: Response): Promise<ApiError> {
  if (r.status === 401) window.dispatchEvent(new Event("gosdoc:unauthorized"));
  let message = `Ошибка ${r.status}`;
  try {
    const { detail } = await r.json();
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail)) message = detail.map((d) => d.msg).join("; ");
  } catch {
    /* тело не JSON */
  }
  return new ApiError(r.status, message);
}

type RequestOptions = RequestInit & { json?: unknown };

/** Единая точка запросов к API: JSON-тело и разбор ошибок. */
async function request(path: string, { json, ...init }: RequestOptions = {}): Promise<Response> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: json === undefined ? init.headers : { "Content-Type": "application/json" },
    body: json === undefined ? init.body : JSON.stringify(json),
  });
  if (!r.ok) throw await errorFrom(r);
  return r;
}

export async function api<T>(path: string, init: RequestOptions = {}): Promise<T> {
  const r = await request(path, init);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** POST с ответом text/event-stream. */
async function stream(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal: AbortSignal,
) {
  const r = await request(path, { method: "POST", json: body, signal });
  if (!r.body) throw new ApiError(r.status, "Пустой ответ сервера");
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  const parse = createSseParser();
  // Сервер всегда завершает поток событием done; без него соединение оборвалось на полпути.
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const { event, data } of parse(value)) {
      if (event === "done") finished = true;
      onEvent(event, data);
    }
  }
  if (!finished) throw new ApiError(0, "Соединение прервано — результат неполный, повторите запрос");
}

export function useStream() {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  useEffect(() => () => ctrl.current?.abort(), []);

  async function run(path: string, body: unknown, on: (event: string, data: any) => void) {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setRunning(true);
    setError(null);
    try {
      await stream(path, body, (e, d) => (e === "error" ? setError(d.message) : on(e, d)), c.signal);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setError((e as Error).message);
    } finally {
      if (ctrl.current === c) setRunning(false);
    }
  }

  return { run, running, error, stop: () => ctrl.current?.abort() };
}

let examplesPromise: Promise<Examples> | null = null;

export function useExamples(): Examples | null {
  const [examples, setExamples] = useState<Examples | null>(null);
  useEffect(() => {
    examplesPromise ??= api<Examples>("/tools/examples");
    examplesPromise.then(setExamples, () => (examplesPromise = null));
  }, []);
  return examples;
}

function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export async function downloadDocx(text: string, filename: string) {
  const r = await request("/tools/docx", { method: "POST", json: { text, filename } });
  saveBlob(await r.blob(), `${filename}.docx`);
}

export function downloadCsv(columns: string[], rows: Record<string, string>[], filename: string) {
  const lines = [columns, ...rows.map((r) => columns.map((c) => r[c] ?? ""))].map((l) =>
    l.map(csvCell).join(";"),
  );
  // BOM и «;» — чтобы Excel с русской локалью открыл файл без мастера импорта.
  saveBlob(new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv" }), `${filename}.csv`);
}

export async function extractFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const { text } = await api<{ text: string }>("/tools/extract", { method: "POST", body: form });
  return text;
}
