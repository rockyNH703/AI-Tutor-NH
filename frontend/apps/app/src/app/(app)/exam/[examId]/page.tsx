import { notFound } from "next/navigation";

import { ExamScreen } from "@/features/exams/exam-screen";

export const metadata = { title: "Practice test" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** A Topic page's key (features/programs/quiz-topics.ts `topicKey`), for "Back to topic". */
const TOPIC = /^[a-z0-9]+(-[a-z0-9]+)*$/;

type Props = { params: Promise<{ examId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

/** A practice exam, a full window like Settings (outside the app shell): the exam, then its result. */
export default async function Page({ params, searchParams }: Props) {
  const { examId } = await params;
  if (!UUID.test(examId)) notFound();
  const topic = (await searchParams).topic;
  return <ExamScreen examId={examId} topic={typeof topic === "string" && topic.length <= 200 && TOPIC.test(topic) ? topic : null} />;
}
