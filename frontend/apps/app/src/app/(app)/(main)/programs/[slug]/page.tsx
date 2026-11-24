import { notFound } from "next/navigation";

import { ProgramLanding } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

export const metadata = { title: "My Programs" };

/** A program's landing page: its five areas. */
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  return <ProgramLanding slug={slug} />;
}
