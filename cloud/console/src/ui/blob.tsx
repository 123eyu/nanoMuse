/** The record's files, shown where they were sent: a picture a person attached, a screenshot
 *  the hands took, a clip, a voice note. GET /v1/admin/blobs/{sha256} wants the admin
 *  token, which an <img src> cannot carry, so each file is fetched once with the token and
 *  shown from an object URL kept for the page's life (the same screenshot in many steps is
 *  fetched once). Click a picture for it at full size. */

import { useEffect, useState, type ReactNode } from "react";
import { ApiError, headers } from "../lib/api";
import { useT } from "../lib/i18n";
import { shaOf } from "../lib/turns";
import { cx } from "./primitives";

const urls = new Map<string, Promise<{ url: string; mime: string }>>();

/** The object URL of a file, fetched once per page. */
export function blobUrl(sha: string): Promise<{ url: string; mime: string }> {
  let p = urls.get(sha);
  if (!p) {
    p = fetch(`/v1/admin/blobs/${sha}`, { headers: headers() }).then(async (r) => {
      if (!r.ok) throw new ApiError(r.status, `http_${r.status}`, r.statusText);
      const b = await r.blob();
      return { url: URL.createObjectURL(b), mime: b.type || r.headers.get("content-type") || "" };
    });
    urls.set(sha, p);
    p.catch(() => urls.delete(sha));
  }
  return p;
}

/** For tests: forget what was fetched. */
export function resetBlobs(): void {
  urls.clear();
}

export function useBlob(sha: string | null): { url: string; mime: string; error: string } {
  const [state, setState] = useState<{ url: string; mime: string; error: string }>({ url: "", mime: "", error: "" });
  useEffect(() => {
    if (!sha) return;
    let live = true;
    setState({ url: "", mime: "", error: "" });
    blobUrl(sha).then(
      (v) => live && setState({ ...v, error: "" }),
      (e: Error) => live && setState({ url: "", mime: "", error: e.message || "failed" }),
    );
    return () => {
      live = false;
    };
  }, [sha]);
  return state;
}

/** One file from the record, by its reference: a picture (click to enlarge), a clip, a voice
 *  note, or a link for anything else. */
export function BlobView({ refer, className, size = 160 }: { refer: string; className?: string; size?: number }) {
  const T = useT();
  const sha = shaOf(refer);
  const { url, mime, error } = useBlob(sha);
  const [big, setBig] = useState(false);
  if (error) return <span className={cx("hint", className)} title={sha}>{T.tu.fileGone}</span>;
  if (!url) return <span className={cx("inline-block animate-pulse rounded-md bg-fill", className)} style={{ width: size, height: Math.round(size * 0.66) }} />;
  if (mime.startsWith("image/")) {
    return (
      <>
        <button type="button" className={cx("inline-block overflow-hidden rounded-md border border-line bg-panel", className)} onClick={() => setBig(true)} title={T.tu.enlarge}>
          <img src={url} alt="" style={{ maxHeight: size, maxWidth: size * 2 }} className="block object-contain" loading="lazy" />
        </button>
        {big && <Lightbox onClose={() => setBig(false)}><img src={url} alt="" className="max-h-[90vh] max-w-[90vw] object-contain" /></Lightbox>}
      </>
    );
  }
  if (mime.startsWith("video/")) return <video src={url} controls className={cx("rounded-md", className)} style={{ maxHeight: size * 1.5, maxWidth: size * 2 }} />;
  if (mime.startsWith("audio/")) return <audio src={url} controls className={className} />;
  return (
    <a href={url} download={sha.slice(0, 12)} className={cx("text-accent", className)}>
      {T.tu.file} <code>{sha.slice(0, 12)}</code>
    </a>
  );
}

function Lightbox({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" onClick={onClose} role="presentation">
      {children}
    </div>
  );
}
