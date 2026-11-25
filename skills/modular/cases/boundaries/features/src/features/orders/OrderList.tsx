import { calc } from "../billing/internal/calc";
import { Invoice } from "@/features/billing";
import type { User } from "../users";
export const listOrders = (u?: User) => [calc(1), Invoice, u];
