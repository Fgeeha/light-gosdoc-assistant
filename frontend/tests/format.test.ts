import assert from "node:assert/strict";
import { test } from "node:test";
import { createSseParser, csvCell } from "../src/format.ts";

test("SSE: событие, разорванное между кусками, собирается целиком", () => {
  const parse = createSseParser();
  assert.deepEqual(parse('event: delta\ndata: "При'), []);
  assert.deepEqual(parse('вет"\n\nevent: done\ndata: {}\n\n'), [
    { event: "delta", data: "Привет" },
    { event: "done", data: {} },
  ]);
});

test("SSE: событие без data", () => {
  assert.deepEqual(createSseParser()("event: ping\n\n"), [{ event: "ping", data: null }]);
});

test("CSV: формулы экранируются, числа и телефоны — нет", () => {
  assert.equal(csvCell("=1+1"), "'=1+1");
  assert.equal(csvCell("@SUM(A1)"), "'@SUM(A1)");
  assert.equal(csvCell("-1+cmd|x"), "'-1+cmd|x");
  assert.equal(csvCell("+7 (912) 345-67-89"), "+7 (912) 345-67-89");
  assert.equal(csvCell("-5,5"), "-5,5");
  assert.equal(csvCell("Иванов"), "Иванов");
});

test("CSV: кавычки, разделитель и переводы строк", () => {
  assert.equal(csvCell('ООО "Ромашка"'), '"ООО ""Ромашка"""');
  assert.equal(csvCell("a;b"), '"a;b"');
  assert.equal(csvCell("a\rb"), '"a\rb"');
  assert.equal(csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
});
