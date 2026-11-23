import { notFound } from "next/navigation";

import { ProgramInterviews } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

/** Mock Interviews: one interviewer per month. */
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  return <ProgramInterviews slug={slug} />;
}
