import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme, type ThemePreference } from "../theme/useTheme";

const ORDER: ThemePreference[] = ["light", "dark", "system"];
const ICONS: Record<ThemePreference, typeof Sun> = { light: Sun, dark: Moon, system: Monitor };
const LABELS: Record<ThemePreference, string> = { light: "Light theme", dark: "Dark theme", system: "System theme" };

export default function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const Icon = ICONS[theme];

  function cycle() {
    const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
    setTheme(next);
  }

  return (
    <button className="icon-btn" onClick={cycle} title={`${LABELS[theme]} — click to change`} aria-label="Toggle theme">
      <Icon />
    </button>
  );
}
