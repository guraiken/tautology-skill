import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";

export function UserSearchInput({ onSearch, placeholder }: { onSearch: (q: string) => void; placeholder?: string }) {
  const [value, setValue] = useState("");
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onSearch(value), 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, onSearch]);
  return (
    <div className="relative flex items-center w-full">
      <Search className="absolute left-3 h-4 w-4 text-muted-foreground" />
      <input
        type="search"
        value={value}
        placeholder={placeholder ?? "Search users"}
        onChange={(e) => setValue(e.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background pl-9 pr-8 text-sm"
      />
      {value && (
        <button type="button" onClick={() => setValue("")} className="absolute right-2 text-muted-foreground">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
