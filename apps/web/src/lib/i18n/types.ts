// All translation keys map English → translated string.
// When a key is missing in a language dictionary, the key (English) is used as fallback.
export type Translations = Record<string, string>;

export type Language = "en" | "ko" | "zh";

const ALL_LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "EN" },
  { code: "ko", label: "한" },
  { code: "zh", label: "中" },
];

const STORAGE_KEY = "brp-language";

export const LANGUAGES = ALL_LANGUAGES.filter((item) => isAvailableLanguage(item.code));

export function getStoredLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLanguage(stored) && isAvailableLanguage(stored)) return stored;
  } catch {
    // localStorage unavailable
  }
  return LANGUAGES[0]?.code ?? "en";
}

export function storeLanguage(lang: Language) {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // localStorage unavailable
  }
}

function isLanguage(value: unknown): value is Language {
  return value === "en" || value === "ko" || value === "zh";
}

function isAvailableLanguage(lang: Language) {
  const configured = String(import.meta.env.VITE_APP_LOCALES || "").trim();
  if (configured) {
    return configured.split(",").map((item) => item.trim()).includes(lang);
  }
  const host = typeof window === "undefined" ? "" : window.location.hostname;
  if (host.includes("brp-kr")) return lang === "en" || lang === "ko";
  return lang === "en" || lang === "zh";
}
