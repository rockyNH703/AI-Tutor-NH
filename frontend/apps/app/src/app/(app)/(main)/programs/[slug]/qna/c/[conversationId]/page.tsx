import { notFound } from "next/navigation";

import { isUuid } from "@/lib/api/routes";

/** An existing program chat, rendered by the layout's chat view (the API answers 404 for anyone else's). */
export default async function Page({ params }: { params: Promise<{ conversationId: string }> }) {
  if (!isUuid((await params).conversationId)) notFound();
  return null;
}
