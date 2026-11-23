import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { ChatView } from "@/features/chat/chat-view";
import { isProgramSlug, monthNumber } from "@/features/programs/route-params";

/**
 * A month's tutor chat. The chat lives in this LAYOUT, like the (chat) group's: a new chat getting its id moves the URL to
 * /c/<id> under it without remounting, so the stream, focus and scroll carry on. The place only names what is
 * asked for; the API checks the account's invoice and binds the chat to it.
 */
export default async function Layout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string; month?: string }>;
}) {
  const { slug, month } = await params;
  const number = monthNumber(month ?? "");
  if (!isProgramSlug(slug) || number === null) notFound();
  return (
    <>
      <ChatView place={{ program: slug, month: number, mode: "tutor" }} />
      {children}
    </>
  );
}
