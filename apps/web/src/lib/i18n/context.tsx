import {
    createContext,
    useCallback,
    useContext,
    useMemo,
    useState,
    type ReactNode,
} from "react";
import type { Language, Translations } from "./types";
import { getStoredLanguage, storeLanguage } from "./types";
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
}

const LanguageContext = createContext<LanguageContextValue>({
    lang: "en",
    setLang: () => {},
    t: (key, fallback) => fallback ?? key,
    switchEnabled: false,
});

export function LanguageProvider({
    switchEnabled,
    children,
}: {
    switchEnabled: boolean;
    children: ReactNode;
}) {
    const [lang, setLangState] = useState<Language>(() => getStoredLanguage());

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
        () => ({ lang, setLang, t, switchEnabled }),
        [lang, setLang, t, switchEnabled],
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
