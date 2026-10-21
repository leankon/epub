import { ApiError } from "@/lib/api";

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const message = e ? e.message : error instanceof Error ? error.message : String(error);
  const issues = (e?.details?.issues as { loc: string[]; msg: string }[] | undefined) ?? [];
  const problems = (e?.details?.problems as string[] | undefined) ?? (e?.details?.errors as string[] | undefined) ?? [];
  return (
    <div className="notice err" role="alert">
      <strong>{message}</strong>
      {e?.code && <span className="muted mono"> ({e.code})</span>}
      {issues.length > 0 && (
        <ul>
          {issues.map((i, k) => (
            <li key={k}>
              {i.loc.filter((x) => x !== "body").join(".")}: {i.msg}
            </li>
          ))}
        </ul>
      )}
      {problems.length > 0 && (
        <ul>
          {problems.slice(0, 10).map((p, k) => (
            <li key={k}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
