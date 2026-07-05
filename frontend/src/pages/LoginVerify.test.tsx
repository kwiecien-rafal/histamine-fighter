import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { verifyMagicLink } from "../api/auth";
import { useSessionStore } from "../store/session";
import { LoginVerify } from "./LoginVerify";

vi.mock("../api/auth", async (importActual) => ({
  ...(await importActual<typeof import("../api/auth")>()),
  verifyMagicLink: vi.fn(),
  getSessionUser: vi.fn(),
}));

const verifyMagicLinkMock = vi.mocked(verifyMagicLink);

function renderVerify(query: string) {
  render(
    <StrictMode>
      <MemoryRouter initialEntries={[`/login/verify${query}`]}>
        <Routes>
          <Route path="/login/verify" element={<LoginVerify />} />
          <Route path="/" element={<p>home page</p>} />
          <Route path="/login" element={<p>login page</p>} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

describe("LoginVerify", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({ user: null, status: "anon" });
  });

  it("POSTs the token exactly once, even under StrictMode's double effect", async () => {
    verifyMagicLinkMock.mockResolvedValue({ email: "gerald@example.com", role: "user" });

    renderVerify("?token=abc");

    await waitFor(() => {
      expect(screen.getByText("home page")).toBeInTheDocument();
    });
    // The token is single-use server-side; a second POST would consume nothing
    // but read as a failure, so the effect must be latched.
    expect(verifyMagicLinkMock).toHaveBeenCalledTimes(1);
    expect(useSessionStore.getState().user?.email).toBe("gerald@example.com");
  });

  it("shows the expired state with a way back when the token is refused", async () => {
    verifyMagicLinkMock.mockRejectedValue(new Error("invalid"));

    renderVerify("?token=stale");

    await waitFor(() => {
      expect(screen.getByText(/sign-in link expired/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /back to sign in/i })).toHaveAttribute(
      "href",
      "/login",
    );
  });

  it("treats a missing token as a failed link", async () => {
    renderVerify("");

    await waitFor(() => {
      expect(screen.getByText(/sign-in link expired/i)).toBeInTheDocument();
    });
    expect(verifyMagicLinkMock).not.toHaveBeenCalled();
  });
});
