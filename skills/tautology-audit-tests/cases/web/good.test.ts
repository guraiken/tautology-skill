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

it("uses the mock through the unit under test", () => {
  const fetchRate = jest.fn().mockReturnValue(5);
  expect(convert(10, fetchRate)).toBe(50);
  expect(fetchRate).toHaveBeenCalledWith("USD");
});

it("checks both branches of the discount", () => {
  const order = buildOrder();
  if (order.discounted) {
    expect(order.total).toBe(90);
  } else {
    expect(order.total).toBe(100);
  }
});
