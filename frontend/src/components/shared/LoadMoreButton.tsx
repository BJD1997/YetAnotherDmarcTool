interface LoadMoreButtonProps {
  hasNextPage: boolean | undefined;
  isFetchingNextPage: boolean;
  onClick: () => void;
}

export function LoadMoreButton({ hasNextPage, isFetchingNextPage, onClick }: LoadMoreButtonProps) {
  if (!hasNextPage) return null;
  return (
    <button type="button" className="btn btn--secondary" style={{ marginTop: "0.75rem" }} onClick={onClick} disabled={isFetchingNextPage}>
      {isFetchingNextPage ? "Loading…" : "Load more"}
    </button>
  );
}
