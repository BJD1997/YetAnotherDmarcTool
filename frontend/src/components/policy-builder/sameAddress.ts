// Report addresses compare case-insensitively; either side may be absent.
export const sameAddress = (a: string | null | undefined, b: string | null | undefined) =>
  !!a && !!b && a.toLowerCase() === b.toLowerCase();
