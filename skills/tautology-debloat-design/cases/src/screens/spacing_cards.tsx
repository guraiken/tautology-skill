export function SpacingCards() {
  return (
    <section className="flex flex-col gap-4">
      <div className="pt-8 pb-2 px-4">Lopsided</div>
      <div className="pt-2 pb-2 md:pt-8 md:pb-8">Even at every size</div>
      <div className="pb-2 md:pb-8 md:hover:pt-8">Even under the stacked variant</div>
      <h2 className="text-xl pt-8 pb-2">Heading with room above</h2>
      <div title="note" className="pt-8 pb-2">A title attribute is not a heading</div>
      <h3 className="pt-8 pb-2">Section</h3><p className="pt-2 pb-8">Body on the same line</p>
      <div className="p-[26px]">Off the scale</div>
    </section>
  );
}
