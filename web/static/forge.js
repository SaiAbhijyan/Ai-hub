/* Live layer: subscribe to the Ledger's SSE stream, prepend new events to the
   Floor feed (when present), and keep the top-bar stats current on any page. */

(function () {
  const pulse = document.getElementById("pulse");
  const feed = document.getElementById("live-feed");
  const maxRows = 80;

  let es;
  function connect() {
    es = new EventSource("/api/stream");
    es.onopen = () => pulse && pulse.classList.remove("off");
    es.onerror = () => pulse && pulse.classList.add("off");
    es.onmessage = (msg) => {
      let ev;
      try { ev = JSON.parse(msg.data); } catch { return; }

      if (pulse) {
        pulse.classList.add("blink");
        setTimeout(() => pulse.classList.remove("blink"), 350);
      }
      if (ev.stats) {
        const t = document.getElementById("stat-tick");
        const n = document.getElementById("stat-events");
        if (t) t.textContent = ev.stats.tick;
        if (n) n.textContent = ev.stats.events;
      }
      if (feed && ev.text) {
        const li = document.createElement("li");
        li.className = "fresh";
        li.innerHTML =
          '<span class="icon"></span><span class="meta"></span><span class="body"></span>';
        li.querySelector(".icon").textContent = ev.icon || "•";
        li.querySelector(".meta").textContent = "#" + ev.id + " · t" + ev.tick;
        li.querySelector(".body").innerHTML = ev.text; // server-rendered, escaped there
        feed.prepend(li);
        while (feed.children.length > maxRows) feed.lastChild.remove();
      }
    };
  }
  connect();
})();
