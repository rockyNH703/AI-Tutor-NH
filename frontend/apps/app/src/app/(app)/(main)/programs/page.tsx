import { MyPrograms } from "@/features/programs/program-pages";

export const metadata = { title: "My Programs" };

/** My Programs: the account's NH programs and the one place to add an invoice (every account; the API decides). */
export default function Page() {
  return <MyPrograms />;
}
