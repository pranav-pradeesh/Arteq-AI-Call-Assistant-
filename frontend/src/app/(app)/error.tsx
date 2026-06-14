"use client";
import * as React from "react";
import { Button, Card, CardBody } from "@/components/ui";

export default function AppError({
  error, reset,
}: { error: Error & { digest?: string }; reset: () => void }) {
  React.useEffect(() => {
    // eslint-disable-next-line no-console
    console.error(error);
  }, [error]);

  return (
    <div className="grid place-items-center py-20">
      <Card className="max-w-md">
        <CardBody className="text-center">
          <p className="font-semibold text-gray-900">Something went wrong</p>
          <p className="mt-1 text-sm text-gray-500">{error.message || "An unexpected error occurred."}</p>
          <Button className="mt-4" onClick={reset}>Try again</Button>
        </CardBody>
      </Card>
    </div>
  );
}
