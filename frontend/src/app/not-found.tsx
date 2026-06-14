import Link from "next/link";

export default function NotFound() {
  return (
    <div className="grid min-h-screen place-items-center bg-gray-50 p-4 text-center">
      <div>
        <p className="text-5xl font-bold text-gray-300">404</p>
        <p className="mt-2 text-gray-600">This page doesn&apos;t exist.</p>
        <Link href="/overview" className="mt-4 inline-block text-brand-600 hover:underline">
          ← Back to dashboard
        </Link>
      </div>
    </div>
  );
}
