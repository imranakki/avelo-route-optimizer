// Google Maps JavaScript API loading, via the official @googlemaps/js-api-loader.
//
// The key is a public browser key restricted by HTTP referrer; it is read from
// NEXT_PUBLIC_GOOGLE_MAPS_API_KEY. `googleAuthFailed` resolves true if Google rejects
// the key at runtime (API not enabled, referrer not allowed), so the UI can fall back
// to the open-data map instead of showing a blank grey box.

import { importLibrary as load, setOptions } from "@googlemaps/js-api-loader";

export const GOOGLE_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "";

let configured = false;
let authFailure: Promise<boolean> | null = null;

declare global {
  interface Window {
    gm_authFailure?: () => void;
  }
}

/** Configure the loader once. Returns false when no key is set. */
export function bootstrapGoogle(): boolean {
  if (typeof window === "undefined" || !GOOGLE_KEY) return false;
  if (configured) return true;
  configured = true;
  authFailure = new Promise<boolean>((resolve) => {
    window.gm_authFailure = () => resolve(true);
    // If Google has not rejected the key within 15 s of first use, assume it is fine.
    setTimeout(() => resolve(false), 15_000);
  });
  setOptions({ key: GOOGLE_KEY, v: "weekly", language: "fr-CA", region: "CA" });
  return true;
}

export const importLibrary = load;

export function googleAuthFailed(): Promise<boolean> {
  return authFailure ?? Promise.resolve(false);
}
