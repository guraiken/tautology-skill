test.each([
  ["10", 10],
  ["20", 20],
])("parses %s", (raw, expected) => {
  expect(parseInt(raw, 10)).toBe(expected);
});

it("checks array non-empty before iterating", () => {
  const items = getItems();
  expect(items.length).toBe(2);
  items.forEach((item) => {
    expect(item.valid).toBe(true);
  });
});

it("throws on invalid input", () => {
  expect(() => parseStrict("bad")).toThrow("invalid");
});
