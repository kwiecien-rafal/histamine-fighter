import { Outlet } from "react-router-dom";

import { Footer } from "./Footer";
import { Navbar } from "./Navbar";

// Shell for every public route: shared navbar, page content, and the footer with
// the medical disclaimer. Flex column pins the footer to the bottom on short pages.
// The admin route renders its own chrome and stays outside this layout.
export function Layout() {
  return (
    <div className="flex min-h-screen flex-col bg-cream-50 text-stone-900">
      <Navbar />
      <main className="flex-1 px-6 pt-10 pb-24">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}
