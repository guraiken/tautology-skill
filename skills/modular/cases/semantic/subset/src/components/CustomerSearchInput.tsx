import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";

type Suggestion = { id: string; label: string };

export function CustomerSearchInput({ onSearch, placeholder, suggestions, onPick }: { onSearch: (q: string) => void; placeholder?: string; suggestions: Suggestion[]; onPick: (s: Suggestion) => void }) {
  const [value, setValue] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onSearch(value), 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, onSearch]);
  useEffect(() => {
    setActive(0);
    setOpen(value.length > 0 && suggestions.length > 0);
  }, [value, suggestions]);
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      onPick(suggestions[active]);
      setOpen(false);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };
  return (
    <div className="relative flex items-center w-full">
      <Search className="absolute left-3 h-4 w-4 text-muted-foreground" />
      <input
        type="search"
        value={value}
        placeholder={placeholder ?? "Search customers"}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        className="h-9 w-full rounded-md border border-input bg-background pl-9 pr-8 text-sm"
      />
      {value && (
        <button type="button" onClick={() => setValue("")} className="absolute right-2 text-muted-foreground">
          <X className="h-4 w-4" />
        </button>
      )}
      {open && (
        <ul role="listbox" className="absolute top-10 z-10 w-full rounded-md border bg-popover p-1 shadow-md">
          {suggestions.map((s, i) => (
            <li
              key={s.id}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={() => onPick(s)}
              className={i === active ? "rounded-sm bg-accent px-2 py-1.5 text-sm" : "rounded-sm px-2 py-1.5 text-sm"}
            >
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
