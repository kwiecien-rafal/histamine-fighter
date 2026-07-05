import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { requestMagicLink, verifyMagicLink } from "../api/auth";
import { Login } from "./Login";

vi.mock("../api/auth", async (importActual) => ({
  ...(await importActual<typeof import("../api/auth")>()),
  requestMagicLink: vi.fn(),
  verifyMagicLink: vi.fn(),
}));

const requestMagicLinkMock = vi.mocked(requestMagicLink);
const verifyMagicLinkMock = vi.mocked(verifyMagicLink);

function renderLogin(initialEntry = "/login") {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Login />
    </MemoryRouter>,
  );
}

describe("Login", () => {
  it("renders no Turnstile widget when no site key is configured", async () => {
    renderLogin();

    // The widget script only loads with VITE_TURNSTILE_SITE_KEY set; the test env
    // has none, so the form must submit without a Turnstile token.
    expect(document.querySelector("script[src*='turnstile']")).toBeNull();
    await userEvent.type(screen.getByLabelText("Email"), "gerald@example.com");
    expect(screen.getByRole("button", { name: /email me a sign-in link/i })).toBeEnabled();
  });

  it("links the consent line to terms and privacy", () => {
    renderLogin();

    expect(screen.getByRole("link", { name: "terms" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "privacy policy" })).toHaveAttribute(
      "href",
      "/privacy",
    );
  });

  it("offers OAuth as full navigations to the backend start routes", () => {
    renderLogin();

    expect(screen.getByRole("link", { name: /continue with google/i })).toHaveAttribute(
      "href",
      "/api/v1/auth/oauth/google/start",
    );
    expect(screen.getByRole("link", { name: /continue with github/i })).toHaveAttribute(
      "href",
      "/api/v1/auth/oauth/github/start",
    );
  });

  it("moves to the code-entry step after the email is sent", async () => {
    requestMagicLinkMock.mockResolvedValue(undefined);
    renderLogin();

    await userEvent.type(screen.getByLabelText("Email"), "gerald@example.com");
    await userEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    await waitFor(() => {
      expect(screen.getByText(/check your inbox/i)).toBeInTheDocument();
    });
    expect(requestMagicLinkMock).toHaveBeenCalledWith("gerald@example.com", null);
    expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
  });

  it("signs in with a valid code", async () => {
    requestMagicLinkMock.mockResolvedValue(undefined);
    verifyMagicLinkMock.mockResolvedValue({ email: "gerald@example.com", role: "user" });
    renderLogin();

    await userEvent.type(screen.getByLabelText("Email"), "gerald@example.com");
    await userEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));
    await waitFor(() => {
      expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
    });

    await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: /sign in with code/i }));

    await waitFor(() => {
      expect(verifyMagicLinkMock).toHaveBeenCalledWith({
        email: "gerald@example.com",
        code: "123456",
      });
    });
  });

  it("shows a friendly message for an OAuth error flag", () => {
    renderLogin("/login?error=oauth");

    expect(screen.getByRole("alert")).toHaveTextContent(/didn't complete/i);
  });

  it("surfaces a failed send as an alert", async () => {
    requestMagicLinkMock.mockRejectedValue(new Error("The email service rejected the message."));
    renderLogin();

    await userEvent.type(screen.getByLabelText("Email"), "gerald@example.com");
    await userEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/rejected the message/i);
    });
  });
});
