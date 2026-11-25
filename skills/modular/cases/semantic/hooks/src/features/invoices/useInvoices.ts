import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Invoice } from "./types";

export function useInvoices(customerId: string) {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!customerId) return;
    let active = true;
    async function load() {
      setLoading(true);
      try {
        const res = await api.get<Invoice[]>(`/customers/${customerId}/invoices`);
        if (active) setInvoices(res.data);
      } catch (e) {
        if (active) setError(e);
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => {
      active = false;
    };
  }, [customerId]);

  return { invoices, loading, error };
}
