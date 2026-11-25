import { listOrders } from "../../orders";
export const Invoice = () => <ul>{listOrders().length}</ul>;
