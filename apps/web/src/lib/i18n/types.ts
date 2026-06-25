// All translation keys map English → translated string.
// When a key is missing in a language dictionary, the key (English) is used as fallback.
export type Translations = Record<string, string>;

export type Language = "en" | "ko" | "zh";

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "EN" },
  { code: "ko", label: "한" },
  { code: "zh", label: "中" },
];

const STORAGE_KEY = "brp-language";

export function getStoredLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "ko") return "ko";
    if (stored === "zh") return "zh";
  } catch {
    // localStorage unavailable
  }
  return "en";
}

export function storeLanguage(lang: Language) {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // localStorage unavailable
  }
}
