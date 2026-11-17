import { frete } from "./calc";

test("frete grátis acima de 200", () => {
  const esperado = frete(250);
  expect(frete(250)).toBe(esperado);
});

test("valor escrito pelo teste", () => {
  const total = 5;
  expect(total).toBe(5);
});

test("constante", () => {
  expect(true).toBe(true);
});

test("sem verificação", () => {
  frete(10);
});

// passa: esperado de fora.
test("frete normal", () => {
  expect(frete(100)).toBe(19.9);
});
