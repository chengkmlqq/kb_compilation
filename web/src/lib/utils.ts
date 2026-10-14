import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 风格 cn 工具（assistant-ui elements 家族使用） */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}