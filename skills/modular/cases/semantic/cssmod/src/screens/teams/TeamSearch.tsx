import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";
import styles from "./TeamSearch.module.css";

type TeamSearchProps = {
  onSearch: (q: string) => void;
  placeholder?: string;
};

export function TeamSearch({ onSearch, placeholder = "Search teams..." }: TeamSearchProps) {
  const [value, setValue] = useState("");

  useEffect(() => {
    const t = setTimeout(() => onSearch(value.trim()), 300);
    return () => clearTimeout(t);
  }, [value, onSearch]);

  return (
    <div className={styles.root}>
      <Search className={styles.icon} />
      <input
        type="search"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        className={styles.input}
      />
      {value && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => setValue("")}
          className={styles.clear}
        >
          <X className={styles.clearIcon} />
        </button>
      )}
    </div>
  );
}
