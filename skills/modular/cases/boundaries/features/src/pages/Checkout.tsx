import { calc } from "@/features/billing/internal/calc";
const loadOrders = () => import("../features/orders");
export const Checkout = () => <div>{calc(2)}{String(loadOrders)}</div>;
