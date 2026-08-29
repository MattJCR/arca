"use strict";

function svgIcon(name, size) {
  const source = document.querySelector('#icon-set [data-icon="' + name + '"]');
  if (!source) {
    return document.createComment(name);
  }
  const icon = source.cloneNode(true);
  icon.removeAttribute("data-icon");
  if (size) {
    icon.setAttribute("width", String(size));
    icon.setAttribute("height", String(size));
  }
  return icon;
}
