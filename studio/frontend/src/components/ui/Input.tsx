import { forwardRef } from "react";
import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const FIELD =
  "flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm " +
  "shadow-sm placeholder:text-muted-foreground focus-visible:outline-none " +
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
  "focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(FIELD, "h-10", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, rows = 4, ...props }, ref) => (
  <textarea
    ref={ref}
    rows={rows}
    className={cn(FIELD, "min-h-[80px] font-mono", className)}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export function Label(props: { htmlFor?: string; className?: string; children: React.ReactNode }) {
  return (
    <label
      htmlFor={props.htmlFor}
      className={cn("text-sm font-medium leading-none", props.className)}
    >
      {props.children}
    </label>
  );
}
