// Render feature_request.html to a PDF with Chromium, and write where every form field
// sits on its page, so build.py can lay fillable fields over the printed boxes.
//
//   node render.mjs <out.pdf> <fields.json>
//
// Fails if any page's content overflows its A4 body.

import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = path.dirname(fileURLToPath(import.meta.url));
const [outPdf, outFields] = process.argv.slice(2);
if (!outPdf || !outFields) {
  console.error("usage: node render.mjs <out.pdf> <fields.json>");
  process.exit(2);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 794, height: 1123 } });
  await page.emulateMedia({ media: "print" });
  await page.goto(pathToFileURL(path.join(here, "feature_request.html")).href);
  await page.waitForFunction(() => document.documentElement.dataset.ready === "1");
  await page.evaluate(() => document.fonts.ready);

  const report = await page.evaluate(() => {
    const pxToPt = 0.75; // CSS px are 1/96 in, PDF points 1/72 in
    const pages = [...document.querySelectorAll(".page")];
    const overflow = [];
    pages.forEach((p, i) => {
      const b = p.querySelector(".body");
      if (b && b.scrollHeight > b.clientHeight + 1) overflow.push({ page: i + 1, by_px: b.scrollHeight - b.clientHeight });
      // cards: each one's content must fit its own box
      p.querySelectorAll(".card .c-main").forEach((m) => {
        const card = m.closest(".card");
        const cr = card.getBoundingClientRect(), mr = m.getBoundingClientRect();
        const last = m.lastElementChild.getBoundingClientRect();
        if (last.bottom > cr.bottom - 2) overflow.push({ page: i + 1, card: card.querySelector(".id").textContent, by_px: Math.round(last.bottom - cr.bottom + 2) });
      });
    });
    const fields = [...document.querySelectorAll(".ff")].map((e) => {
      const pg = e.closest(".page");
      const pr = pg.getBoundingClientRect(), r = e.getBoundingClientRect();
      return {
        page: pages.indexOf(pg),
        kind: e.dataset.kind,
        name: e.dataset.name,
        value: e.dataset.value || null,
        shape: e.classList.contains("round") ? "round" : "square",
        x: (r.left - pr.left) * pxToPt,
        top: (r.top - pr.top) * pxToPt,
        w: r.width * pxToPt,
        h: r.height * pxToPt,
      };
    });
    return { pages: pages.length, overflow, fields };
  });

  if (report.overflow.length) {
    console.error("content overflows:", JSON.stringify(report.overflow));
    process.exitCode = 1;
  }
  await page.pdf({ path: outPdf, preferCSSPageSize: true, printBackground: true });
  writeFileSync(outFields, JSON.stringify(report, null, 1));
  console.log(`rendered ${report.pages} pages, ${report.fields.length} form fields`);
} finally {
  await browser.close();
}
