// MapLibre's worker is a separate ES module that the bundler cannot see. Copy it
// (and the shared chunk it imports) into public/ so it is served as-is, and point
// MapLibre at it with setWorkerUrl() in MapView.tsx. Runs on `npm install` to keep
// the copy in sync with the installed version; the files are also committed so a
// fresh checkout works even if the hook does not run.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "node_modules", "maplibre-gl", "dist");
const dst = join(root, "public", "maplibre");
mkdirSync(dst, { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) copyFileSync(join(src, f), join(dst, f));
console.log("copied MapLibre worker to public/maplibre/");
