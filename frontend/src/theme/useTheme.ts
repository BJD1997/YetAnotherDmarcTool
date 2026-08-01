import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "dmarcwatch-theme";

function applyTheme(pref: ThemePreference) {
  if (pref === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = pref;
  }
}

function readStoredTheme(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function useTheme(): [ThemePreference, (pref: ThemePreference) => void] {
  const [theme, setThemeState] = useState<ThemePreference>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((pref: ThemePreference) => {
    if (pref === "system") {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, pref);
    }
    setThemeState(pref);
  }, []);

  return [theme, setTheme];
}
