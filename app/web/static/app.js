/* The only script the app ships, and only for what HTML cannot do on its own:
   send the visitor's chosen provider as request headers, remember that choice, and
   keep a token tally in their browser.

   An API key lives in localStorage and is read at request time, so it travels as a
   header on the call it pays for and is never part of a form body, never stored by
   us, and never logged. Everything here degrades to nothing: with the script off,
   the forms post normally and the server uses its own configured provider. */

(function () {
  "use strict";

  var SETTINGS_KEY = "hf.llm";
  var USAGE_KEY = "hf.usage";

  /* Approximate USD list prices per million tokens, keyed by the provider/model
     string the backend reports. Prices change far more often than the API does, so
     they are a display concern; an unlisted model shows no figure rather than a
     wrong one. Self-hosters can add their own rows. */
  var PRICES = {
    "openai/gpt-5.4-mini": [0.75, 4.5],
    "anthropic/claude-sonnet-4-6": [3, 15],
    "anthropic/claude-haiku-4-5": [1, 5],
    "gemini/gemini-2.5-flash": [0.3, 2.5],
    "gemini/gemini-2.5-pro": [1.25, 10]
  };
  var PRICES_UPDATED = "2026-06-14";
  var SELF_HOSTED = "ollama/";

  function read(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (err) {
      return fallback;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* Private mode or a full store: remembering is a convenience, not a feature. */
    }
  }

  function settings() {
    var stored = read(SETTINGS_KEY, {});
    return {
      provider: stored.provider || "",
      key: stored.key || {},
      model: stored.model || {},
      baseUrl: stored.baseUrl || ""
    };
  }

  /* Where we pay the bill the shared tier is the sensible start; a self-hoster's is
     the Ollama they already run. Either way an explicit choice overrides it. */
  function defaultProvider() {
    return document.body.dataset.publicDeployment === "true" ? "shared" : "ollama";
  }

  function currentProvider() {
    return settings().provider || defaultProvider();
  }

  function llmHeaders() {
    var current = settings();
    var provider = current.provider || defaultProvider();
    var headers = { "X-LLM-Provider": provider };
    /* The shared tier is pinned server-side; anything else we sent would be ignored. */
    if (provider === "shared") return headers;
    if (current.model[provider]) headers["X-LLM-Model"] = current.model[provider];
    if (current.key[provider]) headers["X-LLM-API-Key"] = current.key[provider];
    if (provider === "ollama" && current.baseUrl) headers["X-LLM-Base-URL"] = current.baseUrl;
    return headers;
  }

  function renderSettings() {
    var panel = document.getElementById("ai-settings");
    if (!panel) return;
    var current = settings();
    var provider = currentProvider();

    panel.querySelectorAll("input[name=provider]").forEach(function (radio) {
      radio.checked = radio.value === provider;
    });
    panel.querySelectorAll("[data-llm]").forEach(function (input) {
      var field = input.dataset.llm;
      input.value = field === "baseUrl" ? current.baseUrl : current[field][input.dataset.provider] || "";
    });
    panel.querySelectorAll("[data-fields]").forEach(function (group) {
      group.hidden = group.dataset.fields !== provider;
    });
  }

  function bindSettings() {
    var panel = document.getElementById("ai-settings");
    if (!panel) return;

    panel.querySelectorAll("input[name=provider]").forEach(function (radio) {
      radio.addEventListener("change", function () {
        var next = settings();
        next.provider = radio.value;
        write(SETTINGS_KEY, next);
        renderSettings();
      });
    });
    panel.querySelectorAll("[data-llm]").forEach(function (input) {
      input.addEventListener("input", function () {
        var next = settings();
        if (input.dataset.llm === "baseUrl") next.baseUrl = input.value;
        else next[input.dataset.llm][input.dataset.provider] = input.value;
        write(SETTINGS_KEY, next);
      });
    });
  }

  function usage() {
    var stored = read(USAGE_KEY, {});
    return { models: stored.models || {} };
  }

  /* The page a model call produced carries what it cost. Only a swap can bring one
     in, so a reload cannot count the same call twice. */
  function recordCall() {
    var node = document.getElementById("llm-call");
    if (!node) return;
    var current = usage();
    var totals = current.models[node.dataset.model] || {
      calls: 0,
      input: 0,
      output: 0,
      unreported: 0
    };
    totals.calls += Number(node.dataset.calls);
    totals.input += Number(node.dataset.input);
    totals.output += Number(node.dataset.output);
    totals.unreported += Number(node.dataset.unreported);
    current.models[node.dataset.model] = totals;
    write(USAGE_KEY, current);
  }

  /* Null when the model has no known price, so the panel can show "—" instead of a
     figure it cannot stand behind. */
  function cost(model, totals) {
    if (model.indexOf(SELF_HOSTED) === 0) return 0;
    var price = PRICES[model];
    if (!price) return null;
    return (totals.input * price[0] + totals.output * price[1]) / 1e6;
  }

  /* A provider that reported no usage leaves zeros behind, which would read as free.
     When nothing about a model was reported its figures are unknown, not nought. */
  function reported(totals) {
    return totals.unreported < totals.calls;
  }

  function formatTokens(totals, value) {
    return reported(totals) ? value.toLocaleString() : "—";
  }

  function formatUsd(value) {
    if (value === null) return "—";
    return "$" + value.toFixed(value > 0 && value < 1 ? 4 : 2);
  }

  function cell(row, text) {
    var td = document.createElement("td");
    td.textContent = text;
    row.appendChild(td);
  }

  function renderUsage() {
    var panel = document.getElementById("ai-usage");
    if (!panel) return;
    var models = usage().models;
    var body = panel.querySelector("[data-usage=rows]");
    var calls = 0;
    var tokens = 0;
    var spend = 0;
    var priced = false;

    body.textContent = "";
    Object.keys(models).sort().forEach(function (model) {
      var totals = models[model];
      var estimate = cost(model, totals);
      var row = document.createElement("tr");
      cell(row, model);
      cell(row, String(totals.calls));
      cell(row, formatTokens(totals, totals.input));
      cell(row, formatTokens(totals, totals.output));
      cell(row, reported(totals) ? formatUsd(estimate) : "—");
      body.appendChild(row);

      calls += totals.calls;
      if (reported(totals)) {
        tokens += totals.input + totals.output;
        if (estimate !== null) {
          spend += estimate;
          priced = true;
        }
      }
    });

    panel.querySelector("[data-usage=priced]").textContent = PRICES_UPDATED;
    panel.querySelector("[data-usage=summary]").textContent = calls
      ? calls + " calls · " + tokens.toLocaleString() + " tokens · ≈ " + formatUsd(priced ? spend : null)
      : "nothing yet";
  }

  function bindUsage() {
    var reset = document.querySelector("[data-usage=reset]");
    if (!reset) return;
    reset.addEventListener("click", function () {
      write(USAGE_KEY, { models: {} });
      renderUsage();
    });
  }

  /* The ingredient editor. The rows already work without any of this — each one edits,
     clearing a name drops it, and the trailing blank row adds one — so everything here
     is the convenience layer: the remove buttons, an add button, and a live count.
     Both controls are markup-hidden until now, so a visitor without the script is never
     shown a button that would do nothing. */

  function ingredientRows(editor) {
    return Array.prototype.slice.call(editor.querySelectorAll(".ingredients__row"));
  }

  /* The count reports what would actually be submitted, so a trailing blank row does
     not read as an ingredient the person has not typed yet. */
  function renderIngredients(editor) {
    var rows = ingredientRows(editor);
    var max = Number(editor.dataset.max);
    var filled = rows.filter(function (row) {
      return row.querySelector("input").value.trim() !== "";
    }).length;

    editor.querySelector("[data-ingredients-count]").textContent = filled + " of " + max;
    rows.forEach(function (row, index) {
      var remove = row.querySelector("[data-ingredients-remove]");
      remove.hidden = false;
      remove.setAttribute("aria-label", "Remove ingredient " + (index + 1));
      row.querySelector("input").setAttribute("aria-label", "Ingredient " + (index + 1));
    });

    var add = editor.querySelector("[data-ingredients-add]");
    add.hidden = false;
    add.disabled = rows.length >= max;
  }

  /* Cloned from a row already on the page rather than built here, so the row's markup
     has exactly one definition and it is the one the server rendered. */
  function addIngredientRow(editor) {
    var rows = ingredientRows(editor);
    if (rows.length >= Number(editor.dataset.max)) return;
    var row = rows[rows.length - 1].cloneNode(true);
    var input = row.querySelector("input");
    input.value = "";
    rows[rows.length - 1].parentNode.appendChild(row);
    renderIngredients(editor);
    input.focus();
  }

  function removeIngredientRow(editor, row) {
    /* Never leave the editor with nothing to type into: the last row empties instead
       of vanishing, which is also what clearing it by hand would have done. */
    if (ingredientRows(editor).length === 1) row.querySelector("input").value = "";
    else row.remove();
    renderIngredients(editor);
  }

  function bindIngredients() {
    document.querySelectorAll("[data-ingredients]").forEach(function (editor) {
      if (editor.dataset.bound) return;
      editor.dataset.bound = "true";

      editor.addEventListener("click", function (event) {
        var remove = event.target.closest("[data-ingredients-remove]");
        if (remove) removeIngredientRow(editor, remove.closest(".ingredients__row"));
        else if (event.target.closest("[data-ingredients-add]")) addIngredientRow(editor);
      });
      editor.addEventListener("input", function () {
        renderIngredients(editor);
      });
      renderIngredients(editor);
    });
  }

  function start() {
    bindSettings();
    bindUsage();
    bindIngredients();
    renderSettings();
    renderUsage();
  }

  document.addEventListener("DOMContentLoaded", function () {
    /* hx-boost replaces the body's contents, so the listeners live on the body
       itself and the panels are re-bound against the markup each swap brings in. */
    document.body.addEventListener("htmx:configRequest", function (event) {
      /* Only the writes spend a model call; a boosted link is a plain page read and
         has no business carrying somebody's API key. */
      if (event.detail.verb !== "get") Object.assign(event.detail.headers, llmHeaders());
    });
    document.body.addEventListener("htmx:afterSwap", function () {
      recordCall();
      start();
    });
    start();
  });
})();
