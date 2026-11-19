import { existsSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

/** An absolute output folder that is in no git working tree: not this worktree, not the main checkout, not any repo. */
export function assertOutsideAnyRepo(folder: string): string {
  if (!isAbsolute(folder)) throw new Error("the output folder must be an absolute path");
  // shortcut: a junction under an allowed folder that points into a repo is not followed; resolve realpaths if that ever matters
  for (let dir = resolve(folder); ; dir = dirname(dir)) {
    if (existsSync(join(dir, ".git"))) throw new Error("the output folder must be outside every git working tree");
    if (dirname(dir) === dir) return resolve(folder);
  }
}
