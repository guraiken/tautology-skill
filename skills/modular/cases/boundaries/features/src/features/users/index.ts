import type { listOrders } from "../orders";
export type User = { id: string; orders?: ReturnType<typeof listOrders> };
