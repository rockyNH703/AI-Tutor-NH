import { notFound } from "next/navigation";

import { ProgramModules } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

/** Course Module: the program's months, each opening its own tutor. */
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  return <ProgramModules slug={slug} />;
}
