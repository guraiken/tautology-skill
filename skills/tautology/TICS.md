# Replacing AI visual tics

Read when the hook reports a tic. Don't swap one default look for another: deleting the dot and adding a
gradient is still the same tic. Rebuild the element from what this screen already uses: the hook lists those
tokens and components. Use the project's tokens and never hardcode a color, size or radius. The hook checks the lines you change.

Before writing, ask what the element tells the user and whether the screen already says it. Then use the
quietest way to say it. Pick from the options below, or find a better one in the app's own components.

| Tic | Instead |
|---|---|
| Status dot + pill ("• Online") | Colour only the status word with the semantic token (success, muted); or put the state inside the control it describes (a disabled switch, a filled icon) |
| Badge on every row or action ("New", "Beta", "AI") | Nothing; if something really is new, sort it first or add one line to the release notes. Keep a chip only when the user acts on it (a filter they can remove, a count that needs a click) |
| Gradient, glow, pulse, CAPS + tracking | The theme's plain surface and text tokens. Show emphasis through weight, size or spacing, not decoration |
| Emoji in headings and buttons | The project's icon component, only where the icon carries meaning; otherwise only the text |
| Tooltip or ⓘ on every field | A label precise enough to need none. Keep the tooltip for the rule the label can't hold ("0 disables"). A long explanation goes in a disclosure the user opens |
| Helper text under every field | The placeholder shows the format, an error explains a wrong value, and help appears only for the field that needs it |
| Footnote under a card or page ("Changes are saved automatically") | Cut it. Put the information where it applies: a disabled control with its reason, a toast once the thing happens, or a saved state on the control itself |
| Card inside card, dividers between every row | One surface. Group with spacing and a section heading; one divider at most where two unrelated groups meet |
| Stacked filter bars (exposed selects + permanent strip of status pills) and fixed-width triggers | One compact `Filters` menu (with sections for status/type/sort and an active count) sized to its label; show removable chips only while a filter is active |
| Multiple solid primary accents on one toolbar (primary CTA + primary active filter + primary toggle) | Reserve solid primary fill for the single main CTA; give selected filters and view toggles a neutral surface/selected token |

A fix is done when the screen is quieter and uses only the project's tokens and components.
