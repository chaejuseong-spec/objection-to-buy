// PDF.js 는 브라우저에서만 불러온다. 같은 문서는 한 번만 연다.
import type { PDFDocumentProxy } from "pdfjs-dist";

const docs = new Map<string, Promise<PDFDocumentProxy>>();

export function loadPdf(url: string): Promise<PDFDocumentProxy> {
  let doc = docs.get(url);
  if (!doc) {
    doc = (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      return pdfjs.getDocument({ url }).promise;
    })();
    doc.catch(() => docs.delete(url));
    docs.set(url, doc);
  }
  return doc;
}

export const pdfUrl = (id: string) => `/api/reports/${id}/pdf`;
