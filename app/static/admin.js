/* The admin panel's only script: run a live composition and show it as it arrives.

   The compose endpoints stream Server-Sent Events over a POST, which neither a form nor
   htmx's SSE extension can consume — EventSource is GET-only — so this reads the response
   body itself. They stay POST on purpose: a run spends real tokens and writes a row, so it
   belongs behind the same Origin check as every other write.

   Nothing on screen is lost when a run ends well: the meal and its full trace are saved on
   the row, so the panel simply reloads and the result appears in the queue below. Only a
   failure holds the page still, with the reason on it. */

(function () {
  "use strict";

  var RELOAD_NOTE = " Reload the panel to see where things stand.";

  /* One SSE frame's event name and JSON payload; a keep-alive comment carries neither. */
  function parseFrame(text) {
    var event = "message";
    var data = [];
    text.split(/\r?\n/).forEach(function (line) {
      if (line.indexOf("event:") === 0) event = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) data.push(line.slice(5).trim());
    });
    if (data.length === 0) return null;
    try {
      return { event: event, data: JSON.parse(data.join("\n")) };
    } catch (err) {
      return null;
    }
  }

  function node(tag, className, text) {
    var created = document.createElement(tag);
    if (className) created.className = className;
    if (text) created.textContent = text;
    return created;
  }

  /* The output region belonging to one compose form: the run's status line, the trace as
     it streams, and the refusals worth reading. */
  function display(form) {
    var region = document.querySelector(form.dataset.output);
    var status = region.querySelector("[data-compose-status]");
    var log = region.querySelector("[data-compose-log]");
    var notes = region.querySelector("[data-compose-notes]");
    var stop = region.querySelector("[data-compose-stop]");
    return {
      start: function (text) {
        region.hidden = false;
        status.textContent = text;
        log.textContent = "";
        notes.textContent = "";
      },
      /* Offered only while a run is open. Assigned rather than added, so a second run
         replaces the first run's handler instead of stacking on it. */
      running: function (onStop) {
        stop.hidden = false;
        stop.onclick = onStop;
      },
      idle: function () {
        stop.hidden = true;
        stop.onclick = null;
      },
      /* A board run announces each slot; the log from here on belongs to that slot. */
      slot: function (text) {
        status.textContent = text;
        log.textContent = "";
      },
      status: function (text) {
        status.textContent = text;
      },
      step: function (event) {
        var line = node("li");
        line.appendChild(node("span", "badge", event.kind));
        line.appendChild(document.createTextNode(" " + event.text));
        log.appendChild(line);
      },
      note: function (text, className) {
        notes.appendChild(node("p", className, text));
      },
      offer: function (text, label, onAccept) {
        var accept = node("button", "button", label);
        accept.type = "button";
        accept.addEventListener("click", onAccept);
        var box = node("div", "notice notice--caution stack");
        box.appendChild(node("p", null, text));
        box.appendChild(accept);
        notes.appendChild(box);
      }
    };
  }

  /* What each endpoint expects: the curated pool takes a slot, a daily slot adds the date
     and an explicit replace, and a board run takes only the date. */
  function requestFor(form, board, replace) {
    var fields = new FormData(form);
    if (board) return { url: form.dataset.boardUrl, body: { date: fields.get("date") } };
    var body = { meal_type: fields.get("meal_type") };
    if (form.dataset.compose === "daily") {
      body.date = fields.get("date");
      body.replace = replace === true;
    }
    return { url: form.dataset.url, body: body };
  }

  /* A refusal the stream never opened for: a taken daily slot the admin can confirm past,
     or a detail the server already worded for a person. */
  async function refused(form, view, response) {
    var detail = null;
    try {
      detail = (await response.json()).detail;
    } catch (err) {
      /* an unreadable body falls back to the status line below */
    }
    view.status("Nothing was composed.");
    if (detail && detail.conflict) {
      view.offer(detail.message, "Replace it", function () {
        run(form, view, requestFor(form, false, true));
      });
    } else if (response.status === 401 || response.status === 403) {
      view.note("Your admin session has ended. Reload and sign in again.", "notice notice--error");
    } else {
      var message =
        typeof detail === "string"
          ? detail
          : "The composer could not start (error " + response.status + ").";
      view.note(message, "notice notice--error");
    }
  }

  /* Read the stream to its end. A run that saved everything it set out to reloads the
     panel; anything else leaves the page alone so the reason stays readable. */
  async function consume(stream, view) {
    var reader = stream.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var failed = false;
    var finished = false;

    function handle(text) {
      var frame = parseFrame(text);
      if (!frame) return;
      if (frame.event === "trace") {
        view.step(frame.data);
      } else if (frame.event === "slot") {
        view.slot(
          "Composing " + frame.data.meal_type + " (" + frame.data.index + " of " + frame.data.total + ")…"
        );
      } else if (frame.event === "saved" || frame.event === "board") {
        finished = true;
      } else if (frame.event === "slot_error") {
        failed = true;
        view.note(frame.data.meal_type + " failed — " + frame.data.detail, "notice notice--error");
      } else if (frame.event === "error") {
        failed = true;
        view.note(frame.data.detail, "notice notice--error");
      }
    }

    for (;;) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop();
      frames.forEach(handle);
    }
    /* Flush the decoder and parse a frame the close left unterminated, so a trailing
       result is never dropped. */
    buffer += decoder.decode();
    if (buffer.trim()) handle(buffer);

    if (!finished && !failed) {
      failed = true;
      view.note("The stream ended before anything was saved.", "notice notice--error");
    }
    if (failed) view.status("The run stopped early." + RELOAD_NOTE);
    else window.location.reload();
  }

  async function run(form, view, request) {
    var buttons = form.querySelectorAll("button");
    var controller = new AbortController();
    buttons.forEach(function (button) {
      button.disabled = true;
    });
    view.start("Composing…");
    view.running(function () {
      controller.abort();
    });
    try {
      var response = await fetch(request.url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request.body),
        signal: controller.signal
      });
      if (response.ok && response.body) await consume(response.body, view);
      else await refused(form, view, response);
    } catch (err) {
      /* Aborting closes the connection, which the server sees as a client disconnect and
         releases the compose lock on. Anything already saved stays saved. */
      if (controller.signal.aborted) view.status("Stopped." + RELOAD_NOTE);
      else {
        view.status("");
        view.note("The composer could not be reached." + RELOAD_NOTE, "notice notice--error");
      }
    } finally {
      view.idle();
      buttons.forEach(function (button) {
        button.disabled = false;
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-compose]").forEach(function (form) {
      var view = display(form);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var board = Boolean(event.submitter) && event.submitter.name === "board";
        run(form, view, requestFor(form, board, false));
      });
    });
  });
})();
