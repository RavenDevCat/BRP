import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from "react";
import type { Language, Translations } from "./types";
import { LANGUAGES, getStoredLanguage, storeLanguage } from "./types";
import en from "./en";
import ko from "./ko";
import zh from "./zh";

const dictionaries: Record<Language, Translations> = { en, ko, zh };

interface LanguageContextValue {
    lang: Language;
    setLang: (lang: Language) => void;
    /** Translate a key. Falls back to the key (English) if missing. */
    t: (key: string, fallback?: string) => string;
    /** Whether the language switcher should be visible (server-controlled). */
    switchEnabled: boolean;
    availableLanguages: typeof LANGUAGES;
}

const LanguageContext = createContext<LanguageContextValue>({
    lang: "en",
    setLang: () => {},
    t: (key, fallback) => fallback ?? key,
    switchEnabled: false,
    availableLanguages: LANGUAGES,
});

export function LanguageProvider({
    switchEnabled,
    availableLanguages,
    children,
}: {
    switchEnabled: boolean;
    availableLanguages?: string[];
    children: ReactNode;
}) {
    const [lang, setLangState] = useState<Language>(() => getStoredLanguage());
    const enabledLanguages = useMemo(() => {
        const allowed = new Set(availableLanguages?.filter(isKnownLanguage));
        return allowed.size ? LANGUAGES.filter((item) => allowed.has(item.code)) : LANGUAGES;
    }, [availableLanguages]);

    useEffect(() => {
        if (!enabledLanguages.some((item) => item.code === lang)) {
            const fallback = enabledLanguages[0]?.code ?? "en";
            setLangState(fallback);
            storeLanguage(fallback);
        }
    }, [enabledLanguages, lang]);

    const setLang = useCallback((next: Language) => {
        setLangState(next);
        storeLanguage(next);
    }, []);

    const dict = useMemo(() => dictionaries[lang], [lang]);

    const t = useCallback(
        (key: string, fallback?: string) => dict[key] ?? fallback ?? key,
        [dict],
    );

    const value = useMemo<LanguageContextValue>(
        () => ({ lang, setLang, t, switchEnabled, availableLanguages: enabledLanguages }),
        [lang, setLang, t, switchEnabled, enabledLanguages],
    );

    return (
        <LanguageContext.Provider value={value}>
            {children}
        </LanguageContext.Provider>
    );
}

export function useT() {
    return useContext(LanguageContext).t;
}

export function useLanguage() {
    return useContext(LanguageContext);
}

function isKnownLanguage(value: unknown): value is Language {
    return value === "en" || value === "ko" || value === "zh";
}
