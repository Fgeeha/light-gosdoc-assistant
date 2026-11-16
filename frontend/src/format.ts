// Чистые функции без React и DOM — их проверяют тесты в frontend/tests (node --test).

export type SseEvent = { event: string; data: any };

/** Разбор text/event-stream по кускам: возвращает готовые события, неполный хвост копит. */
export function createSseParser() {
  let buf = "";
  return (chunk: string): SseEvent[] => {
    buf += chunk;
    const out: SseEvent[] = [];
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      out.push({ event, data: data ? JSON.parse(data) : null });
    }
    return out;
  };
}

/** Ячейка CSV для Excel: кавычки по необходимости и защита от исполнения формул. */
export function csvCell(raw: string): string {
  // «=…», «+…», «-…», «@…» Excel исполняет как формулу (CSV-инъекция); числа и телефоны не трогаем.
  const formula = /^[=+\-@\t\r]/.test(raw) && !/^[+-]?[\d\s().,-]+$/.test(raw);
  const v = formula ? `'${raw}` : raw;
  return /[";\r\n]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v;
}
