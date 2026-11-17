// Progressive enhancement for the flasher page. Everything works without it; the version form then
// shows a submit button and the browser hint stays hidden.

// Changing the version reloads the page for that version.
for (const select of document.querySelectorAll("[data-autosubmit]")) {
  select.addEventListener("change", () => select.form.submit());
}
for (const element of document.querySelectorAll("[data-hide-with-js]")) {
  element.hidden = true;
}

// Flashing needs Web Serial, which only Chromium-based desktop browsers have.
if (!("serial" in navigator)) {
  for (const element of document.querySelectorAll("[data-show-without-serial]")) {
    element.hidden = false;
  }
}
