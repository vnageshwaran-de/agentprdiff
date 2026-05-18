import { forwardRef } from "react";
import type { SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex h-10 w-full items-center justify-between rounded-md border border-input bg-background",
      "px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring",
      "focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  >
    {children}
  </select>
));
Select.displayName = "Select";
