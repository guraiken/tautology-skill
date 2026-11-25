// shared formatting
import { pageTitle } from "../pages/Home";
import type { Page } from "@/pages/types";
export const fmt = (s: string, p?: Page) => s + pageTitle + (p ? p.id : "");
