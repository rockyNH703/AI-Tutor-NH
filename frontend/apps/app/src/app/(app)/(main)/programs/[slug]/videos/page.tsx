import { notFound } from "next/navigation";

import { ProgramVideos } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

/** Videos & Links: a month's NH lessons. */
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  return <ProgramVideos slug={slug} />;
}
