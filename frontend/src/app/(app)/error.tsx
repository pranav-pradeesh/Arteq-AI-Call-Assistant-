"use client";
import { Button, EmptyState } from "@/components/ui";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="grid place-items-center py-20">
      <div className="text-center">
        <EmptyState
          title="Something went wrong"
          hint="An unexpected error occurred. Please try again."
        />
        {error.message && (
          <p className="mt-1 text-xs text-gray-400">{error.message}</p>
        )}
        <div className="mt-4">
          <Button variant="outline" onClick={() => reset()}>
            Try again
          </Button>
        </div>
      </div>
    </div>
  );
}
