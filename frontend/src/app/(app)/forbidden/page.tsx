import Link from "next/link";
import { NoReason } from "@/components/no-reason";

export default function ForbiddenPage() {
  return (
    <div className="grid place-items-center py-20 text-center">
      <div className="max-w-md">
        <p className="text-5xl font-bold text-gray-300">403</p>
        <p className="mt-2 font-medium text-gray-700">You don&apos;t have access to that page.</p>
        <NoReason className="mt-3 text-sm italic text-gray-500" />
        <Link href="/overview" className="mt-4 inline-block text-brand-600 hover:underline">
          &larr; Back to dashboard
        </Link>
      </div>
    </div>
  );
}
