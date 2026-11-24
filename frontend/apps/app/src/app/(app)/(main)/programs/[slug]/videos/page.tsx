import { notFound } from "next/navigation";

import { ProgramVideos } from "@/features/programs/program-pages";
import { isProgramSlug } from "@/features/programs/route-params";

export const metadata = { title: "Videos & Links" };

/** Videos & Links: a month's NH lessons. */
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ month?: string | string[] }>;
}) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  // ?month= opens that month (a link, a reload, coming back); the page checks it against the program's months.
  const { month } = await searchParams;
  return <ProgramVideos slug={slug} month={typeof month === "string" ? month : undefined} />;
}
