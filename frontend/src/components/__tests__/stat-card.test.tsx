import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatCard } from "@/components/stat-card";

describe("StatCard", () => {
  it("renders label and value", () => {
    render(<StatCard label="Total calls" value="42" />);
    expect(screen.getByText("Total calls")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
  });

  it("does not render a delta indicator when delta is not provided", () => {
    render(<StatCard label="Total calls" value="42" />);
    expect(screen.queryByText("↑")).toBeNull();
    expect(screen.queryByText("↓")).toBeNull();
  });

  it("renders a hint when provided", () => {
    render(<StatCard label="Total calls" value="42" hint="vs last week" />);
    expect(screen.getByText("vs last week")).toBeTruthy();
  });

  it("renders up arrow and percentage for positive delta", () => {
    render(<StatCard label="Revenue" value="₹500" delta={12} />);
    expect(screen.getByText("↑")).toBeTruthy();
    // delta=12 renders as "12.0%"
    expect(screen.getByText(/12\.0%/)).toBeTruthy();
  });

  it("renders down arrow and percentage for negative delta", () => {
    render(<StatCard label="Revenue" value="₹500" delta={-5} />);
    expect(screen.getByText("↓")).toBeTruthy();
    // delta=-5 renders absolute value as "5.0%"
    expect(screen.getByText(/5\.0%/)).toBeTruthy();
  });

  it("applies green text class for positive delta", () => {
    const { container } = render(<StatCard label="Revenue" value="₹500" delta={12} />);
    const deltaSpan = container.querySelector(".text-green-600");
    expect(deltaSpan).not.toBeNull();
  });

  it("applies red text class for negative delta", () => {
    const { container } = render(<StatCard label="Revenue" value="₹500" delta={-5} />);
    const deltaSpan = container.querySelector(".text-red-600");
    expect(deltaSpan).not.toBeNull();
  });

  it("renders up arrow and 0.0% for delta=0", () => {
    render(<StatCard label="Calls" value="10" delta={0} />);
    expect(screen.getByText("↑")).toBeTruthy();
    expect(screen.getByText(/0\.0%/)).toBeTruthy();
  });
});
