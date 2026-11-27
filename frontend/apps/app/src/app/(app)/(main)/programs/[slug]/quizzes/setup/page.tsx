import { notFound, redirect } from "next/navigation";

import { ExamSetup } from "@/features/exams/exam-setup";
import { isProgramSlug, monthNumber } from "@/features/programs/route-params";

export const metadata = { title: "Test setup" };

/** A Topic page's key (features/programs/quiz-topics.ts `topicKey`). */
const TOPIC = /^[a-z0-9]+(-[a-z0-9]+)*$/;
/** A Quiz is a bank topic id, e.g. "CCNA-D1-T01" (the API checks it against the month again). */
const QUIZ = /^[A-Za-z0-9._-]{1,100}$/;

type Props = { params: Promise<{ slug: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

/** Quizzes & Games › month › topic › test setup (`?month=&topic=&quiz=`; no quiz: a test of the whole topic). */
export default async function Page({ params, searchParams }: Props) {
  const { slug } = await params;
  if (!isProgramSlug(slug)) notFound();
  const query = await searchParams;
  const one = (name: string) => (typeof query[name] === "string" ? (query[name] as string) : "");
  const month = monthNumber(one("month"));
  const topic = one("topic");
  if (month === null || topic.length > 200 || !TOPIC.test(topic)) redirect(`/programs/${slug}/quizzes`);
  const quiz = one("quiz");
  return <ExamSetup slug={slug} month={month} topic={topic} quizId={QUIZ.test(quiz) ? quiz : null} />;
}
