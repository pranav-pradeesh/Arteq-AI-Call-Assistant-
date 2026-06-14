import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataTable } from "@/components/data-table";
import type { ColumnDef } from "@/components/data-table";

type Row = { name: string };

const columns: ColumnDef<Row, string>[] = [
  {
    accessorKey: "name",
    header: "Name",
  },
];

describe("DataTable", () => {
  it("renders rows for each data item", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ name: "Alpha" }, { name: "Beta" }]}
      />
    );
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
  });

  it("renders the column header", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ name: "Alpha" }]}
      />
    );
    expect(screen.getByText("Name")).toBeTruthy();
  });

  it("renders the search input with default placeholder", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ name: "Alpha" }]}
      />
    );
    expect(screen.getByPlaceholderText("Search…")).toBeTruthy();
  });

  it("renders the search input with custom placeholder", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ name: "Alpha" }]}
        searchPlaceholder="Find record"
      />
    );
    expect(screen.getByPlaceholderText("Find record")).toBeTruthy();
  });

  it("renders EmptyState with emptyTitle when data is empty", () => {
    render(
      <DataTable
        columns={columns}
        data={[]}
        emptyTitle="Nothing"
      />
    );
    expect(screen.getByText("Nothing")).toBeTruthy();
  });

  it("renders default emptyTitle when data is empty and no emptyTitle provided", () => {
    render(
      <DataTable
        columns={columns}
        data={[]}
      />
    );
    expect(screen.getByText("Nothing here yet")).toBeTruthy();
  });

  it("does not render EmptyState when data is not empty", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ name: "Alpha" }]}
        emptyTitle="Nothing"
      />
    );
    expect(screen.queryByText("Nothing")).toBeNull();
  });
});
