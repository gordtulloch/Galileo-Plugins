// The one way markup is made: DOM calls, never strings (the page's content-security-policy forbids anything else).
export const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids);
  return node;
};
