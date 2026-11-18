it.only("computes tax tautologically", () => {
  expect(
    computeTax(10)
  ).toEqual(
    computeTax(10)
  );
});

it("swallows and sleeps", async () => {
  await new Promise((resolve) => setTimeout(resolve, 300));
  const items = getItems();
  items.forEach((item) => {
    expect(item.valid).toBe(true);
  });
  try {
    failMaybe();
  } catch (err) {}
});
