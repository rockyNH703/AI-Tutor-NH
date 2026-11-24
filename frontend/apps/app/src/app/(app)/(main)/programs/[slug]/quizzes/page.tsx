import { notFound } from "next/navigation";

import { ProgramQuizzes } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

export const metadata = { title: "Quizzes & Games" };

/** Quizzes & Games: a month's practice topics. */
export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  return <ProgramQuizzes slug={slug} />; // ?month= is read where it is shown (program-pages.tsx usePickedMonth)
}
