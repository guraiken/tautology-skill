import { Receipt } from "lucide-react";

export const InvoicesEmpty = () => (
  <div className="flex flex-col items-center justify-center p-10 text-center rounded-lg border border-dashed">
    <Receipt className="h-10 w-10 text-muted-foreground" />
    <h3 className="mt-4 text-lg font-semibold">No invoices</h3>
    <p className="mt-1 text-sm text-muted-foreground">Invoices appear here after your first billing cycle.</p>
  </div>
);
