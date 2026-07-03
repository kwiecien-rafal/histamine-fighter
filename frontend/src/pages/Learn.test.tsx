import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { askLearn, listLearnArticles, type LearnResponse } from "../api/learn";
import { Learn } from "./Learn";

vi.mock("../api/learn", async (importActual) => {
  const actual = await importActual<typeof import("../api/learn")>();
  return { ...actual, askLearn: vi.fn(), listLearnArticles: vi.fn() };
});

const askMock = vi.mocked(askLearn);
const articlesMock = vi.mocked(listLearnArticles);

function grounded(): LearnResponse {
  return {
    question: "What is DAO?",
    answer: "DAO is the enzyme that breaks down histamine in the gut.",
    grounded: true,
    citations: [
      { title: "DAO and histamine breakdown", source: "SIGHI", slug: "dao-and-histamine-breakdown" },
    ],
    model: "ollama/test-model",
  };
}

function declined(): LearnResponse {
  return {
    question: "Best motor oil?",
    answer: null,
    grounded: false,
    citations: [],
    model: "ollama/test-model",
  };
}

function renderLearn() {
  render(
    <MemoryRouter>
      <Learn />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  articlesMock.mockResolvedValue([
    { slug: "what-is-histamine-intolerance", title: "What is histamine intolerance", topic: "basics" },
  ]);
});

describe("Learn", () => {
  it("fills the question from a topic chip without submitting", async () => {
    const user = userEvent.setup();
    renderLearn();

    await user.click(await screen.findByRole("button", { name: "What is histamine intolerance" }));

    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue(
      "Tell me about what is histamine intolerance.",
    );
    expect(askMock).not.toHaveBeenCalled();
  });

  it("shows a grounded answer with its sources and model badge", async () => {
    askMock.mockResolvedValue(grounded());
    const user = userEvent.setup();
    renderLearn();

    await user.type(screen.getByRole("textbox", { name: "Your question" }), "What is DAO?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(
      await screen.findByText("DAO is the enzyme that breaks down histamine in the gut."),
    ).toBeInTheDocument();
    expect(screen.getByText("DAO and histamine breakdown — SIGHI")).toBeInTheDocument();
    expect(screen.getByText("ollama/test-model")).toBeInTheDocument();
  });

  it("renders an honest decline when the sources cannot back an answer", async () => {
    askMock.mockResolvedValue(declined());
    const user = userEvent.setup();
    renderLearn();

    await user.type(screen.getByRole("textbox", { name: "Your question" }), "Best motor oil?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/outside our sources/i);
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
  });

  it("surfaces an error with a working try again", async () => {
    askMock
      .mockRejectedValueOnce(new Error("Catch your breath and try again in a minute."))
      .mockResolvedValueOnce(grounded());
    const user = userEvent.setup();
    renderLearn();

    await user.type(screen.getByRole("textbox", { name: "Your question" }), "What is DAO?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/catch your breath/i);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByText("DAO is the enzyme that breaks down histamine in the gut."),
    ).toBeInTheDocument();
  });

  it("caps the question at the backend limit", () => {
    renderLearn();

    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveAttribute(
      "maxlength",
      "500",
    );
  });
});
