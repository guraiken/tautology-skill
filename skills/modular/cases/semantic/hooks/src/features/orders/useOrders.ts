import { useEffect, useState } from "react";
import type { Order } from "./types";

export const useOrders = (status: string) => {
  const [orders, setOrders] = useState<Order[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [err, setErr] = useState<Error | null>(null);

  useEffect(() => {
    let ignore = false;
    setIsLoading(true);
    fetch(`/api/orders?status=${status}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((body) => {
        if (!ignore) setOrders(body.items);
      })
      .catch((e) => !ignore && setErr(e))
      .finally(() => !ignore && setIsLoading(false));
    return () => {
      ignore = true;
    };
  }, [status]);

  return { orders, isLoading, err };
};
