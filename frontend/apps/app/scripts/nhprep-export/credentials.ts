import { readFileSync } from "node:fs";
import { isAbsolute } from "node:path";

export type Credentials = { email: string; password: string };

const SEPARATED = /^([^\s:,;|]+@[^\s:,;|]+)[\s:,;|]+(\S.*)$/;
// shortcut: an address glued to its password is split at the domain's first known TLD, so "x@mail.co.in<password>" splits wrong; write a separator in such a file
const GLUED = /^([^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*?\.(?:com|in|net|org|io))(\S+)$/;

/** An email and password from text: two lines, one line with a separator, or the address glued to the password. */
export function parseCredentials(text: string): Credentials {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const [first, second] = lines;
  if (lines.length === 2 && first && second && first.includes("@")) return { email: first, password: second };
  if (lines.length === 1 && first) {
    const found = SEPARATED.exec(first) ?? GLUED.exec(first);
    if (found?.[1] && found[2]) return { email: found[1], password: found[2] };
  }
  // Never echo the text: it holds a password.
  throw new Error("the credentials file is not in a known shape (email and password)");
}

/** Read the file named by an environment variable at run time; its content is never printed, logged or put in argv. */
export function readCredentials(variable: string): Credentials {
  const path = process.env[variable];
  if (!path) throw new Error(`set ${variable} to the absolute path of the credentials file`);
  if (!isAbsolute(path)) throw new Error(`${variable} must be an absolute path`);
  return parseCredentials(readFileSync(path, "utf8"));
}
