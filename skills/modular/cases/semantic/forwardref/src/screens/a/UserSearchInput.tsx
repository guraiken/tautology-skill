import * as React from "react";
import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface UserSearchInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  onClear?: () => void;
}

export const UserSearchInput = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & { onClear?: () => void }>(
  ({ className, value, onClear, ...props }, ref) => {
    return (
      <div className="relative flex w-full items-center">
        <Search className="absolute left-3 h-4 w-4 text-muted-foreground" />
        <input
          ref={ref}
          type="search"
          value={value}
          className={cn(
            "h-9 w-full rounded-md border border-input bg-background pl-9 pr-8 text-sm shadow-sm",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            className,
          )}
          {...props}
        />
        {value ? (
          <button type="button" aria-label="Clear" onClick={onClear} className="absolute right-2 text-muted-foreground">
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    );
  },
);
UserSearchInput.displayName = "UserSearchInput";
