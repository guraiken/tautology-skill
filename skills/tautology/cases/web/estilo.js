// passa: CSS dentro de template e o mesmo nome de fonte em duas regras.
const css = `.tag{font:600 11px/16px "Segoe UI",system-ui,sans-serif;color:#fff}
.banner{position:fixed;display:none;
font:500 13px/18px "Segoe UI",system-ui,sans-serif;color:#fff}`;
document.head.appendChild(document.createElement("style")).textContent = css;
