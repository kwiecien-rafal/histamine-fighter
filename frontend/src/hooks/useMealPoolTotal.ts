import { useEffect, useState } from "react";

import { browseMeals } from "../api/meals";

// The size of the approved meal pool, for the Home stat. Purely decorative, so a
// failed fetch just stays null and the page shows fallback copy instead of an error.
export function useMealPoolTotal(): { total: number | null } {
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    browseMeals({ limit: 1 })
      .then((page) => {
        if (!cancelled) setTotal(page.total);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return { total };
}
