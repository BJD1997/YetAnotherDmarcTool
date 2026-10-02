import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoadMoreButton } from "./LoadMoreButton";

describe("LoadMoreButton", () => {
  it("renders nothing when there's no next page", () => {
    const { container } = render(<LoadMoreButton hasNextPage={false} isFetchingNextPage={false} onClick={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("calls onClick and shows a loading label while fetching", () => {
    const onClick = vi.fn();
    render(<LoadMoreButton hasNextPage={true} isFetchingNextPage={false} onClick={onClick} />);
    fireEvent.click(screen.getByText("Load more"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("disables itself and shows Loading… while isFetchingNextPage", () => {
    render(<LoadMoreButton hasNextPage={true} isFetchingNextPage={true} onClick={vi.fn()} />);
    expect(screen.getByText("Loading…")).toBeDisabled();
  });
});
