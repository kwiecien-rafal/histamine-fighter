import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getComposeSettings, updateComposeSettings, type ComposeSettings } from "../api/admin";
import { GenerationSettingsPanel } from "./GenerationSettingsPanel";

vi.mock("../api/admin", async (importActual) => {
  const actual = await importActual<typeof import("../api/admin")>();
  return {
    ...actual,
    getComposeSettings: vi.fn(),
    updateComposeSettings: vi.fn(),
  };
});

const getSettingsMock = vi.mocked(getComposeSettings);
const updateSettingsMock = vi.mocked(updateComposeSettings);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("GenerationSettingsPanel", () => {
  it("saves the chosen provider and model", async () => {
    const current: ComposeSettings = {
      provider: "openai",
      model: "gpt-5.4-mini",
      available_providers: ["openai", "ollama"],
    };
    getSettingsMock.mockResolvedValue(current);
    updateSettingsMock.mockResolvedValue({ ...current, model: "gpt-5.4" });
    const user = userEvent.setup();

    render(<GenerationSettingsPanel onExpired={vi.fn()} />);

    const model = await screen.findByPlaceholderText("provider default");
    await user.clear(model);
    await user.type(model, "gpt-5.4");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith("openai", "gpt-5.4"));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("flags a saved provider that is no longer available", async () => {
    getSettingsMock.mockResolvedValue({
      provider: "anthropic",
      model: null,
      available_providers: ["openai"],
    });

    render(<GenerationSettingsPanel onExpired={vi.fn()} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/anthropic/);
    expect(alert).toHaveTextContent(/no key configured/);
  });
});
