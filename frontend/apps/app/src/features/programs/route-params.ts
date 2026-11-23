/** Route segments the program pages accept; anything else is a 404 before any request (the API checks again). */
export function isProgramSlug(value: string): boolean {
  return value.length <= 60 && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(value);
}

/** A month segment: 1 to 12, else null. */
export function monthNumber(value: string): number | null {
  return /^(1[0-2]|[1-9])$/.test(value) ? Number(value) : null;
}
