import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Tests run with globals off, so Testing Library's auto-cleanup hook is not
// registered for us — unmount between tests explicitly.
afterEach(cleanup);
