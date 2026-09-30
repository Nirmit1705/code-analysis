import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Upper-cases only the first character — for sentence-like text that comes
 * from data (backend notes, relative times) and so can't be fixed in the
 * markup. Deliberately not applied to identifiers (file paths, branch names). */
export function capitalizeFirst(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
