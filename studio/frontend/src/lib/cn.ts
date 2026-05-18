// Minimal `clsx`-style class joiner. We don't want a dependency for a
// six-line utility.
export function cn(
  ...values: Array<string | undefined | null | false | Record<string, boolean>>
): string {
  const out: string[] = [];
  for (const v of values) {
    if (!v) continue;
    if (typeof v === "string") out.push(v);
    else for (const [k, on] of Object.entries(v)) if (on) out.push(k);
  }
  return out.join(" ");
}
