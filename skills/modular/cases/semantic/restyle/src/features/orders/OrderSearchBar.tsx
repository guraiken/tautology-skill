import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";

export function OrderSearchBar({ onSearch, onSubmit }: { onSearch: (q: string) => void; onSubmit: (q: string) => void }) {
  const [query, setQuery] = useState("");
  const debounce = useRef<number | null>(null);
  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => onSearch(query), 300);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query, onSearch]);
  return (
    <div className="flex h-10 items-center gap-2 rounded-lg bg-muted px-3 shadow-sm">
      <Search className="size-4 opacity-60" />
      <input
        type="search"
        value={query}
        placeholder="Search orders"
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSubmit(query)}
        className="flex-1 bg-transparent text-sm outline-none placeholder:text-zinc-400"
      />
      {query && (
        <button type="button" onClick={() => setQuery("")} className="rounded-full p-1 hover:bg-zinc-200">
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
